"""
Simple robot movement for week 3: the "greedy step".

Each tick, the robot tries to move one cell closer to its target.
It does NOT plan a route, so it can get stuck behind shelves or other
robots. That is expected: in week 4, A* path planning replaces this,
and we can measure how much better it is.
"""


def manhattan(a, b):
    """Grid distance between two cells: steps up/down plus steps left/right."""
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def greedy_directions(position, target):
    """
    Directions that bring the robot closer to the target, best first.
    The axis with the bigger distance is tried first.
    """
    d_row = target[0] - position[0]
    d_col = target[1] - position[1]

    vertical = "down" if d_row > 0 else "up"
    horizontal = "right" if d_col > 0 else "left"

    options = []
    if abs(d_row) >= abs(d_col):
        if d_row != 0:
            options.append(vertical)
        if d_col != 0:
            options.append(horizontal)
    else:
        if d_col != 0:
            options.append(horizontal)
        if d_row != 0:
            options.append(vertical)
    return options


def greedy_step(robot, target, warehouse):
    """
    Try to move the robot one cell closer to the target.
    Returns (moved, reason). moved is False when the robot is stuck this tick.
    """
    if robot.position == target:
        return False, "already at target"

    last_reason = "no direction"
    for direction in greedy_directions(robot.position, target):
        moved, reason = robot.move(direction, warehouse)
        if moved:
            return True, direction
        last_reason = reason
    return False, last_reason


def nearest(position, cells):
    """The cell closest to position. Ties are broken by (row, col) so runs repeat."""
    return min(cells, key=lambda cell: (manhattan(position, cell), cell)) if cells else None