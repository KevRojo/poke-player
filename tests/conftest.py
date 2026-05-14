"""Pytest fixtures and configuration."""

import pytest


@pytest.fixture
def sample_collision_map():
    """A simple 3x3 walkable grid for pathfinding tests."""
    return {
        (0, 0): True,
        (1, 0): True,
        (2, 0): True,
        (0, 1): True,
        (1, 1): True,
        (2, 1): True,
        (0, 2): True,
        (1, 2): True,
        (2, 2): True,
    }


@pytest.fixture
def collision_map_with_wall():
    """A 3x3 grid with a wall in the middle."""
    return {
        (0, 0): True,
        (1, 0): True,
        (2, 0): True,
        (0, 1): True,
        (1, 1): False,  # wall
        (2, 1): True,
        (0, 2): True,
        (1, 2): True,
        (2, 2): True,
    }
