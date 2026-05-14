"""Tests for state builder module."""

import pytest
from unittest.mock import MagicMock

from poke_player.state.builder import build_game_state, build_state_summary


class MockReader:
    """Mock memory reader for testing state builder."""

    def __init__(self, fail_methods=None):
        self.fail_methods = fail_methods or set()

    @property
    def game_name(self):
        return "Mock Pokemon Game"

    def read_player(self):
        if "player" in self.fail_methods:
            raise RuntimeError("Player read failed")
        return {
            "name": "Red",
            "rival_name": "Blue",
            "money": 12345,
            "badges": ["Boulder", "Cascade"],
            "position": {"x": 5, "y": 10},
            "facing": "down",
            "play_time": {"hours": 10, "minutes": 30, "seconds": 0},
        }

    def read_party(self):
        if "party" in self.fail_methods:
            raise RuntimeError("Party read failed")
        return [
            {
                "nickname": "PIKA",
                "species": "Pikachu",
                "level": 25,
                "hp": 60,
                "max_hp": 60,
                "status": "OK",
                "moves": [{"name": "Thunderbolt"}, {"name": "Quick Attack"}],
            }
        ]

    def read_bag(self):
        if "bag" in self.fail_methods:
            raise RuntimeError("Bag read failed")
        return [
            {"item": "Potion", "quantity": 5},
            {"item": "Poke Ball", "quantity": 10},
        ]

    def read_battle(self):
        if "battle" in self.fail_methods:
            raise RuntimeError("Battle read failed")
        return {"in_battle": False}

    def read_dialog(self):
        if "dialog" in self.fail_methods:
            raise RuntimeError("Dialog read failed")
        return {"active": False}

    def read_map_info(self):
        if "map" in self.fail_methods:
            raise RuntimeError("Map read failed")
        return {"map_id": 1, "map_name": "Pallet Town"}

    def read_flags(self):
        if "flags" in self.fail_methods:
            raise RuntimeError("Flags read failed")
        return {
            "has_pokedex": True,
            "has_oaks_parcel": True,
            "pokedex_owned": 10,
            "pokedex_seen": 25,
            "badges": ["Boulder", "Cascade"],
            "badge_count": 2,
        }


class TestBuildGameState:
    """Tests for build_game_state function."""

    def test_builds_complete_state(self):
        reader = MockReader()
        state = build_game_state(reader, frame_count=100)

        assert state["metadata"]["game"] == "Mock Pokemon Game"
        assert state["metadata"]["frame_count"] == 100
        assert "timestamp" in state["metadata"]

        assert state["player"]["name"] == "Red"
        assert state["party"][0]["species"] == "Pikachu"
        assert state["bag"][0]["item"] == "Potion"
        assert state["battle"]["in_battle"] is False
        assert state["dialog"]["active"] is False
        assert state["map"]["map_name"] == "Pallet Town"
        assert state["flags"]["badge_count"] == 2

    def test_not_implemented_error_handling(self):
        """NotImplementedError from reader methods should be handled gracefully."""
        reader = MockReader()
        # Monkey-patch to raise NotImplementedError
        reader.read_player = lambda: (_ for _ in ()).throw(NotImplementedError("Not yet"))

        state = build_game_state(reader)

        assert state["player"] is None
        assert "player_error" in state
        assert "Not yet" in state["player_error"]

    def test_general_exception_handling(self):
        """General exceptions should include traceback."""
        reader = MockReader()
        reader.read_player = lambda: (_ for _ in ()).throw(RuntimeError("Oops"))

        state = build_game_state(reader)

        assert state["player"] is None
        assert "player_error" in state
        assert "RuntimeError: Oops" in state["player_error"]
        assert "Traceback" in state["player_error"]

    def test_no_frame_count(self):
        """Frame count is optional."""
        reader = MockReader()
        state = build_game_state(reader)

        assert state["metadata"]["frame_count"] is None

    def test_timestamp_is_iso_format(self):
        """Timestamp should be in ISO format."""
        reader = MockReader()
        state = build_game_state(reader)

        timestamp = state["metadata"]["timestamp"]
        assert "T" in timestamp  # ISO format has T separator
        assert "Z" in timestamp or "+" in timestamp  # Has timezone info


class TestBuildStateSummary:
    """Tests for build_state_summary function."""

    def test_renders_player_info(self):
        reader = MockReader()
        state = build_game_state(reader)
        summary = build_state_summary(state)

        assert "Red" in summary
        assert "Blue" in summary
        assert "$12,345" in summary
        assert "Boulder" in summary
        assert "Cascade" in summary

    def test_renders_party_info(self):
        reader = MockReader()
        state = build_game_state(reader)
        summary = build_state_summary(state)

        assert "PIKA" in summary
        assert "Pikachu" in summary
        assert "Lv25" in summary
        assert "Thunderbolt" in summary

    def test_renders_map_info(self):
        reader = MockReader()
        state = build_game_state(reader)
        summary = build_state_summary(state)

        assert "Pallet Town" in summary

    def test_renders_battle_info_when_not_in_battle(self):
        reader = MockReader()
        state = build_game_state(reader)
        summary = build_state_summary(state)

        assert "Not in battle" in summary

    def test_renders_battle_info_when_in_battle(self):
        reader = MockReader()
        state = build_game_state(reader)
        state["battle"] = {
            "in_battle": True,
            "type": "wild",
            "enemy": {
                "species": "Rattata",
                "level": 3,
                "hp": 15,
                "max_hp": 15,
                "status": "OK",
                "moves": ["Tackle"],
            },
        }
        summary = build_state_summary(state)

        assert "wild" in summary
        assert "Rattata" in summary

    def test_renders_bag_items(self):
        reader = MockReader()
        state = build_game_state(reader)
        summary = build_state_summary(state)

        assert "Potion" in summary
        assert "Poke Ball" in summary

    def test_renders_flags(self):
        reader = MockReader()
        state = build_game_state(reader)
        summary = build_state_summary(state)

        assert "Has Pokedex" in summary
        assert "Pokedex owned" in summary
        assert "10" in summary  # owned count

    def test_handles_error_sections(self):
        """Summary should show errors for failed sections."""
        state = {
            "metadata": {"game": "Test"},
            "player": None,
            "player_error": "Something went wrong",
        }
        summary = build_state_summary(state)

        assert "Player read error" in summary
        assert "Something went wrong" in summary

    def test_empty_state(self):
        """Should handle mostly empty state."""
        state = {
            "metadata": {"game": "Test"},
        }
        summary = build_state_summary(state)

        assert "GAME STATE SNAPSHOT" in summary
        assert "Test" in summary

    def test_dialog_active(self):
        """Should show dialog section when active."""
        reader = MockReader()
        state = build_game_state(reader)
        state["dialog"] = {"active": True}
        summary = build_state_summary(state)

        assert "DIALOG" in summary

    def test_party_with_string_moves(self):
        """Handle moves that are strings instead of dicts."""
        reader = MockReader()
        state = build_game_state(reader)
        state["party"][0]["moves"] = ["Tackle", "Growl"]
        summary = build_state_summary(state)

        assert "Tackle" in summary
        assert "Growl" in summary
