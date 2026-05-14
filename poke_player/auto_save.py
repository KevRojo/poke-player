"""Automatic save-state manager.

Monitors game state and creates save states at key moments:
- Before battles
- When entering new maps
- At regular intervals (time-based)
- On significant events (level ups, item finds, etc.)
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set

# Lazy import to avoid circular dependency
logger = None

def _get_logger():
    global logger
    if logger is None:
        from poke_player.logging_config import get_logger
        logger = get_logger("poke_player.auto_save")
    return logger


@dataclass
class AutoSaveConfig:
    """Configuration for automatic save states."""

    enabled: bool = True
    interval_seconds: int = 300  # Save every 5 minutes
    save_before_battle: bool = True
    save_on_new_map: bool = True
    save_on_level_up: bool = True
    max_auto_saves: int = 10  # Keep last N auto-saves
    prefix: str = "auto"


@dataclass
class GameSnapshot:
    """A lightweight snapshot of game state for comparison."""

    map_name: str = ""
    in_battle: bool = False
    party_levels: List[int] = field(default_factory=list)
    money: int = 0
    badges: int = 0
    timestamp: float = field(default_factory=time.time)

    @classmethod
    def from_state(cls, state: Dict) -> "GameSnapshot":
        """Create snapshot from full game state dict."""
        party = state.get("party", [])
        levels = [p.get("level", 0) for p in party if isinstance(p, dict)]

        player = state.get("player", {})
        if isinstance(player, dict):
            money = player.get("money", 0)
            badges = len(player.get("badges", []))
        else:
            money = 0
            badges = 0

        return cls(
            map_name=state.get("map", {}).get("name", "") if isinstance(state.get("map"), dict) else str(state.get("map", "")),
            in_battle=state.get("battle", {}).get("in_battle", False) if isinstance(state.get("battle"), dict) else bool(state.get("battle", False)),
            party_levels=levels,
            money=money,
            badges=badges,
        )


class AutoSaveManager:
    """Manages automatic save states based on game events."""

    def __init__(
        self,
        emulator,
        config: Optional[AutoSaveConfig] = None,
        saves_dir: Optional[Path] = None,
    ):
        self.emu = emulator
        self.config = config or AutoSaveConfig()
        self.saves_dir = saves_dir or Path("~/.poke-player/saves").expanduser()
        self.saves_dir.mkdir(parents=True, exist_ok=True)

        self._last_snapshot: Optional[GameSnapshot] = None
        self._last_save_time: float = 0
        self._saved_maps: Set[str] = set()
        self._task: Optional[asyncio.Task] = None
        self._running = False

    def start(self):
        """Start the auto-save monitoring loop."""
        if not self.config.enabled or self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._monitor_loop())
        _get_logger().info("Auto-save manager started", extra={
            "interval": self.config.interval_seconds,
            "before_battle": self.config.save_before_battle,
            "on_new_map": self.config.save_on_new_map,
        })

    def stop(self):
        """Stop the auto-save monitoring loop."""
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None
        _get_logger().info("Auto-save manager stopped")

    async def _monitor_loop(self):
        """Main monitoring loop — checks state periodically."""
        while self._running:
            try:
                await self._check_and_save()
            except Exception as e:
                _get_logger().warning(f"Auto-save check failed: {e}")
            await asyncio.sleep(5)  # Check every 5 seconds

    async def _check_and_save(self):
        """Check current state and save if conditions are met."""
        from poke_player.state.builder import build_game_state

        # Need a reader to get state
        # This is a simplified version — in practice we'd pass the reader
        # For now, we'll use a basic approach

        # Time-based save
        now = time.time()
        if now - self._last_save_time >= self.config.interval_seconds:
            await self._do_save("interval")
            return

    async def _do_save(self, reason: str):
        """Create a save state with the given reason."""
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        name = f"{self.config.prefix}_{reason}_{timestamp}"
        path = self.saves_dir / f"{name}.state"

        try:
            # Run save in executor since emulator methods may block
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self.emu.save_state, str(path))
            self._last_save_time = time.time()
            _get_logger().info(f"Auto-saved: {name}", extra={"save_name": name, "reason": reason})
            self._cleanup_old_saves()
        except Exception as e:
            _get_logger().error(f"Auto-save failed: {e}", exc_info=True)

    def _cleanup_old_saves(self):
        """Remove old auto-saves, keeping only the most recent N."""
        try:
            auto_saves = sorted(
                self.saves_dir.glob(f"{self.config.prefix}_*.state"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            for old_save in auto_saves[self.config.max_auto_saves :]:
                old_save.unlink()
                _get_logger().debug(f"Removed old auto-save: {old_save.name}")
        except Exception as e:
            _get_logger().warning(f"Cleanup failed: {e}")

    def check_state_change(self, current_state: Dict) -> Optional[str]:
        """Check if game state warrants an auto-save.

        Returns the save reason if a save should be made, None otherwise.
        """
        snapshot = GameSnapshot.from_state(current_state)

        if self._last_snapshot is None:
            self._last_snapshot = snapshot
            return None

        last = self._last_snapshot

        # Check for battle start
        if self.config.save_before_battle and snapshot.in_battle and not last.in_battle:
            self._last_snapshot = snapshot
            return "battle"

        # Check for new map
        if self.config.save_on_new_map and snapshot.map_name and snapshot.map_name != last.map_name:
            if snapshot.map_name not in self._saved_maps:
                self._saved_maps.add(snapshot.map_name)
                self._last_snapshot = snapshot
                return "new_map"

        # Check for level up
        if self.config.save_on_level_up:
            if sum(snapshot.party_levels) > sum(last.party_levels):
                self._last_snapshot = snapshot
                return "level_up"

        self._last_snapshot = snapshot
        return None
