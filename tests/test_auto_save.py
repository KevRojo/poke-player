"""Tests for auto-save manager."""

import asyncio
import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path

from poke_player.auto_save import AutoSaveConfig, AutoSaveManager, GameSnapshot


class TestAutoSaveConfig:
    """Tests for AutoSaveConfig dataclass."""

    def test_default_values(self):
        """Default config should have sensible values."""
        config = AutoSaveConfig()
        assert config.enabled is True
        assert config.interval_seconds == 300
        assert config.save_before_battle is True
        assert config.save_on_new_map is True
        assert config.max_auto_saves == 10
        assert config.prefix == "auto"

    def test_custom_values(self):
        """Custom config values should be respected."""
        config = AutoSaveConfig(
            enabled=False,
            interval_seconds=60,
            max_auto_saves=5,
            prefix="test",
        )
        assert config.enabled is False
        assert config.interval_seconds == 60
        assert config.max_auto_saves == 5
        assert config.prefix == "test"


class TestGameSnapshot:
    """Tests for GameSnapshot."""

    def test_from_empty_state(self):
        """Snapshot from empty state should have defaults."""
        snapshot = GameSnapshot.from_state({})
        assert snapshot.map_name == ""
        assert snapshot.in_battle is False
        assert snapshot.party_levels == []
        assert snapshot.money == 0
        assert snapshot.badges == 0

    def test_from_full_state(self):
        """Snapshot from complete state."""
        state = {
            "map": {"name": "Pallet Town"},
            "battle": {"in_battle": True},
            "party": [
                {"level": 5},
                {"level": 10},
            ],
            "player": {
                "money": 1234,
                "badges": ["Boulder"],
            },
        }
        snapshot = GameSnapshot.from_state(state)
        assert snapshot.map_name == "Pallet Town"
        assert snapshot.in_battle is True
        assert snapshot.party_levels == [5, 10]
        assert snapshot.money == 1234
        assert snapshot.badges == 1

    def test_from_legacy_state(self):
        """Snapshot from legacy flat state format."""
        state = {
            "map": "Viridian City",
            "battle": False,
        }
        snapshot = GameSnapshot.from_state(state)
        assert snapshot.map_name == "Viridian City"
        assert snapshot.in_battle is False


class TestAutoSaveManager:
    """Tests for AutoSaveManager."""

    def test_init_creates_directory(self, tmp_path):
        """Manager should create saves directory if it doesn't exist."""
        saves_dir = tmp_path / "saves"
        assert not saves_dir.exists()

        emu = MagicMock()
        manager = AutoSaveManager(emu, saves_dir=saves_dir)

        assert saves_dir.exists()

    @pytest.mark.asyncio
    async def test_start_stop(self):
        """Start and stop should work without errors."""
        emu = MagicMock()
        manager = AutoSaveManager(emu)

        # Should not raise
        manager.start()
        assert manager._running is True

        # Give the task a moment to start
        await asyncio.sleep(0.01)

        manager.stop()
        assert manager._running is False

    def test_disabled_config(self):
        """Disabled config should not start monitoring."""
        emu = MagicMock()
        config = AutoSaveConfig(enabled=False)
        manager = AutoSaveManager(emu, config=config)

        manager.start()
        assert manager._task is None  # No task created

    def test_check_state_change_battle(self):
        """Should detect battle start."""
        emu = MagicMock()
        manager = AutoSaveManager(emu)

        # Initial state - no battle
        state1 = {"battle": {"in_battle": False}}
        result = manager.check_state_change(state1)
        assert result is None

        # Battle starts
        state2 = {"battle": {"in_battle": True}}
        result = manager.check_state_change(state2)
        assert result == "battle"

    def test_check_state_change_new_map(self):
        """Should detect new map."""
        emu = MagicMock()
        manager = AutoSaveManager(emu)

        # Initial state
        state1 = {"map": {"name": "Pallet Town"}, "battle": {"in_battle": False}}
        manager.check_state_change(state1)

        # New map
        state2 = {"map": {"name": "Viridian City"}, "battle": {"in_battle": False}}
        result = manager.check_state_change(state2)
        assert result == "new_map"

    def test_check_state_change_no_duplicate_map_save(self):
        """Should not save again for same map."""
        emu = MagicMock()
        manager = AutoSaveManager(emu)

        state1 = {"map": {"name": "Pallet Town"}, "battle": {"in_battle": False}}
        manager.check_state_change(state1)

        # Same map again
        state2 = {"map": {"name": "Pallet Town"}, "battle": {"in_battle": False}}
        result = manager.check_state_change(state2)
        assert result is None

    def test_check_state_change_level_up(self):
        """Should detect level up."""
        emu = MagicMock()
        manager = AutoSaveManager(emu)

        state1 = {
            "party": [{"level": 5}],
            "battle": {"in_battle": False},
        }
        manager.check_state_change(state1)

        state2 = {
            "party": [{"level": 6}],
            "battle": {"in_battle": False},
        }
        result = manager.check_state_change(state2)
        assert result == "level_up"

    def test_cleanup_old_saves(self, tmp_path):
        """Should keep only max_auto_saves."""
        saves_dir = tmp_path / "saves"
        saves_dir.mkdir()

        # Create 15 fake auto-save files with different timestamps
        import time
        for i in range(15):
            f = saves_dir / f"auto_test_{i:02d}.state"
            f.write_text("fake")
            # Set different modification times
            mtime = time.time() - (15 - i) * 60  # Older files first
            import os
            os.utime(f, (mtime, mtime))

        emu = MagicMock()
        config = AutoSaveConfig(max_auto_saves=5)
        manager = AutoSaveManager(emu, config=config, saves_dir=saves_dir)

        # Mock logger to avoid circular import
        with patch('poke_player.auto_save._get_logger'):
            manager._cleanup_old_saves()

        remaining = list(saves_dir.glob("*.state"))
        assert len(remaining) == 5
