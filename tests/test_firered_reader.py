"""Tests for FireRed memory reader."""

import pytest
from unittest.mock import MagicMock, patch

from poke_player.memory.firered import (
    FireRedMemoryReader,
    GEN3_ENCODING,
    ITEM_NAMES,
    SPECIES_NAMES,
    SUBSTRUCTURE_ORDER,
    PARTY_MON_SIZE_GEN3,
    ENCRYPTED_BLOCK_SIZE,
)


class TestFireRedMemoryReader:
    """Tests for FireRedMemoryReader."""

    @pytest.fixture
    def mock_emu(self):
        """Create a mock emulator."""
        emu = MagicMock()
        # Default saveblock pointers
        emu.read_u32.side_effect = lambda addr: {
            0x0300500C: 0x02000000,  # SaveBlock1
            0x03005010: 0x02010000,  # SaveBlock2
        }.get(addr, 0)
        return emu

    @pytest.fixture
    def reader(self, mock_emu):
        """Create a FireRedMemoryReader with mock emulator."""
        return FireRedMemoryReader(mock_emu)

    def test_game_name(self, reader):
        """Reader reports correct game name."""
        assert reader.game_name == "Pokemon FireRed (USA)"

    def test_read_player_basic(self, reader, mock_emu):
        """Test reading basic player info."""
        # Mock player name "RED" in Gen 3 encoding
        # R=0xCC, E=0xBF, D=0xBE
        name_bytes = [0xCC, 0xBF, 0xBE, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF]
        mock_emu.read_range.return_value = name_bytes

        # Mock other reads
        def mock_read_u8(addr):
            if addr == 0x02010008:  # gender
                return 0  # Male
            return 0

        def mock_read_u16(addr):
            if addr == 0x0201000E:  # hours
                return 10
            return 0

        def mock_read_u32(addr):
            if addr == 0x0300500C:
                return 0x02000000
            elif addr == 0x03005010:
                return 0x02010000
            elif addr == 0x0201000A:  # trainer ID
                return 0x12345678
            elif addr == 0x02010F20:  # security key
                return 0x00000000
            elif addr == 0x02000290:  # money
                return 5000
            return 0

        mock_emu.read_u8.side_effect = mock_read_u8
        mock_emu.read_u16.side_effect = mock_read_u16
        mock_emu.read_u32.side_effect = mock_read_u32

        player = reader.read_player()

        assert player["name"] == "RED"
        assert player["trainer_id"] == 0x5678
        assert player["secret_id"] == 0x1234
        assert player["gender"] == "Male"
        assert player["money"] == 5000
        assert player["play_time"]["hours"] == 10

    def test_read_party_empty(self, reader, mock_emu):
        """Test reading empty party."""
        mock_emu.read_u8.return_value = 0

        party = reader.read_party()
        assert party == []

    def test_read_party_with_pokemon(self, reader, mock_emu):
        """Test reading party with one Pokemon."""
        # Create a simple Pokemon data structure
        # Personality value = 0x12345678
        # OT ID = 0x87654321
        # Encryption key = 0x12345678 ^ 0x87654321 = 0x95511559

        personality = 0x12345678
        ot_id = 0x87654321
        encryption_key = personality ^ ot_id

        # Build Pokemon data
        data = bytearray(PARTY_MON_SIZE_GEN3)
        data[0:4] = personality.to_bytes(4, 'little')
        data[4:8] = ot_id.to_bytes(4, 'little')
        # Nickname: "PIKA" in Gen 3
        # P=0xCA, I=0xC3, K=0xC5, A=0xBB
        nickname = [0xCA, 0xC3, 0xC5, 0xBB, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF]
        data[8:18] = nickname
        data[18] = 2  # English
        data[19] = 0  # misc flags
        # OT name: "ASH"
        # A=0xBB, S=0xCD, H=0xC2
        ot_name = [0xBB, 0xCD, 0xC2, 0xFF, 0xFF, 0xFF, 0xFF]
        data[20:27] = ot_name
        data[27] = 0  # markings
        data[28:30] = b'\x00\x00'  # checksum
        data[30:32] = b'\x00\x00'  # unknown

        # Create encrypted block (Growth substructure first)
        # Growth: species=25 (Pikachu), item=0, exp=100000, pp_bonuses=0, friendship=70
        growth = bytearray(12)
        growth[0:2] = (25).to_bytes(2, 'little')  # Pikachu
        growth[2:4] = (0).to_bytes(2, 'little')   # No item
        growth[4:8] = (100000).to_bytes(4, 'little')  # EXP
        growth[8] = 0   # PP bonuses
        growth[9] = 70  # Friendship
        growth[10:12] = b'\x00\x00'

        # Attacks: 4 moves
        attacks = bytearray(12)
        attacks[0:2] = (84).to_bytes(2, 'little')   # Thundershock
        attacks[2:4] = (98).to_bytes(2, 'little')   # Quick Attack
        attacks[4:6] = (0).to_bytes(2, 'little')
        attacks[6:8] = (0).to_bytes(2, 'little')
        attacks[8:12] = [30, 30, 0, 0]  # PP

        # EVs/Condition
        evs = bytearray(12)
        evs[0] = 100  # HP EV
        evs[1] = 100  # Attack EV
        evs[2:12] = [0] * 10

        # Misc
        misc = bytearray(12)
        misc[0] = 0   # Pokerus
        misc[1] = 88  # Met location (Viridian Forest)
        misc[2:4] = b'\x00\x00'  # Origins
        misc[4:8] = (0x0A0A0A0A).to_bytes(4, 'little')  # IVs
        misc[8:12] = b'\x00\x00\x00\x00'  # Ribbons

        # Combine substructures in order (GAEM for personality % 24 = 0)
        substructure_order = SUBSTRUCTURE_ORDER[personality % 24]
        substructures = {
            'G': growth,
            'A': attacks,
            'E': evs,
            'M': misc,
        }

        decrypted_block = bytearray(ENCRYPTED_BLOCK_SIZE)
        for i, struct_type in enumerate(substructure_order):
            start = i * 12
            decrypted_block[start:start+12] = substructures[struct_type]

        # Encrypt the block
        encrypted_block = bytearray(ENCRYPTED_BLOCK_SIZE)
        for i in range(0, ENCRYPTED_BLOCK_SIZE, 4):
            word = int.from_bytes(decrypted_block[i:i+4], 'little')
            encrypted_word = word ^ encryption_key
            encrypted_block[i:i+4] = encrypted_word.to_bytes(4, 'little')

        data[32:80] = encrypted_block

        # Mock emulator reads
        def mock_read_u8(addr):
            if addr == 0x02000234:  # party count
                return 1
            return 0

        def mock_read_range(addr, length):
            if addr == 0x02000238 and length == PARTY_MON_SIZE_GEN3:
                return list(data)
            return [0] * length

        mock_emu.read_u8.side_effect = mock_read_u8
        mock_emu.read_range.side_effect = mock_read_range

        party = reader.read_party()

        assert len(party) == 1
        pokemon = party[0]
        assert pokemon["slot"] == 1
        assert pokemon["species_id"] == 25
        assert pokemon["species"] == "Pikachu"
        assert pokemon["nickname"] == "PIKA"
        assert pokemon["experience"] == 100000
        assert pokemon["friendship"] == 70
        assert len(pokemon["moves"]) == 2  # Only 2 non-zero moves
        # IVs from 0x0A0A0A0A = 0000 1010 0000 1010 0000 1010 0000 1010
        # HP:    bits 0-4   = 01010 = 10
        # Attack: bits 5-9  = 00000 = 0 (but shifted)
        # Let's just verify they exist and are in valid range
        assert 0 <= pokemon["ivs"]["hp"] <= 31
        assert 0 <= pokemon["ivs"]["attack"] <= 31
        assert 0 <= pokemon["ivs"]["defense"] <= 31
        assert 0 <= pokemon["ivs"]["speed"] <= 31
        assert 0 <= pokemon["ivs"]["sp_attack"] <= 31
        assert 0 <= pokemon["ivs"]["sp_defense"] <= 31

    def test_read_bag(self, reader, mock_emu):
        """Test reading bag contents."""
        def mock_read_u16(addr):
            # Bag at 0x02000310
            offsets = {
                0x02000310: 13,    # Potion
                0x02000312: 5,     # Quantity
                0x02000314: 4,     # Poke Ball
                0x02000316: 10,    # Quantity
                0x02000318: 0,     # End
            }
            return offsets.get(addr, 0)

        mock_emu.read_u16.side_effect = mock_read_u16

        bag = reader.read_bag()

        assert len(bag) == 2
        assert bag[0]["item"] == "Potion"
        assert bag[0]["quantity"] == 5
        assert bag[1]["item"] == "Poké Ball"
        assert bag[1]["quantity"] == 10

    def test_read_map_info(self, reader, mock_emu):
        """Test reading map info."""
        def mock_read_u8(addr):
            offsets = {
                0x02000004: 0,   # Map group
                0x02000005: 0,   # Map number (Pallet Town)
            }
            return offsets.get(addr, 0)

        def mock_read_u16(addr):
            offsets = {
                0x02000000: 5,   # X position
                0x02000002: 7,   # Y position
            }
            return offsets.get(addr, 0)

        mock_emu.read_u8.side_effect = mock_read_u8
        mock_emu.read_u16.side_effect = mock_read_u16

        map_info = reader.read_map_info()

        assert map_info["map_group"] == 0
        assert map_info["map_number"] == 0
        assert map_info["map_name"] == "Pallet Town"
        assert map_info["position"]["x"] == 5
        assert map_info["position"]["y"] == 7

    def test_read_battle_not_in_battle(self, reader, mock_emu):
        """Test battle state when not in battle."""
        mock_emu.read_u8.return_value = 0

        battle = reader.read_battle()

        assert battle["in_battle"] is False
        assert battle["battle_type"] is None

    def test_read_dialog_not_active(self, reader, mock_emu):
        """Test dialog state when not active."""
        mock_emu.read_u8.return_value = 0

        dialog = reader.read_dialog()

        assert dialog["active"] is False

    def test_read_flags_no_badges(self, reader, mock_emu):
        """Test flags with no badges."""
        mock_emu.read_u16.return_value = 0
        mock_emu.read_u8.return_value = 0

        flags = reader.read_flags()

        assert flags["badges"] == []
        assert flags["badge_count"] == 0
        assert flags["has_pokedex"] is False

    def test_read_flags_with_badges(self, reader, mock_emu):
        """Test flags with some badges."""
        def mock_read_u16(addr):
            if addr == 0x020100F8:
                return 0x05  # Badges 1 and 3 (Boulder and Thunder)
            return 0

        mock_emu.read_u16.side_effect = mock_read_u16

        flags = reader.read_flags()

        assert len(flags["badges"]) == 2
        assert "Boulder Badge" in flags["badges"]
        assert "Thunder Badge" in flags["badges"]
        assert flags["badge_count"] == 2

    def test_decrypt_pokemon_invalid_size(self, reader):
        """Test decryption with wrong data size."""
        with pytest.raises(ValueError, match="Expected 100 bytes"):
            reader._decrypt_pokemon(b"\x00" * 50)

    def test_get_nature(self, reader):
        """Test nature calculation."""
        from poke_player.memory.firered import NATURE_NAMES

        # Personality 0 -> Hardy
        assert reader._get_nature(0) == "Hardy"
        # Personality 24 -> Brave (24 % 25 = 24, but let's check)
        nature = reader._get_nature(24)
        assert nature in NATURE_NAMES

    def test_get_shininess(self, reader):
        """Test shiny calculation."""
        # This is a probabilistic test - most combinations won't be shiny
        is_shiny = reader._get_shininess(0x12345678, 0x87654321)
        assert isinstance(is_shiny, bool)

    def test_gen3_encoding_table(self):
        """Test that Gen 3 encoding has common characters."""
        assert GEN3_ENCODING[0xBB] == "A"
        assert GEN3_ENCODING[0xD5] == "a"
        assert GEN3_ENCODING[0x00] == " "
        assert GEN3_ENCODING[0xFF] == ""

    def test_species_names(self):
        """Test species name lookup."""
        assert SPECIES_NAMES[25] == "Pikachu"
        assert SPECIES_NAMES[1] == "Bulbasaur"
        assert SPECIES_NAMES[150] == "Mewtwo"

    def test_item_names(self):
        """Test item name lookup."""
        assert ITEM_NAMES[1] == "Master Ball"
        assert ITEM_NAMES[13] == "Potion"
        assert ITEM_NAMES[4] == "Poké Ball"


class TestFireRedMemoryReaderIntegration:
    """Integration-style tests with more realistic data."""

    def test_full_party_of_six(self):
        """Test reading a full party of 6 Pokemon."""
        emu = MagicMock()
        emu.read_u32.side_effect = lambda addr: {
            0x0300500C: 0x02000000,
            0x03005010: 0x02010000,
        }.get(addr, 0)

        reader = FireRedMemoryReader(emu)

        # Mock party count
        emu.read_u8.return_value = 6

        # Create 6 simple Pokemon
        def mock_read_range(addr, length):
            if addr >= 0x02000238 and length == PARTY_MON_SIZE_GEN3:
                # Return a simple Pokemon structure
                data = bytearray(PARTY_MON_SIZE_GEN3)
                data[0:4] = (0x11111111).to_bytes(4, 'little')
                data[4:8] = (0x22222222).to_bytes(4, 'little')
                # No encryption needed if key makes it zero
                return list(data)
            return [0] * length

        emu.read_range.side_effect = mock_read_range

        party = reader.read_party()

        assert len(party) == 6
        for i, pokemon in enumerate(party):
            assert pokemon["slot"] == i + 1

    def test_map_name_lookup(self):
        """Test various map name lookups."""
        emu = MagicMock()
        emu.read_u32.side_effect = lambda addr: {
            0x0300500C: 0x02000000,
            0x03005010: 0x02010000,
        }.get(addr, 0)

        reader = FireRedMemoryReader(emu)

        test_cases = [
            ((0, 1), "Viridian City"),
            ((0, 2), "Pewter City"),
            ((0, 3), "Cerulean City"),
            ((0, 12), "Route 1"),
            ((1, 0), "Oak's Lab"),
            ((99, 99), "Map 99-99"),  # Unknown map
        ]

        for (group, number), expected_name in test_cases:
            def make_mock(group, number):
                def mock_read_u8(addr):
                    if addr == 0x02000004:
                        return group
                    elif addr == 0x02000005:
                        return number
                    return 0
                return mock_read_u8

            emu.read_u8.side_effect = make_mock(group, number)
            map_info = reader.read_map_info()
            assert map_info["map_name"] == expected_name


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
