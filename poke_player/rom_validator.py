"""Validación y detección de ROMs de Pokémon.

Soporta:
- Game Boy / Game Boy Color (.gb, .gbc)
- Game Boy Advance (.gba)

Detecta el juego específico leyendo el header de la ROM y
valida integridad básica (tamaño, checksums del header).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from poke_player.logging_config import get_logger

logger = get_logger("poke_player.rom_validator")


# ---------------------------------------------------------------------------
# Enums y constantes
# ---------------------------------------------------------------------------

class Platform(Enum):
    """Plataforma de la consola."""
    GAME_BOY = auto()
    GAME_BOY_COLOR = auto()
    GAME_BOY_ADVANCE = auto()


class GameTitle(Enum):
    """Títulos de Pokémon soportados."""
    RED = "Pokémon Red"
    BLUE = "Pokémon Blue"
    YELLOW = "Pokémon Yellow"
    FIRERED = "Pokémon FireRed"
    LEAFGREEN = "Pokémon LeafGreen"
    EMERALD = "Pokémon Emerald"
    RUBY = "Pokémon Ruby"
    SAPPHIRE = "Pokémon Sapphire"
    CRYSTAL = "Pokémon Crystal"
    GOLD = "Pokémon Gold"
    SILVER = "Pokémon Silver"
    UNKNOWN = "Unknown"


# Mapeo de títulos en header → GameTitle
_GB_TITLE_MAP: Dict[bytes, GameTitle] = {
    b"POKEMON RED": GameTitle.RED,
    b"POKEMON BLU": GameTitle.BLUE,
    b"POKEMON YEL": GameTitle.YELLOW,
    b"PM_CRYSTAL": GameTitle.CRYSTAL,
    b"POKEMON_GLD": GameTitle.GOLD,
    b"POKEMON_SLV": GameTitle.SILVER,
}

# GBA usa un código de 4 caracteres
_GBA_CODE_MAP: Dict[str, GameTitle] = {
    "BPR": GameTitle.FIRERED,      # BPRE / BPRP
    "BPG": GameTitle.LEAFGREEN,    # BPGE
    "BPE": GameTitle.EMERALD,      # BPEE
    "AXV": GameTitle.RUBY,         # AXVE
    "AXP": GameTitle.SAPPHIRE,     # AXPE
}

# Tamaños válidos de ROM (en bytes)
_VALID_GB_SIZES = {32 * 1024, 64 * 1024, 128 * 1024, 256 * 1024,
                   512 * 1024, 1024 * 1024, 2048 * 1024, 4096 * 1024}
_VALID_GBA_SIZES = set()
for i in range(5, 10):  # 32KB * 2^5 a 32KB * 2^9 = 1MB a 32MB
    _VALID_GBA_SIZES.add(32 * 1024 * (2 ** i))


# ---------------------------------------------------------------------------
# Dataclass de resultado
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ROMInfo:
    """Información validada de una ROM."""
    path: str
    platform: Platform
    game_title: GameTitle
    header_title: str
    header_code: str
    is_valid: bool
    errors: List[str]
    warnings: List[str]
    size: int
    header_checksum_valid: bool
    global_checksum_valid: Optional[bool]  # GB only

    @property
    def game_type(self) -> str:
        """Retorna 'red' o 'firered' para compatibilidad con el server."""
        if self.game_title in (GameTitle.RED, GameTitle.BLUE, GameTitle.YELLOW,
                                GameTitle.CRYSTAL, GameTitle.GOLD, GameTitle.SILVER):
            return "red"
        elif self.game_title in (GameTitle.FIRERED, GameTitle.LEAFGREEN,
                                  GameTitle.EMERALD, GameTitle.RUBY, GameTitle.SAPPHIRE):
            return "firered"
        return "auto"


# ---------------------------------------------------------------------------
# Funciones de validación
# ---------------------------------------------------------------------------

def _compute_gb_header_checksum(data: bytes) -> int:
    """Calcula el checksum del header de Game Boy (bytes $0134-$014C)."""
    checksum = 0
    for b in data[0x0134:0x014D]:
        checksum = (checksum - b - 1) & 0xFF
    return checksum


def _compute_gb_global_checksum(data: bytes) -> int:
    """Calcula el checksum global de Game Boy (suma de todos los bytes excepto los dos del checksum)."""
    total = sum(data[:0x014E]) + sum(data[0x0150:])
    return total & 0xFFFF


def _validate_gb_rom(data: bytes, path: str) -> ROMInfo:
    """Valida una ROM de Game Boy / Game Boy Color."""
    errors: List[str] = []
    warnings: List[str] = []

    size = len(data)
    if size not in _VALID_GB_SIZES:
        warnings.append(f"Tamaño inusual: {size} bytes")

    # Nintendo logo (bytes $0104-$0133)
    nintendo_logo = bytes([
        0xCE, 0xED, 0x66, 0x66, 0xCC, 0x0D, 0x00, 0x0B,
        0x03, 0x73, 0x00, 0x83, 0x00, 0x0C, 0x00, 0x0D,
        0x00, 0x08, 0x11, 0x1F, 0x88, 0x89, 0x00, 0x0E,
        0xDC, 0xCC, 0x6E, 0xE6, 0xDD, 0xDD, 0xD9, 0x99,
        0xBB, 0xBB, 0x67, 0x63, 0x6E, 0x0E, 0xEC, 0xCC,
        0xDD, 0xDC, 0x99, 0x9F, 0xBB, 0xB9, 0x33, 0x3E,
    ])
    logo_valid = data[0x0104:0x0134] == nintendo_logo
    if not logo_valid:
        errors.append("Nintendo logo inválido — posible ROM corrupta o no oficial")

    # Título (bytes $0134-$0143, 16 chars max, null-padded)
    raw_title = data[0x0134:0x0144]
    title_bytes = raw_title.split(b"\x00")[0]
    header_title = title_bytes.decode("ascii", errors="replace").strip()

    # Detectar juego
    game_title = GameTitle.UNKNOWN
    for key, value in _GB_TITLE_MAP.items():
        if title_bytes.upper().startswith(key):
            game_title = value
            break

    # Si no detectamos por título, intentar por características
    if game_title == GameTitle.UNKNOWN:
        # Yellow tiene ciertos bytes distintivos
        if b"YEL" in title_bytes.upper() or b"YELL" in title_bytes.upper():
            game_title = GameTitle.YELLOW
        elif b"RED" in title_bytes.upper():
            game_title = GameTitle.RED
        elif b"BLU" in title_bytes.upper() or b"BLUE" in title_bytes.upper():
            game_title = GameTitle.BLUE

    # Checksum del header (byte $014D)
    header_checksum = data[0x014D]
    computed_header_checksum = _compute_gb_header_checksum(data)
    header_checksum_valid = header_checksum == computed_header_checksum
    if not header_checksum_valid:
        warnings.append(
            f"Header checksum inválido: esperado {computed_header_checksum:02X}, "
            f"encontrado {header_checksum:02X}"
        )

    # Checksum global (bytes $014E-$014F)
    global_checksum = struct.unpack(">H", data[0x014E:0x0150])[0]
    computed_global_checksum = _compute_gb_global_checksum(data)
    global_checksum_valid = global_checksum == computed_global_checksum
    if not global_checksum_valid:
        warnings.append(
            f"Global checksum inválido: esperado {computed_global_checksum:04X}, "
            f"encontrado {global_checksum:04X}"
        )

    # Determinar plataforma
    cgb_flag = data[0x0143]
    platform = Platform.GAME_BOY_COLOR if cgb_flag in (0x80, 0xC0) else Platform.GAME_BOY

    is_valid = len(errors) == 0 and logo_valid

    return ROMInfo(
        path=path,
        platform=platform,
        game_title=game_title,
        header_title=header_title,
        header_code="",
        is_valid=is_valid,
        errors=errors,
        warnings=warnings,
        size=size,
        header_checksum_valid=header_checksum_valid,
        global_checksum_valid=global_checksum_valid,
    )


def _validate_gba_rom(data: bytes, path: str) -> ROMInfo:
    """Valida una ROM de Game Boy Advance."""
    errors: List[str] = []
    warnings: List[str] = []

    size = len(data)
    if size not in _VALID_GBA_SIZES:
        warnings.append(f"Tamaño inusual: {size} bytes")

    # Logo de Nintendo (bytes $04-$9F)
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
    logo_valid = data[0x04:0xA0] == gba_logo
    if not logo_valid:
        errors.append("Nintendo logo inválido — posible ROM corrupta o no oficial")

    # Título (bytes $A0-$AB, 12 caracteres)
    raw_title = data[0xA0:0xAC]
    header_title = raw_title.split(b"\x00")[0].decode("ascii", errors="replace").strip()

    # Código del juego (bytes $AC-$AF, 4 caracteres)
    header_code = data[0xAC:0xB0].decode("ascii", errors="replace").strip()

    # Detectar juego por código
    game_title = GameTitle.UNKNOWN
    for prefix, title in _GBA_CODE_MAP.items():
        if header_code.startswith(prefix):
            game_title = title
            break

    # Complement check (byte $B0)
    complement = data[0xB0]
    header_checksum = data[0xAC:0xB0]
    computed_complement = (-sum(header_checksum) - 0x19) & 0xFF
    header_checksum_valid = complement == computed_complement
    if not header_checksum_valid:
        warnings.append(
            f"Complement check inválido: esperado {computed_complement:02X}, "
            f"encontrado {complement:02X}"
        )

    is_valid = len(errors) == 0 and logo_valid

    return ROMInfo(
        path=path,
        platform=Platform.GAME_BOY_ADVANCE,
        game_title=game_title,
        header_title=header_title,
        header_code=header_code,
        is_valid=is_valid,
        errors=errors,
        warnings=warnings,
        size=size,
        header_checksum_valid=header_checksum_valid,
        global_checksum_valid=None,  # GBA no tiene checksum global simple
    )


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------

def validate_rom(path: str) -> ROMInfo:
    """Valida una ROM y retorna información detallada.

    Parameters
    ----------
    path : str
        Ruta al archivo ROM.

    Returns
    -------
    ROMInfo
        Información validada de la ROM.

    Raises
    ------
    FileNotFoundError
        Si el archivo no existe.
    ValueError
        Si la extensión no es soportada.
    """
    rom_path = Path(path).expanduser().resolve()
    if not rom_path.exists():
        raise FileNotFoundError(f"ROM no encontrada: {rom_path}")

    ext = rom_path.suffix.lower()
    if ext not in (".gb", ".gbc", ".gba"):
        raise ValueError(f"Extensión no soportada: {ext}. Use .gb, .gbc o .gba")

    data = rom_path.read_bytes()
    logger.info(f"Validando ROM: {rom_path} ({len(data)} bytes)")

    if ext in (".gb", ".gbc"):
        info = _validate_gb_rom(data, str(rom_path))
    else:
        info = _validate_gba_rom(data, str(rom_path))

    # Loggear resultado
    if info.is_valid:
        logger.info(
            f"ROM válida: {info.game_title.value} ({info.platform.name})",
            extra={"game": info.game_title.value, "platform": info.platform.name},
        )
    else:
        logger.warning(
            f"ROM con problemas: {info.game_title.value}",
            extra={"errors": info.errors, "warnings": info.warnings},
        )

    return info


def detect_game_type(path: str) -> str:
    """Detecta el tipo de juego ('red' o 'firered') desde una ROM.

    Esta es una versión más robusta que la detección por extensión
    usada actualmente en el server.
    """
    try:
        info = validate_rom(path)
        return info.game_type
    except Exception as e:
        logger.warning(f"No se pudo detectar tipo de juego: {e}")
        # Fallback a detección por extensión
        ext = Path(path).suffix.lower()
        if ext in (".gb", ".gbc"):
            return "red"
        elif ext == ".gba":
            return "firered"
        return "auto"


def is_valid_rom(path: str) -> bool:
    """Validación rápida — retorna True/False."""
    try:
        info = validate_rom(path)
        return info.is_valid
    except Exception:
        return False
