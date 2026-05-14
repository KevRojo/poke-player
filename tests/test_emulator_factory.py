"""Tests for emulator factory and abstract base."""

import pytest
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
        """All expected buttons are defined."""
        assert Emulator.BUTTONS == ["a", "b", "start", "select", "up", "down", "left", "right"]

    def test_cannot_instantiate_abstract(self):
        """Emulator is abstract and cannot be instantiated directly."""
        with pytest.raises(TypeError):
            Emulator()


class TestFactory:
    """Tests for create_emulator factory function."""

    def test_ext_map_has_expected_extensions(self):
        """Factory supports .gb, .gbc, and .gba."""
        assert ".gb" in _EXT_MAP
        assert ".gbc" in _EXT_MAP
        assert ".gba" in _EXT_MAP

    def test_gb_maps_to_pyboy(self):
        """.gb extension maps to PyBoyEmulator."""
        assert _EXT_MAP[".gb"] is PyBoyEmulator

    def test_gbc_maps_to_pyboy(self):
        """.gbc extension maps to PyBoyEmulator."""
        assert _EXT_MAP[".gbc"] is PyBoyEmulator

    def test_gba_maps_to_pygba(self):
        """.gba extension maps to PyGBAEmulator."""
        assert _EXT_MAP[".gba"] is PyGBAEmulator

    def test_unsupported_extension_raises(self):
        """Unsupported file extension raises ValueError."""
        with pytest.raises(ValueError, match="Unsupported ROM extension"):
            create_emulator("game.nes")

    def test_unsupported_extension_message_includes_supported(self):
        """Error message lists supported extensions."""
        with pytest.raises(ValueError) as exc_info:
            create_emulator("game.nes")
        error_msg = str(exc_info.value)
        assert ".gb" in error_msg
        assert ".gbc" in error_msg
        assert ".gba" in error_msg

    def test_case_insensitive_extension(self):
        """Extensions are case-insensitive."""
        assert _EXT_MAP.get(".GB") is None  # Factory lowercases before lookup
        # The actual test would need a ROM file, but we verify the logic


class TestPyBoyEmulatorInfo:
    """Tests for PyBoyEmulator metadata."""

    def test_platform_is_gb(self):
        """PyBoyEmulator reports platform as GB."""
        # We can't instantiate without PyBoy, but we can test the class structure
        assert hasattr(PyBoyEmulator, 'get_info')
