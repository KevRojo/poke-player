"""
poke-player — CLI for the Pokemon AI gameplay server.

Usage:
    poke-player serve --rom <path>                Start the game server
    poke-player info  --rom <path>                Show ROM info
    poke-player state                             Get compact game state
    poke-player action <button(s)>                Press button(s) e.g. 'a' or 'up,up,a'
    poke-player save   <name>                     Save state to named slot
    poke-player load   <name>                     Load state from named slot
    poke-player saves                             List available save slots
    poke-player screenshot [--out <path>]         Grab a PNG of the current frame
    poke-player minimap                           Get the explored-area minimap
    poke-player stop                              Shutdown the running server
    poke-player --version

All non-serve commands hit the running server (default http://127.0.0.1:8765).
Use --url to override.
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

__version__ = "0.1.2"

BANNER = r"""
  ____            _              ____  _
 |  _ \ ___ | | _____  |  _ \| | __ _ _   _ ___ _ __
 | |_) / _ \| |/ / _ \ | |_) | |/ _` | | | / _ \ '__|
 |  __/ (_) |   <  __/ |  __/| | (_| | |_| /  __/ |
 |_|   \___/|_|\_\___| |_|   |_|\__,_|\__, \___|_|
                                       |___/  v{version}
"""

DEFAULT_URL = "http://127.0.0.1:8765"


def _detect_game_type(rom_path: str) -> str:
    ext = Path(rom_path).suffix.lower()
    if ext in (".gb", ".gbc"):
        return "red"
    elif ext == ".gba":
        return "firered"
    return "unknown"


# ── Server-side commands ─────────────────────────────────────────────────

def cmd_serve(args):
    rom = Path(args.rom).expanduser().resolve()
    if not rom.exists():
        print(f"ERROR: ROM file not found: {rom}", file=sys.stderr)
        sys.exit(1)

    data_dir = Path(args.data_dir).expanduser().resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "saves").mkdir(exist_ok=True)

    game_type = _detect_game_type(str(rom))

    print(BANNER.format(version=__version__))
    print(f"  ROM:       {rom}")
    print(f"  Game type: {game_type}")
    print(f"  Port:      {args.port}")
    print(f"  Data dir:  {data_dir}")
    print()

    from poke_player.server import GameConfig, configure, app  # noqa: F401

    configure(GameConfig(
        rom_path=str(rom),
        game_type=game_type,
        port=args.port,
        data_dir=str(data_dir),
        load_state=getattr(args, 'load_state', None),
    ))

    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=args.port, log_level="info")


def cmd_info(args):
    rom = Path(args.rom).expanduser().resolve()
    if not rom.exists():
        print(f"ERROR: ROM file not found: {rom}", file=sys.stderr)
        sys.exit(1)
    size = rom.stat().st_size
    sha = hashlib.sha256()
    with open(rom, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha.update(chunk)
    print(f"ROM path:    {rom}")
    print(f"File size:   {size:,} bytes ({size / 1024 / 1024:.2f} MB)")
    print(f"SHA-256:     {sha.hexdigest()}")
    print(f"Extension:   {rom.suffix}")
    print(f"Detected as: {_detect_game_type(str(rom))}")


# ── Client commands (talk to running server) ─────────────────────────────

def _http_get(url: str, path: str, timeout: float = 5.0):
    import requests
    return requests.get(f"{url}{path}", timeout=timeout)


def _http_post(url: str, path: str, payload: dict | None = None, timeout: float = 60.0):
    import requests
    return requests.post(f"{url}{path}", json=payload or {}, timeout=timeout)


def _print_json(data) -> None:
    print(json.dumps(data, indent=2, default=str, ensure_ascii=False))


def _bail_on_error(label: str, e: Exception) -> None:
    print(f"ERROR ({label}): {e}", file=sys.stderr)
    sys.exit(2)


def cmd_state(args):
    try:
        r = _http_get(args.url, "/state")
        s = r.json()
    except Exception as e:
        _bail_on_error("state", e); return
    if args.verbose:
        _print_json(s); return
    p = s.get("player") or {}
    pos = p.get("position") or {}
    m = s.get("map") or {}
    party = s.get("party") or []
    party_compact = [
        f"{(pk.get('name') or pk.get('species') or '?')}({pk.get('hp_pct') or pk.get('hp') or '?'})"
        for pk in party[:6]
    ]
    _print_json({
        "game": (s.get("metadata") or {}).get("game"),
        "map": m.get("map_name"),
        "pos": f"({pos.get('x')},{pos.get('y')}) {p.get('facing')}",
        "player": p.get("name"),
        "money": p.get("money"),
        "badges": p.get("badge_count") or (s.get("flags") or {}).get("badge_count"),
        "party": party_compact,
        "bag_items": len(s.get("bag") or []),
        "dialog": (s.get("dialog") or {}).get("active", False),
        "battle": (s.get("battle") or {}).get("in_battle", False),
    })


def cmd_action(args):
    raw = args.buttons
    if not raw:
        print("ERROR: pass at least one button (e.g. 'a' or 'up,up,a')", file=sys.stderr)
        sys.exit(2)
    DIRECTIONS = {"up", "down", "left", "right"}
    NATIVE = ("press_", "walk_", "hold_", "wait_")
    actions = []
    for tok in [t.strip() for t in raw.split(",") if t.strip()]:
        low = tok.lower()
        if low.startswith(NATIVE) or low == "a_until_dialog_end":
            actions.append(low)
        elif low in DIRECTIONS:
            actions.append(f"walk_{low}")
        else:
            actions.append(f"press_{low}")
    try:
        r = _http_post(args.url, "/action", {"actions": actions})
        _print_json(r.json())
    except Exception as e:
        _bail_on_error("action", e)


def cmd_save(args):
    try:
        _print_json(_http_post(args.url, "/save", {"name": args.name}).json())
    except Exception as e:
        _bail_on_error("save", e)


def cmd_load(args):
    try:
        _print_json(_http_post(args.url, "/load", {"name": args.name}).json())
    except Exception as e:
        _bail_on_error("load", e)


def cmd_saves(args):
    try:
        _print_json(_http_get(args.url, "/saves").json())
    except Exception as e:
        _bail_on_error("saves", e)


def cmd_screenshot(args):
    try:
        r = _http_get(args.url, "/screenshot")
        target = Path(args.out).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(r.content)
        _print_json({"saved_to": str(target), "bytes": len(r.content)})
    except Exception as e:
        _bail_on_error("screenshot", e)


def cmd_minimap(args):
    try:
        _print_json(_http_get(args.url, "/minimap").json())
    except Exception as e:
        _bail_on_error("minimap", e)


def cmd_stop(args):
    try:
        r = _http_post(args.url, "/shutdown")
        _print_json(r.json() if r.headers.get("content-type", "").startswith("application/json") else {"status": r.status_code})
    except Exception as e:
        # /shutdown may not exist on all server versions — fall back to OS-kill hint
        print(f"NOTE: HTTP shutdown failed ({e}). The server exposes no /shutdown route on this version.", file=sys.stderr)
        print("Kill the `poke-player serve` process directly to stop it.", file=sys.stderr)
        sys.exit(2)


# ── Entry point ──────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        prog="poke-player",
        description="poke-player — AI-powered Pokemon game controller with REST API and dashboard.",
    )
    parser.add_argument("--version", action="version", version=f"poke-player {__version__}")
    parser.add_argument("--url", default=DEFAULT_URL,
                        help=f"Server URL for client commands (default: {DEFAULT_URL})")

    sub = parser.add_subparsers(dest="command")

    # --- serve ---
    p = sub.add_parser("serve", help="Start the game server.")
    p.add_argument("--rom", required=True, help="Path to .gb/.gbc/.gba ROM")
    p.add_argument("--port", type=int, default=8765, help="Server port (default: 8765)")
    p.add_argument("--data-dir", default="~/.poke-player", help="Save-state + dashboard dir")
    p.add_argument("--no-dashboard", action="store_true", help="Disable dashboard mounting")
    p.add_argument("--load-state", default=None, help="Named save slot to auto-load at boot")

    # --- info ---
    p = sub.add_parser("info", help="Show ROM info (size, SHA-256, detected type).")
    p.add_argument("--rom", required=True, help="Path to ROM file")

    # --- state ---
    p = sub.add_parser("state", help="Print a compact summary of the current game state.")
    p.add_argument("--verbose", "-v", action="store_true", help="Return the full raw state JSON")

    # --- action ---
    p = sub.add_parser("action", help="Press button(s). Friendly shortcuts ('a','up,up,a') or native verbs ('press_a','walk_up','wait_60','a_until_dialog_end').")
    p.add_argument("buttons", help="Button or comma-separated sequence")

    # --- save ---
    p = sub.add_parser("save", help="Save the current game state to a named slot.")
    p.add_argument("name", help="Slot name")

    # --- load ---
    p = sub.add_parser("load", help="Load a previously-saved game state.")
    p.add_argument("name", help="Slot name")

    # --- saves ---
    sub.add_parser("saves", help="List all save-state slots.")

    # --- screenshot ---
    p = sub.add_parser("screenshot", help="Save a PNG of the current emulator frame.")
    p.add_argument("--out", default=str(Path.home() / ".poke-player" / "last.png"),
                   help="Output PNG path (default: ~/.poke-player/last.png)")

    # --- minimap ---
    sub.add_parser("minimap", help="Get the explored-area minimap data.")

    # --- stop ---
    sub.add_parser("stop", help="Request the running server to shut down (if /shutdown route exists).")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(0)

    handler = {
        "serve": cmd_serve, "info": cmd_info,
        "state": cmd_state, "action": cmd_action,
        "save": cmd_save, "load": cmd_load, "saves": cmd_saves,
        "screenshot": cmd_screenshot, "minimap": cmd_minimap,
        "stop": cmd_stop,
    }[args.command]
    handler(args)


if __name__ == "__main__":
    main()
