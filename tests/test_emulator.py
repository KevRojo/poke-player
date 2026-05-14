"""Tests for emulator module."""

import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path

from poke_player.emulator import (
    Emulator,
    PyBoyEmulator,
    PyGBAEmulator,
    create_emulator,
    _EXT_MAP,
)


class TestEmulatorABC:
    """Tests for the abstract Emulator base class."""

    def test_buttons_defined(self):
        """Emulator should define standard buttons."""
        assert Emulator.BUTTONS == ["a", "b", "start", "select", "up", "down", "left", "right"]

    def test_cannot_instantiate_abstract(self):
        """Abstract class cannot be instantiated directly."""
        with pytest.raises(TypeError):
            Emulator()


class TestCreateEmulator:
    """Tests for the emulator factory."""

    def test_create_gb_emulator(self):
        """Factory creates PyBoyEmulator for .gb files."""
        with patch.dict("poke_player.emulator._EXT_MAP", {".gb": MagicMock()}, clear=False):
            mock_cls = _EXT_MAP[".gb"]
            mock_instance = MagicMock()
            mock_cls.return_value = mock_instance

            result = create_emulator("/path/to/game.gb")

            mock_cls.assert_called_once()
            mock_instance.load.assert_called_once_with("/path/to/game.gb")
            assert result is mock_instance

    def test_create_gbc_emulator(self):
        """Factory creates PyBoyEmulator for .gbc files."""
        with patch.dict("poke_player.emulator._EXT_MAP", {".gbc": MagicMock()}, clear=False):
            mock_cls = _EXT_MAP[".gbc"]
            mock_instance = MagicMock()
            mock_cls.return_value = mock_instance

            result = create_emulator("/path/to/game.gbc")

            mock_cls.assert_called_once()
            assert result is mock_instance

    def test_create_gba_emulator(self):
        """Factory creates PyGBAEmulator for .gba files."""
        with patch.dict("poke_player.emulator._EXT_MAP", {".gba": MagicMock()}, clear=False):
            mock_cls = _EXT_MAP[".gba"]
            mock_instance = MagicMock()
            mock_cls.return_value = mock_instance

            result = create_emulator("/path/to/game.gba")

            mock_cls.assert_called_once()
            mock_instance.load.assert_called_once_with("/path/to/game.gba")
            assert result is mock_instance

    def test_unsupported_extension(self):
        """Factory raises ValueError for unsupported extensions."""
        with pytest.raises(ValueError, match="Unsupported ROM extension"):
            create_emulator("/path/to/game.nes")

    def test_extension_map_keys(self):
        """Extension map covers expected formats."""
        assert ".gb" in _EXT_MAP
        assert ".gbc" in _EXT_MAP
        assert ".gba" in _EXT_MAP

    def test_case_insensitive_extension(self):
        """Extensions should be case-insensitive."""
        with patch.dict("poke_player.emulator._EXT_MAP", {".gb": MagicMock(), ".gbc": MagicMock()}, clear=False):
            mock_instance_gb = MagicMock()
            mock_instance_gbc = MagicMock()
            _EXT_MAP[".gb"].return_value = mock_instance_gb
            _EXT_MAP[".gbc"].return_value = mock_instance_gbc

            result = create_emulator("/path/to/game.GB")
            assert result is mock_instance_gb

            result = create_emulator("/path/to/game.GbC")
            assert result is mock_instance_gbc


class TestPyBoyEmulatorInfo:
    """Tests for PyBoyEmulator.get_info."""

    def test_get_info_platform(self):
        """Info should include platform when set."""
        emu = PyBoyEmulator()
        emu.frame_count = 100
        emu.rom_path = "test.gb"

        info = emu.get_info()

        assert info["platform"] == "GB/GBC"
        assert info["frame_count"] == 100
        assert info["backend"] == "PyBoyEmulator"


class TestPyGBAEmulatorInfo:
    """Tests for PyGBAEmulator.get_info."""

    def test_get_info_platform(self):
        """Info should include platform."""
        emu = PyGBAEmulator()
        emu._gba = MagicMock()
        emu.frame_count = 200
        emu.rom_path = "test.gba"

        info = emu.get_info()

        assert info["platform"] == "GBA"
        assert info["frame_count"] == 200
        assert info["backend"] == "PyGBAEmulator"
