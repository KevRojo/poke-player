"""Tests for memory reader base class."""

import pytest
from unittest.mock import MagicMock

from poke_player.memory.reader import GameMemoryReader


class ConcreteReader(GameMemoryReader):
    """Concrete implementation for testing abstract methods."""

    @property
    def game_name(self):
        return "Test Game"

    def read_player(self):
        return {"name": "Test"}

    def read_party(self):
        return []

    def read_bag(self):
        return []

    def read_battle(self):
        return {"in_battle": False}

    def read_dialog(self):
        return {"active": False}

    def read_map_info(self):
        return {"map_id": 0, "map_name": "Test"}

    def read_flags(self):
        return {}


class TestGameMemoryReader:
    """Tests for GameMemoryReader base class."""

    def test_cannot_instantiate_abstract(self):
        """Base class is abstract."""
        mock_emu = MagicMock()
        with pytest.raises(TypeError):
            GameMemoryReader(mock_emu)

    def test_concrete_can_instantiate(self):
        """Concrete subclass can be instantiated."""
        mock_emu = MagicMock()
        reader = ConcreteReader(mock_emu)
        assert reader.emu is mock_emu

    def test_read_string_basic(self):
        """String decoding with simple encoding."""
        mock_emu = MagicMock()
        mock_emu.read_range.return_value = [0x41, 0x42, 0x43, 0x50]  # ABC<term>
        reader = ConcreteReader(mock_emu)

        encoding = {0x41: "A", 0x42: "B", 0x43: "C"}
        result = reader.read_string(0xD158, 4, encoding, terminator=0x50)
        assert result == "ABC"

    def test_read_string_with_unknown_bytes(self):
        """Unknown bytes become '?'."""
        mock_emu = MagicMock()
        mock_emu.read_range.return_value = [0x41, 0x99, 0x50]  # A, unknown, term
        reader = ConcreteReader(mock_emu)

        encoding = {0x41: "A"}
        result = reader.read_string(0xD158, 3, encoding)
        assert result == "A?"

    def test_read_bcd(self):
        """BCD decoding."""
        mock_emu = MagicMock()
        # 0x12 0x34 = 1234 in BCD
        mock_emu.read_range.return_value = [0x12, 0x34]
        reader = ConcreteReader(mock_emu)

        result = reader.read_bcd(0xD347, 2)
        assert result == 1234

    def test_read_bcd_single_byte(self):
        """Single byte BCD."""
        mock_emu = MagicMock()
        mock_emu.read_range.return_value = [0x56]
        reader = ConcreteReader(mock_emu)

        result = reader.read_bcd(0xD347, 1)
        assert result == 56

    def test_read_bits(self):
        """Bit flag reading."""
        mock_emu = MagicMock()
        # 0b00000001, 0b00000010
        mock_emu.read_range.return_value = [0x01, 0x02]
        reader = ConcreteReader(mock_emu)

        bits = reader.read_bits(0xD356, 2)
        assert len(bits) == 16
        assert bits[0] is True   # bit 0 of first byte
        assert bits[1] is False  # bit 1 of first byte
        assert bits[8] is False  # bit 0 of second byte
        assert bits[9] is True   # bit 1 of second byte
