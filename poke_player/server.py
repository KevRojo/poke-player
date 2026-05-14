"""
Pokemon Agent — FastAPI Game Server

Provides HTTP + WebSocket API for controlling a Game Boy / GBA emulator
running a Pokemon ROM, reading game state, and broadcasting events.
"""

import asyncio
import base64
import io
import json
import os
import re
import time
from functools import partial
from pathlib import Path
from typing import Optional, Set

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from poke_player.logging_config import get_logger, get_metrics, log_context, setup_logging

__version__ = "0.2.0"
logger = get_logger("poke_player.server")
metrics = get_metrics()

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class GameConfig(BaseModel):
    """Server configuration — set before startup."""
    rom_path: str
    game_type: str = "auto"       # "red", "firered", or "auto"
    port: int = 8765
    data_dir: str = "~/.poke-player"
    load_state: Optional[str] = None  # Save-state name to auto-load on startup


class ActionRequest(BaseModel):
    """Body for POST /action."""
    actions: list[str]


class SaveRequest(BaseModel):
    """Body for POST /save and POST /load."""
    name: str


# ---------------------------------------------------------------------------
# Global state
# ---------------------------------------------------------------------------

_config: Optional[GameConfig] = None
_emulator = None          # Emulator instance
_reader = None            # GameMemoryReader subclass instance
_start_time: float = 0.0
_loop: Optional[asyncio.AbstractEventLoop] = None
_emu_lock: Optional[asyncio.Lock] = None  # serialize all emulator access
_auto_save = None         # AutoSaveManager instance

# WebSocket clients
_ws_clients: Set[WebSocket] = set()

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Pokemon Agent Server",
    version=__version__,
    description="HTTP + WebSocket API for Pokemon emulator control",
)

# CORS — allow everything for local dev
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _detect_game_type(rom_path: str) -> str:
    """Detecta el tipo de juego leyendo el header de la ROM."""
    from poke_player.rom_validator import detect_game_type
    return detect_game_type(rom_path)


def _ensure_emulator():
    """Raise 503 if the emulator isn't ready."""
    if _emulator is None:
        raise HTTPException(status_code=503, detail="Emulator not initialised")


async def _run_sync(func, *args):
    """Run a blocking emulator call in the default executor, serialised
    by ``_emu_lock`` so the background frame ticker and request handlers
    never reach into PyBoy at the same time (PyBoy is not thread-safe —
    concurrent ticks cause hangs and sound-buffer corruption)."""
    loop = asyncio.get_running_loop()
    if _emu_lock is None:
        return await loop.run_in_executor(None, partial(func, *args))
    async with _emu_lock:
        return await loop.run_in_executor(None, partial(func, *args))


async def broadcast(event: dict):
    """Send a JSON event to every connected WebSocket client.

    Each send is wrapped in a 1s timeout so a half-open / stalled client
    can't block the /action handler that called us.
    """
    dead: list[WebSocket] = []
    payload = json.dumps(event)
    for ws in _ws_clients:
        try:
            await asyncio.wait_for(ws.send_text(payload), timeout=1.0)
        except Exception:
            dead.append(ws)
    for ws in dead:
        _ws_clients.discard(ws)


def _get_state_dict() -> dict:
    """Build full game state from the memory reader."""
    from poke_player.state.builder import build_game_state
    return build_game_state(_reader)


def _get_screenshot_bytes() -> bytes:
    """Grab the current frame as PNG bytes."""
    screen = _emulator.get_screen()          # PIL Image or numpy array
    buf = io.BytesIO()
    # If it's a numpy array, convert to PIL first
    try:
        from PIL import Image
        if not isinstance(screen, Image.Image):
            import numpy as np
            screen = Image.fromarray(screen)
        screen.save(buf, format="PNG")
    except ImportError:
        # Fallback: assume screen already has save()
        screen.save(buf, format="PNG")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Action parser
# ---------------------------------------------------------------------------

_ACTION_RE = re.compile(
    r"^(?P<kind>press|walk|hold|wait|a_until_dialog_end)(?:_(?P<rest>.+))?$"
)


async def _execute_action(action_str: str) -> None:
    """Parse and execute a single action string on the emulator.

    Supported formats:
        press_X       — press button X for 10 frames, wait 20 frames
        walk_X        — press direction for 16 frames, wait 8 frames
        hold_X_N      — hold button X for N frames
        wait_N        — tick N frames with no input
        a_until_dialog_end — press A every 30 frames until dialog clears (max 300)
    """
    action_str = action_str.strip().lower()

    if action_str == "a_until_dialog_end":
        for _ in range(10):  # max 300 frames = 10 * 30
            await _run_sync(_emulator.press, "a")
            await _run_sync(_emulator.tick, 30)
            # Check dialog flag via reader if available
            try:
                state = _get_state_dict()
                if not state.get("dialog_active", False):
                    break
            except Exception:
                pass
        return

    # Split into tokens
    parts = action_str.split("_")

    if parts[0] == "press" and len(parts) >= 2:
        button = "_".join(parts[1:])
        # Hold button for 8 frames so the game registers the press,
        # then wait 12 frames for the game to process it.
        await _run_sync(_emulator.press, button, 8)
        await _run_sync(_emulator.tick, 12)
        return

    if parts[0] == "walk" and len(parts) >= 2:
        direction = parts[1]
        # Gen 1 movement timing (empirically tested):
        #   - Button must be held >= 4 frames for the game's vblank joypad
        #     poll to register the input reliably.
        #   - wWalkCounter starts at 8, decrements each frame (2 px/frame
        #     = 16 px = 1 tile). Total walk animation = ~16 frames.
        #   - Minimum total frames for a confirmed tile move = 17.
        #   - We use hold=8 + wait=12 = 20 total for a safety margin.
        await _run_sync(_emulator.press, direction, 8)
        await _run_sync(_emulator.tick, 12)
        return

    if parts[0] == "hold" and len(parts) >= 3:
        button = "_".join(parts[1:-1])
        frames = int(parts[-1])
        await _run_sync(_emulator.press, button, frames)
        return

    if parts[0] == "wait" and len(parts) == 2:
        frames = int(parts[1])
        await _run_sync(_emulator.tick, frames)
        return

    raise ValueError(f"Unknown action format: {action_str}")


# ---------------------------------------------------------------------------
# Server lifecycle
# ---------------------------------------------------------------------------

def configure(config: GameConfig):
    """Set server configuration (call before app startup)."""
    global _config
    _config = config


@app.on_event("startup")
async def _startup():
    global _emulator, _reader, _start_time, _config, _loop, _emu_lock

    # Configurar logging al inicio
    setup_logging(
        level=os.environ.get("POKE_LOG_LEVEL", "INFO"),
        json_format=os.environ.get("POKE_LOG_JSON", "").lower() == "true",
    )

    _loop = asyncio.get_running_loop()
    _emu_lock = asyncio.Lock()
    _start_time = time.time()

    if _config is None:
        logger.warning("No GameConfig set — emulator will NOT start.")
        logger.info("Call server.configure(GameConfig(...)) before startup.")
        return

    rom = Path(_config.rom_path).expanduser().resolve()
    if not rom.exists():
        logger.error(f"ROM not found: {rom}")
        return

    # Auto-detect game type
    game_type = _config.game_type
    if game_type == "auto":
        game_type = _detect_game_type(str(rom))

    logger.info(f"Loading ROM: {rom}", extra={"rom": str(rom), "game_type": game_type})
    logger.info(f"Detected game type: {game_type}")

    # Create emulator
    from poke_player.emulator import create_emulator
    _emulator = create_emulator(str(rom))

    # Create memory reader
    if game_type == "red":
        from poke_player.memory.red import PokemonRedReader
        _reader = PokemonRedReader(_emulator)
    elif game_type == "firered":
        from poke_player.memory.firered import PokemonFireRedReader
        _reader = PokemonFireRedReader(_emulator)
    else:
        raise ValueError(f"Unknown game type: {game_type}")

    # Create data directories
    data_dir = Path(_config.data_dir).expanduser().resolve()
    (data_dir / "saves").mkdir(parents=True, exist_ok=True)

    # Background frame ticker — without this the emulator sits at frame 0
    # between actions, so the dashboard / screenshot endpoint always shows
    # the same idle frame. We tick at ~60 FPS in a separate asyncio task.
    async def _frame_loop():
        import asyncio as _aio
        while True:
            try:
                if _emulator is not None:
                    # Go through _run_sync so we acquire _emu_lock — otherwise
                    # this races with /action handlers in another executor
                    # thread and pyboy hangs.
                    await _run_sync(_emulator.tick, 4)
            except Exception:
                pass
            # 4 frames @ ~60Hz target ≈ 67ms cycle
            await _aio.sleep(0.066)
    asyncio.create_task(_frame_loop())

    # Try mounting dashboard
    try:
        import poke_player.dashboard as dashboard_mod  # noqa: F401
        from fastapi.staticfiles import StaticFiles
        dash_dir = Path(dashboard_mod.__file__).parent / "static"
        if dash_dir.is_dir():
            app.mount("/dashboard", StaticFiles(directory=str(dash_dir), html=True), name="dashboard")
            logger.info("Dashboard mounted at /dashboard")
        else:
            logger.warning("Dashboard module found but no static/ directory")
    except ImportError:
        logger.warning("Dashboard not installed — /dashboard unavailable")
        logger.info("Install with: pip install poke-player[dashboard]")

    # Auto-load a save state if specified
    if _config.load_state:
        saves_dir = data_dir / "saves"
        state_path = saves_dir / f"{_config.load_state}.state"
        if state_path.exists():
            try:
                _emulator.load_state(str(state_path))
                logger.info(f"Loaded save state: {_config.load_state}")
            except Exception as e:
                logger.warning(f"Failed to load state '{_config.load_state}': {e}")
        else:
            logger.warning(f"Save state not found: {state_path}")

    # Initialize auto-save manager
    global _auto_save
    try:
        from poke_player.auto_save import AutoSaveManager, AutoSaveConfig
        auto_save_config = AutoSaveConfig(
            enabled=os.environ.get("POKE_AUTOSAVE", "true").lower() == "true",
            interval_seconds=int(os.environ.get("POKE_AUTOSAVE_INTERVAL", "300")),
            save_before_battle=True,
            save_on_new_map=True,
            max_auto_saves=10,
        )
        _auto_save = AutoSaveManager(_emulator, auto_save_config, saves_dir=data_dir / "saves")
        _auto_save.start()
    except Exception as e:
        logger.warning(f"Auto-save initialization failed: {e}")

    logger.info(f"Ready — listening on port {_config.port}", extra={"port": _config.port})
    logger.info("Endpoints available: /, /state, /screenshot, /action, /save, /load, /saves, /minimap, /health, /ws")


@app.on_event("shutdown")
async def _shutdown():
    """Clean up on server shutdown."""
    global _auto_save
    if _auto_save:
        _auto_save.stop()
        logger.info("Auto-save manager stopped")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/")
async def index():
    """Server info."""
    return {
        "name": "poke-player",
        "version": __version__,
        "game": _config.game_type if _config else None,
        "rom": _config.rom_path if _config else None,
        "uptime_seconds": round(time.time() - _start_time, 1) if _start_time else 0,
        "emulator_ready": _emulator is not None,
    }


@app.get("/health")
async def health():
    """Health check."""
    return {"status": "ok", "emulator_ready": _emulator is not None}


@app.get("/rom/validate")
async def validate_rom_endpoint(rom_path: str):
    """Valida una ROM y retorna información detallada."""
    from poke_player.rom_validator import validate_rom
    try:
        info = validate_rom(rom_path)
        return {
            "valid": info.is_valid,
            "game": info.game_title.value,
            "platform": info.platform.name,
            "header_title": info.header_title,
            "header_code": info.header_code,
            "size": info.size,
            "game_type": info.game_type,
            "header_checksum_valid": info.header_checksum_valid,
            "global_checksum_valid": info.global_checksum_valid,
            "errors": info.errors,
            "warnings": info.warnings,
        }
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/state")
async def get_state():
    """Full game state JSON."""
    _ensure_emulator()
    try:
        with metrics.timer("state_read"):
            state = await _run_sync(_get_state_dict)
        return JSONResponse(content=state)
    except Exception as e:
        logger.error(f"Error reading state: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error reading state: {e}")


@app.get("/screenshot")
async def screenshot():
    """Current emulator frame as PNG image."""
    _ensure_emulator()
    try:
        with metrics.timer("screenshot"):
            png_bytes = await _run_sync(_get_screenshot_bytes)
        metrics.increment("screenshots_total")
        return Response(content=png_bytes, media_type="image/png")
    except Exception as e:
        logger.error(f"Screenshot error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Screenshot error: {e}")


@app.get("/screenshot/base64")
async def screenshot_base64():
    """Current emulator frame as base64-encoded PNG in JSON."""
    _ensure_emulator()
    try:
        png_bytes = await _run_sync(_get_screenshot_bytes)
        b64 = base64.b64encode(png_bytes).decode("ascii")
        return {"image": b64, "format": "png"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Screenshot error: {e}")


@app.post("/action")
async def execute_actions(req: ActionRequest):
    """Execute a sequence of game actions."""
    _ensure_emulator()
    logger.info(f"Executing actions: {req.actions}", extra={"actions": req.actions})
    metrics.increment("actions_total", len(req.actions))

    try:
        with metrics.timer("action_execution"):
            executed = 0
            for idx, action_str in enumerate(req.actions):
                if idx > 0:
                    # Forced cooldown between actions — without it, pyboy's
                    # sound buffer / internal state can corrupt under back-to-back
                    # presses and crash the interpreter.
                    await asyncio.sleep(5.0)
                await _execute_action(action_str)
                executed += 1

            state_after = await _run_sync(_get_state_dict)

        # Grab a screenshot for the live dashboard
        try:
            png_bytes = await _run_sync(_get_screenshot_bytes)
            screenshot_b64 = base64.b64encode(png_bytes).decode("ascii")
        except Exception:
            screenshot_b64 = None

        # Broadcast to WebSocket clients
        await broadcast({
            "type": "action",
            "actions": req.actions,
            "actions_executed": executed,
            "state_after": state_after,
        })
        # Also push the latest frame so the dashboard updates immediately
        if screenshot_b64:
            await broadcast({
                "type": "screenshot",
                "data": {"image": screenshot_b64, "format": "png"},
            })

        logger.info(f"Actions completed: {executed}/{len(req.actions)}")
        return {
            "success": True,
            "actions_executed": executed,
            "state_after": state_after,
        }
    except ValueError as e:
        logger.warning(f"Action validation error: {e}", extra={"actions": req.actions})
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Action execution error: {e}", extra={"actions": req.actions}, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Action error: {e}")


@app.post("/save")
async def save_state(req: SaveRequest):
    """Save emulator state to disk."""
    _ensure_emulator()
    if not _config:
        raise HTTPException(status_code=503, detail="Server not configured")
    try:
        saves_dir = Path(_config.data_dir).expanduser().resolve() / "saves"
        saves_dir.mkdir(parents=True, exist_ok=True)
        save_path = saves_dir / f"{req.name}.state"
        await _run_sync(_emulator.save_state, str(save_path))
        return {"success": True, "path": str(save_path)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Save error: {e}")


@app.post("/load")
async def load_state(req: SaveRequest):
    """Load emulator state from disk."""
    _ensure_emulator()
    if not _config:
        raise HTTPException(status_code=503, detail="Server not configured")
    try:
        saves_dir = Path(_config.data_dir).expanduser().resolve() / "saves"
        save_path = saves_dir / f"{req.name}.state"
        if not save_path.exists():
            raise HTTPException(status_code=404, detail=f"Save not found: {req.name}")
        await _run_sync(_emulator.load_state, str(save_path))
        state_after = await _run_sync(_get_state_dict)

        await broadcast({"type": "state_update", "reason": "load", "state": state_after})

        return {"success": True, "name": req.name, "state_after": state_after}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Load error: {e}")


@app.get("/saves")
async def list_saves():
    """List available save-state files."""
    if not _config:
        raise HTTPException(status_code=503, detail="Server not configured")
    try:
        saves_dir = Path(_config.data_dir).expanduser().resolve() / "saves"
        if not saves_dir.exists():
            return {"saves": []}
        files = sorted(saves_dir.glob("*.state"))
        saves = [
            {
                "name": f.stem,
                "file": f.name,
                "size_bytes": f.stat().st_size,
                "modified": f.stat().st_mtime,
            }
            for f in files
        ]
        return {"saves": saves}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error listing saves: {e}")


@app.get("/minimap")
async def minimap():
    """Simple ASCII minimap — current map name + player position."""
    _ensure_emulator()
    try:
        state = await _run_sync(_get_state_dict)
        map_info = state.get("map", {})
        player = state.get("player", {})
        map_name = map_info.get("map_name", "Unknown")
        pos = player.get("position", {})
        x = pos.get("x", "?")
        y = pos.get("y", "?")

        lines = [
            f"=== {map_name} ===",
            f"Player position: ({x}, {y})",
            "",
            "  N",
            "W + E",
            "  S",
        ]
        text = "\n".join(lines)
        return Response(content=text, media_type="text/plain")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Minimap error: {e}")


@app.get("/autosave/status")
async def autosave_status():
    """Get auto-save manager status."""
    if _auto_save is None:
        return {"enabled": False, "reason": "not_initialized"}
    return {
        "enabled": _auto_save.config.enabled,
        "last_save_time": _auto_save._last_save_time,
        "interval_seconds": _auto_save.config.interval_seconds,
        "max_auto_saves": _auto_save.config.max_auto_saves,
        "save_before_battle": _auto_save.config.save_before_battle,
        "save_on_new_map": _auto_save.config.save_on_new_map,
    }


@app.post("/autosave/trigger")
async def autosave_trigger():
    """Manually trigger an auto-save."""
    if _auto_save is None:
        raise HTTPException(status_code=503, detail="Auto-save not initialized")
    try:
        await _auto_save._do_save("manual")
        return {"success": True, "message": "Auto-save triggered"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Auto-save failed: {e}")


# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    """Live event stream via WebSocket."""
    await ws.accept()
    _ws_clients.add(ws)
    try:
        # Send a welcome message
        await ws.send_json({
            "type": "connected",
            "version": __version__,
            "emulator_ready": _emulator is not None,
        })
        # Keep alive — wait for client messages (or disconnect)
        while True:
            data = await ws.receive_text()
            # Clients can send a "ping" to keep alive
            if data.strip().lower() == "ping":
                await ws.send_json({"type": "pong"})
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        _ws_clients.discard(ws)


# ---------------------------------------------------------------------------
# Dashboard fallback — only registered if dashboard static files are missing
# ---------------------------------------------------------------------------

def _register_dashboard_fallback():
    """Register a fallback route for /dashboard if static files aren't available."""
    try:
        import poke_player.dashboard as _dm
        static_dir = Path(_dm.__file__).parent / "static"
        if static_dir.is_dir() and (static_dir / "index.html").exists():
            return  # Dashboard exists — don't register fallback
    except ImportError:
        pass

    @app.get("/dashboard")
    @app.get("/dashboard/{path:path}")
    async def dashboard_fallback(path: str = ""):
        raise HTTPException(
            status_code=404,
            detail="Dashboard not installed. Install with: pip install poke-player[dashboard]",
        )

_register_dashboard_fallback()
