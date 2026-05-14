"""Tests for pathfinding module."""

import pytest

from poke_player.pathfinding import (
    DIRECTIONS,
    find_path,
    manhattan,
    neighbors,
    directions_to_actions,
    navigate,
    path_length,
)


class TestManhattan:
    """Tests for the manhattan distance heuristic."""

    def test_same_point(self):
        assert manhattan((0, 0), (0, 0)) == 0

    def test_horizontal(self):
        assert manhattan((0, 0), (5, 0)) == 5

    def test_vertical(self):
        assert manhattan((0, 0), (0, 3)) == 3

    def test_diagonal(self):
        assert manhattan((0, 0), (3, 4)) == 7

    def test_negative_coords(self):
        assert manhattan((-1, -1), (2, 3)) == 7


class TestNeighbors:
    """Tests for the neighbors helper."""

    def test_no_collision_map(self):
        """Without collision map, all 4 neighbors are returned."""
        result = neighbors((0, 0))
        assert len(result) == 4
        # Check all directions are present
        directions = {d for _, d in result}
        assert directions == {"up", "down", "left", "right"}

    def test_with_walkable_grid(self, sample_collision_map):
        """Center of 3x3 grid has 4 neighbors."""
        result = neighbors((1, 1), sample_collision_map)
        assert len(result) == 4

    def test_corner_top_left(self, sample_collision_map):
        """Corner has only 2 neighbors."""
        result = neighbors((0, 0), sample_collision_map)
        directions = {d for _, d in result}
        assert directions == {"down", "right"}

    def test_with_wall(self, collision_map_with_wall):
        """Wall blocks movement."""
        result = neighbors((1, 1), collision_map_with_wall)
        directions = {d for _, d in result}
        # Can't move because (1,1) is a wall and we're testing from there
        # Actually, let's test from center-left where we can move around
        result = neighbors((0, 1), collision_map_with_wall)
        directions = {d for _, d in result}
        # Can't go right because (1,1) is a wall
        assert "right" not in directions
        # Can't go left because (-1,1) is not in collision_map (defaults to False)
        assert "left" not in directions
        assert "up" in directions
        assert "down" in directions

    def test_fully_blocked(self):
        """When surrounded by walls, no neighbors."""
        collision_map = {
            (0, 0): True,
            (1, 0): False,
            (-1, 0): False,
            (0, 1): False,
            (0, -1): False,
        }
        result = neighbors((0, 0), collision_map)
        assert len(result) == 0


class TestFindPath:
    """Tests for the A* pathfinding algorithm."""

    def test_same_start_and_goal(self):
        """Path from point to itself is empty."""
        assert find_path((0, 0), (0, 0)) == []

    def test_simple_horizontal(self):
        """Move right twice."""
        path = find_path((0, 0), (2, 0))
        assert path == ["right", "right"]

    def test_simple_vertical(self):
        """Move down twice."""
        path = find_path((0, 0), (0, 2))
        assert path == ["down", "down"]

    def test_diagonal_path(self):
        """Path to diagonal point."""
        path = find_path((0, 0), (1, 1))
        assert len(path) == 2
        assert set(path) == {"down", "right"}

    def test_with_collision_map(self, sample_collision_map):
        """Path respects walkable tiles."""
        path = find_path((0, 0), (2, 2), sample_collision_map)
        assert len(path) == 4
        # Path should be valid (only moves to adjacent tiles)
        pos = (0, 0)
        for direction in path:
            dx, dy = DIRECTIONS[direction]
            pos = (pos[0] + dx, pos[1] + dy)
            assert sample_collision_map.get(pos, False)
        assert pos == (2, 2)

    def test_path_around_wall(self, collision_map_with_wall):
        """Path goes around a wall."""
        path = find_path((0, 1), (2, 1), collision_map_with_wall)
        assert len(path) == 4  # Must go around the wall
        # Verify we don't step on the wall
        pos = (0, 1)
        for direction in path:
            dx, dy = DIRECTIONS[direction]
            pos = (pos[0] + dx, pos[1] + dy)
            assert pos != (1, 1), "Path should not go through the wall"

    def test_unreachable_goal(self):
        """When goal is unreachable, return empty list."""
        collision_map = {
            (0, 0): True,
            (1, 0): True,
            (2, 0): False,  # goal is unwalkable
        }
        path = find_path((0, 0), (2, 0), collision_map)
        assert path == []

    def test_max_iterations_limit(self):
        """Test that max_iterations prevents infinite loops."""
        # Large open grid with no collision map
        path = find_path((0, 0), (1000, 1000), max_iterations=100)
        # Should return empty because we hit the iteration limit
        assert path == []

    def test_complex_maze(self):
        """Test pathfinding through a more complex layout."""
        collision_map = {
            # Corridor
            (0, 0): True, (1, 0): True, (2, 0): True,
            (0, 1): False, (1, 1): True, (2, 1): False,
            (0, 2): True, (1, 2): True, (2, 2): True,
        }
        path = find_path((0, 0), (2, 2), collision_map)
        assert len(path) == 4
        pos = (0, 0)
        for direction in path:
            dx, dy = DIRECTIONS[direction]
            pos = (pos[0] + dx, pos[1] + dy)
            assert collision_map.get(pos, False)
        assert pos == (2, 2)


class TestDirectionsToActions:
    """Tests for converting directions to action strings."""

    def test_empty(self):
        assert directions_to_actions([]) == []

    def test_single(self):
        assert directions_to_actions(["up"]) == ["walk_up"]

    def test_multiple(self):
        directions = ["up", "right", "down"]
        expected = ["walk_up", "walk_right", "walk_down"]
        assert directions_to_actions(directions) == expected


class TestNavigate:
    """Tests for the high-level navigate helper."""

    def test_navigate_returns_actions(self, sample_collision_map):
        actions = navigate((0, 0), (1, 1), sample_collision_map)
        assert all(a.startswith("walk_") for a in actions)
        assert len(actions) == 2

    def test_navigate_with_collision_map_unreachable(self):
        """When goal is blocked, returns empty list."""
        collision_map = {
            (0, 0): True,
            (1, 0): False,
            (0, 1): False,
        }
        actions = navigate((0, 0), (1, 1), collision_map)
        assert actions == []


class TestPathLength:
    """Tests for path_length helper."""

    def test_same_point(self):
        assert path_length((0, 0), (0, 0)) == 0

    def test_reachable(self, sample_collision_map):
        assert path_length((0, 0), (2, 2), sample_collision_map) == 4

    def test_unreachable(self):
        collision_map = {(0, 0): True, (1, 0): False}
        assert path_length((0, 0), (1, 0), collision_map) == -1
