"""Generate the second browser-E2E property fixture.

Development/testing only. These files are uploaded as a second, visibly
different property to prove that jobs do not share geometry or textures.
"""
from pathlib import Path

from create_test_floor_plan import create_two_room_floor_plan
from create_test_photos import create_palette_photo


def create_second_property(input_dir: Path):
    create_two_room_floor_plan(str(input_dir / "e2e_two_room_plan.png"))
    create_palette_photo(
        str(input_dir / "e2e_beta_hall.jpg"),
        wall_rgb=(52, 68, 84),
        floor_rgb=(120, 82, 50),
        accent_rgb=(30, 30, 30),
        accent_box=(60, 170, 220, 260),
    )
    create_palette_photo(
        str(input_dir / "e2e_beta_study.jpg"),
        wall_rgb=(120, 90, 60),
        floor_rgb=(60, 50, 40),
        accent_rgb=(210, 190, 120),
        accent_box=(300, 90, 430, 200),
    )


if __name__ == "__main__":
    input_dir = Path(__file__).parent / "input"
    input_dir.mkdir(exist_ok=True)
    create_second_property(input_dir)
