"""
Create a clean, clearly-labelled synthetic floor plan fixture.

This is a development fixture only: it is not a real property and is never
served as a user's reconstruction. It exists so the parser, geometry builder
and viewer can be tested end-to-end without external assets.

Layout: four rooms in a 12 m x 9 m footprint, 8 px walls, four 60 px doorways
(~0.82 m at 73.3 px/m) forming a loop.
"""
import cv2
import numpy as np
from pathlib import Path

CANVAS_W, CANVAS_H = 1000, 800
WALL = 8

# Footprint outer bounds (pixels). 880 x 660 px at 73.33 px/m = 12 m x 9 m.
X0, Y0, X1, Y1 = 60, 60, 940, 720
DOOR = 60

# Interior wall centrelines.
MID_X = 500
MID_Y = 390

# Door openings as (start, end) along the wall's run axis.
LEFT_H_DOOR = (160, 160 + DOOR)      # kitchen <-> living
RIGHT_H_DOOR = (760, 760 + DOOR)     # bedroom <-> bathroom
TOP_V_DOOR = (150, 150 + DOOR)       # kitchen <-> bedroom
BOTTOM_V_DOOR = (520, 520 + DOOR)    # living <-> bathroom


def _wall_h(canvas, y_center, x_start, x_end, gaps=()):
    half = WALL // 2
    y0, y1 = y_center - half, y_center + half
    cursor = x_start
    for gap_start, gap_end in gaps:
        if gap_start > cursor:
            cv2.rectangle(canvas, (cursor, y0), (gap_start, y1), (0, 0, 0), -1)
        cursor = max(cursor, gap_end)
    if cursor < x_end:
        cv2.rectangle(canvas, (cursor, y0), (x_end, y1), (0, 0, 0), -1)


def _wall_v(canvas, x_center, y_start, y_end, gaps=()):
    half = WALL // 2
    x0, x1 = x_center - half, x_center + half
    cursor = y_start
    for gap_start, gap_end in gaps:
        if gap_start > cursor:
            cv2.rectangle(canvas, (x0, cursor), (x1, gap_start), (0, 0, 0), -1)
        cursor = max(cursor, gap_end)
    if cursor < y_end:
        cv2.rectangle(canvas, (x0, cursor), (x1, y_end), (0, 0, 0), -1)


def create_test_floor_plan(output_path: str):
    canvas = np.ones((CANVAS_H, CANVAS_W, 3), dtype=np.uint8) * 255

    # Outer walls (continuous, no entry door: the fixture models an interior
    # loop so navigation tests exercise all four connections).
    _wall_h(canvas, Y0 + WALL // 2, X0, X1)
    _wall_h(canvas, Y1 - WALL // 2, X0, X1)
    _wall_v(canvas, X0 + WALL // 2, Y0, Y1)
    _wall_v(canvas, X1 - WALL // 2, Y0, Y1)

    # Interior walls.
    _wall_h(canvas, MID_Y, X0 + WALL, X1 - WALL, gaps=(LEFT_H_DOOR, RIGHT_H_DOOR))
    _wall_v(canvas, MID_X, Y0 + WALL, Y1 - WALL, gaps=(TOP_V_DOOR, BOTTOM_V_DOOR))

    # Light-grey labels and dimension hints: intentionally lighter than the
    # Otsu wall threshold so they are not treated as geometry.
    hint = (205, 205, 205)
    font = cv2.FONT_HERSHEY_SIMPLEX

    def label(text, x, y, scale=0.7):
        cv2.putText(canvas, text, (x, y), font, scale, hint, 1, cv2.LINE_AA)

    label("Kitchen", 210, 230)
    label("Bedroom", 640, 230)
    label("Living Room", 170, 560)
    label("Bath", 660, 560)

    label("12 m", 470, 755, 0.55)
    label("9 m", 15, 400, 0.55)

    # Dimension ticks.
    cv2.line(canvas, (X0, 738), (X1, 738), hint, 1)
    cv2.line(canvas, (X0, 730), (X0, 746), hint, 1)
    cv2.line(canvas, (X1, 730), (X1, 746), hint, 1)
    cv2.line(canvas, (450, Y1 + 5), (450, Y1 + 30), hint, 1)
    cv2.line(canvas, (540, Y1 + 5), (540, Y1 + 30), hint, 1)

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(output), canvas):
        raise RuntimeError(f"Could not write floor plan fixture to {output}")
    print(f"Created test floor plan: {output}")


def create_two_room_floor_plan(output_path: str):
    """Development fixture for the second E2E property: 8 m x 6 m, two rooms."""
    canvas = np.ones((600, 800, 3), dtype=np.uint8) * 255
    black = (0, 0, 0)
    x0, y0, x1, y1 = 60, 60, 740, 540
    cv2.rectangle(canvas, (x0, y0), (x1, y1), black, 10)
    cv2.rectangle(canvas, (395, y0), (405, 270), black, -1)
    cv2.rectangle(canvas, (395, 340), (405, y1), black, -1)

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(output), canvas):
        raise RuntimeError(f"Could not write two-room floor plan fixture to {output}")
    print(f"Created two-room test floor plan: {output}")


if __name__ == "__main__":
    create_test_floor_plan(str(Path(__file__).parent / "input" / "test_floor_plan.png"))
