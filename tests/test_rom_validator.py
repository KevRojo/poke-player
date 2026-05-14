"""Tests para el validador de ROMs."""

import pytest
from unittest.mock import MagicMock, patch

from poke_player.rom_validator import (
    GameTitle,
    Platform,
    ROMInfo,
    _compute_gb_header_checksum,
    _validate_gb_rom,
    detect_game_type,
    is_valid_rom,
    validate_rom,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_gb_rom(tmp_path):
    """Crea una ROM fake de Game Boy con header válido."""
    rom_path = tmp_path / "test.gb"
    data = bytearray(32 * 1024)  # 32KB mínimo

    # Nintendo logo (bytes $0104-$0133)
    logo = bytes([
        0xCE, 0xED, 0x66, 0x66, 0xCC, 0x0D, 0x00, 0x0B,
        0x03, 0x73, 0x00, 0x83, 0x00, 0x0C, 0x00, 0x0D,
        0x00, 0x08, 0x11, 0x1F, 0x88, 0x89, 0x00, 0x0E,
        0xDC, 0xCC, 0x6E, 0xE6, 0xDD, 0xDD, 0xD9, 0x99,
        0xBB, 0xBB, 0x67, 0x63, 0x6E, 0x0E, 0xEC, 0xCC,
        0xDD, 0xDC, 0x99, 0x9F, 0xBB, 0xB9, 0x33, 0x3E,
    ])
    data[0x0104:0x0134] = logo

    # Título "POKEMON RED" (bytes $0134-$0143)
    title = b"POKEMON RED"
    data[0x0134:0x0134 + len(title)] = title

    # Checksum del header (byte $014D)
    checksum = _compute_gb_header_checksum(bytes(data))
    data[0x014D] = checksum

    rom_path.write_bytes(bytes(data))
    return str(rom_path)


@pytest.fixture
def fake_gba_rom(tmp_path):
    """Crea una ROM fake de GBA con header válido."""
    rom_path = tmp_path / "test.gba"
    data = bytearray(1024 * 1024)  # 1MB

    # GBA logo (bytes $04-$9F)
    gba_logo = bytes([
        0x24, 0xFF, 0xAE, 0x51, 0x69, 0x9A, 0xA2, 0x21,
        0x3D, 0x84, 0x82, 0x0A, 0x84, 0xE4, 0x09, 0xAD,
        0x11, 0x24, 0x8B, 0x98, 0xC0, 0x81, 0x7F, 0x21,
        0xA3, 0x52, 0xBE, 0x19, 0x93, 0x09, 0xCE, 0x20,
        0x10, 0x46, 0x4A, 0x4A, 0xF8, 0x27, 0x31, 0xEC,
        0x58, 0xC7, 0xE8, 0x33, 0x82, 0xE3, 0xCE, 0xBF,
        0x85, 0xF4, 0xDF, 0x94, 0xCE, 0x4B, 0x09, 0xC1,
        0x94, 0x56, 0x8A, 0xC0, 0x13, 0x72, 0xA7, 0xFC,
        0x9F, 0x84, 0x4D, 0x73, 0xA3, 0xCA, 0x9A, 0x61,
        0x58, 0x97, 0xA3, 0x27, 0xFC, 0x03, 0x98, 0x76,
        0x23, 0x1D, 0xC7, 0x61, 0x03, 0x04, 0xAE, 0x56,
        0xBF, 0x38, 0x84, 0x00, 0x40, 0xA7, 0x0E, 0xFD,
        0xFF, 0x52, 0xFE, 0x03, 0x6F, 0x95, 0x30, 0xF1,
        0x97, 0xFB, 0xC0, 0x85, 0x60, 0xD6, 0x80, 0x25,
        0xA9, 0x63, 0xBE, 0x03, 0x01, 0x4E, 0x38, 0xE2,
        0xF9, 0xA2, 0x34, 0xFF, 0xBB, 0x3E, 0x03, 0x44,
        0x78, 0x00, 0x90, 0xCB, 0x88, 0x11, 0x3A, 0x94,
        0x65, 0xC0, 0x7C, 0x63, 0x87, 0xF0, 0x3C, 0xAF,
        0xD6, 0x25, 0xE4, 0x8B, 0x38, 0x0A, 0xAC, 0x72,
        0x21, 0xD4, 0xF8, 0x07,
    ])
    data[0x04:0xA0] = gba_logo

    # Título "POKEMON FIRE" (bytes $A0-$AB)
    title = b"POKEMON FIRE"
    data[0xA0:0xA0 + len(title)] = title

    # Código BPRE (bytes $AC-$AF)
    data[0xAC:0xB0] = b"BPRE"

    # Complement check (byte $B0)
    header_checksum = b"BPRE"
    complement = (-sum(header_checksum) - 0x19) & 0xFF
    data[0xB0] = complement

    rom_path.write_bytes(bytes(data))
    return str(rom_path)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestROMInfo:
    def test_game_type_red(self):
        info = ROMInfo(
            path="test.gb",
            platform=Platform.GAME_BOY,
            game_title=GameTitle.RED,
            header_title="POKEMON RED",
            header_code="",
            is_valid=True,
            errors=[],
            warnings=[],
            size=32768,
            header_checksum_valid=True,
            global_checksum_valid=True,
        )
        assert info.game_type == "red"

    def test_game_type_firered(self):
        info = ROMInfo(
            path="test.gba",
            platform=Platform.GAME_BOY_ADVANCE,
            game_title=GameTitle.FIRERED,
            header_title="POKEMON FIRE",
            header_code="BPRE",
            is_valid=True,
            errors=[],
            warnings=[],
            size=1048576,
            header_checksum_valid=True,
            global_checksum_valid=None,
        )
        assert info.game_type == "firered"


class TestValidateROM:
    def test_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            validate_rom("/no/existe/rom.gb")

    def test_unsupported_extension(self, tmp_path):
        bad_rom = tmp_path / "test.txt"
        bad_rom.write_text("not a rom")
        with pytest.raises(ValueError, match="Extensión no soportada"):
            validate_rom(str(bad_rom))

    def test_gb_rom_valid(self, fake_gb_rom):
        info = validate_rom(fake_gb_rom)
        assert info.platform == Platform.GAME_BOY
        assert info.game_title == GameTitle.RED
        assert info.is_valid is True
        assert info.header_checksum_valid is True

    def test_gb_rom_detects_yellow(self, tmp_path):
        """Test que detecta Yellow por el título."""
        rom_path = tmp_path / "yellow.gb"
        data = bytearray(32 * 1024)
        logo = bytes([
            0xCE, 0xED, 0x66, 0x66, 0xCC, 0x0D, 0x00, 0x0B,
            0x03, 0x73, 0x00, 0x83, 0x00, 0x0C, 0x00, 0x0D,
            0x00, 0x08, 0x11, 0x1F, 0x88, 0x89, 0x00, 0x0E,
            0xDC, 0xCC, 0x6E, 0xE6, 0xDD, 0xDD, 0xD9, 0x99,
            0xBB, 0xBB, 0x67, 0x63, 0x6E, 0x0E, 0xEC, 0xCC,
            0xDD, 0xDC, 0x99, 0x9F, 0xBB, 0xB9, 0x33, 0x3E,
        ])
        data[0x0104:0x0134] = logo
        data[0x0134:0x013F] = b"POKEMON YEL"
        data[0x014D] = _compute_gb_header_checksum(bytes(data))
        rom_path.write_bytes(bytes(data))

        info = validate_rom(str(rom_path))
        assert info.game_title == GameTitle.YELLOW
        assert info.game_type == "red"

    def test_gba_rom_valid(self, fake_gba_rom):
        info = validate_rom(fake_gba_rom)
        assert info.platform == Platform.GAME_BOY_ADVANCE
        assert info.game_title == GameTitle.FIRERED
        assert info.is_valid is True

    def test_gb_invalid_logo(self, tmp_path):
        """ROM con logo inválido debe marcar is_valid=False."""
        rom_path = tmp_path / "bad.gb"
        data = bytearray(32 * 1024)
        # Logo incorrecto
        data[0x0104:0x0134] = b"X" * 48
        data[0x0134:0x013F] = b"POKEMON RED"
        data[0x014D] = _compute_gb_header_checksum(bytes(data))
        rom_path.write_bytes(bytes(data))

        info = validate_rom(str(rom_path))
        assert info.is_valid is False
        assert any("Nintendo logo" in e for e in info.errors)


class TestDetectGameType:
    def test_detect_from_gb(self, fake_gb_rom):
        assert detect_game_type(fake_gb_rom) == "red"

    def test_detect_from_gba(self, fake_gba_rom):
        assert detect_game_type(fake_gba_rom) == "firered"

    def test_fallback_to_extension_gb(self, tmp_path):
        """Si falla la validación, fallback a extensión."""
        rom_path = tmp_path / "fallback.gb"
        rom_path.write_bytes(b"X" * 100)  # ROM muy corta, va a fallar
        result = detect_game_type(str(rom_path))
        assert result == "red"

    def test_fallback_to_extension_gba(self, tmp_path):
        rom_path = tmp_path / "fallback.gba"
        rom_path.write_bytes(b"X" * 100)
        result = detect_game_type(str(rom_path))
        assert result == "firered"


class TestIsValidROM:
    def test_valid_rom(self, fake_gb_rom):
        assert is_valid_rom(fake_gb_rom) is True

    def test_invalid_rom(self, tmp_path):
        rom_path = tmp_path / "bad.gb"
        rom_path.write_bytes(b"X" * 48)  # Muy corta, logo inválido
        assert is_valid_rom(str(rom_path)) is False

    def test_nonexistent_file(self):
        assert is_valid_rom("/no/existe.gb") is False
