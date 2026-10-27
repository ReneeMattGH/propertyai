"""Reusable procedural 3D furniture assets.

Every asset is built from original trimesh primitives with real-world
dimensions in metres. Assets are shared between rooms and styles: placement
only chooses transforms and material palettes, so the same library is reused
for every generated property. No external or unlicensed models are required.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np
import trimesh
from shapely.geometry import box as shapely_box
from trimesh.visual.material import PBRMaterial

STYLES = ("modern", "minimalist", "scandinavian", "warm_luxury")
STYLE_LABELS = {
    "modern": "Modern Contemporary",
    "minimalist": "Minimalist",
    "scandinavian": "Scandinavian",
    "warm_luxury": "Warm Luxury",
}

# Semantic material palettes. Colours are sRGB hex values; the exporter
# converts them to linear factors for the glTF PBR material.
PALETTES: Dict[str, Dict[str, str]] = {
    "modern": {
        "wood": "#7d5a3c",
        "wood_light": "#a9825e",
        "wood_dark": "#4f3826",
        "fabric": "#59616b",
        "fabric_light": "#8a929b",
        "fabric_dark": "#3d444c",
        "bedding": "#dedbd3",
        "cushion": "#b7a98f",
        "rug": "#9a9186",
        "metal": "#9aa0a5",
        "metal_dark": "#4b4f53",
        "glass": "#a9c4d2",
        "ceramic": "#eceae4",
        "stone": "#77756d",
        "counter": "#3f464d",
        "cabinet": "#454d56",
        "appliance": "#c9cac7",
        "plant": "#3f6b46",
        "soil": "#4a3a2c",
        "screen": "#0e1216",
        "art": "#496b7a",
        "accent": "#2f6f6f",
        "light": "#f6e6c8",
    },
    "minimalist": {
        "wood": "#b6a187",
        "wood_light": "#cdbb9f",
        "wood_dark": "#8c7860",
        "fabric": "#d6d1c7",
        "fabric_light": "#e6e2da",
        "fabric_dark": "#b9b3a8",
        "bedding": "#f0eee8",
        "cushion": "#c9c2b4",
        "rug": "#d6cfc2",
        "metal": "#b7b7b2",
        "metal_dark": "#777772",
        "glass": "#c6d8df",
        "ceramic": "#f4f2ec",
        "stone": "#c7c3b9",
        "counter": "#e6e2da",
        "cabinet": "#ece8e0",
        "appliance": "#deddd8",
        "plant": "#4d7254",
        "soil": "#5a4735",
        "screen": "#15191c",
        "art": "#9ba9a2",
        "accent": "#d9d2c4",
        "light": "#fbf2df",
    },
    "scandinavian": {
        "wood": "#c49a6c",
        "wood_light": "#d8b78b",
        "wood_dark": "#9a744c",
        "fabric": "#dfe3dc",
        "fabric_light": "#eef0ea",
        "fabric_dark": "#b9c0b6",
        "bedding": "#f3efe6",
        "cushion": "#c7b18e",
        "rug": "#ded6c6",
        "metal": "#a3aaa8",
        "metal_dark": "#5c6361",
        "glass": "#c3d8dd",
        "ceramic": "#f5f2ea",
        "stone": "#b9b4a8",
        "counter": "#eee9df",
        "cabinet": "#f0ece3",
        "appliance": "#e4e2dc",
        "plant": "#4c7a55",
        "soil": "#5a4735",
        "screen": "#111619",
        "art": "#8ea79a",
        "accent": "#9fb3a3",
        "light": "#fbf0d8",
    },
    "warm_luxury": {
        "wood": "#6b4a2f",
        "wood_light": "#8d6a45",
        "wood_dark": "#3f2f20",
        "fabric": "#5c5f55",
        "fabric_light": "#cfc3ae",
        "fabric_dark": "#3b4038",
        "bedding": "#efe7d6",
        "cushion": "#b78d5d",
        "rug": "#a8967c",
        "metal": "#c0a061",
        "metal_dark": "#836a3d",
        "glass": "#b8cfd1",
        "ceramic": "#efece3",
        "stone": "#7b7468",
        "counter": "#2e3732",
        "cabinet": "#33423a",
        "appliance": "#cfccc4",
        "plant": "#3c6247",
        "soil": "#43342a",
        "screen": "#0c1113",
        "art": "#7c5a3a",
        "accent": "#9a6b3f",
        "light": "#ffe2b0",
    },
}


def _hex_to_rgba(value: str, alpha: float = 1.0) -> List[float]:
    value = value.lstrip("#")
    return [
        int(value[0:2], 16) / 255.0,
        int(value[2:4], 16) / 255.0,
        int(value[4:6], 16) / 255.0,
        float(alpha),
    ]


@dataclass
class Part:
    name: str
    mesh: trimesh.Trimesh
    material: str


@dataclass
class AssetSpec:
    asset_id: str
    label: str
    size: Tuple[float, float, float]  # width (x), height (y), depth (z)
    builder: Callable[[str], List[Part]]
    collision: bool = True
    category: str = "furniture"
    description: str = ""


def _rounded_box_mesh(
    size: Tuple[float, float, float], radius: float = 0.01, segments: int = 4
) -> trimesh.Trimesh:
    """Box with rounded vertical edges.

    Real upholstery, casegoods and tabletops have a small edge radius; an
    unbroken cube reads as a rendering placeholder. The rounded outline is
    extruded to the exact requested height, so dimensions stay unchanged.
    """
    width, height, depth = size
    limit = min(width, depth) * 0.24
    radius = min(radius, limit)
    if radius <= 0.002 or min(size) < 0.03:
        return trimesh.creation.box(extents=size)
    outline = shapely_box(-width / 2, -depth / 2, width / 2, depth / 2)
    try:
        outline = outline.buffer(-radius, resolution=segments, join_style=1)
        outline = outline.buffer(radius, resolution=segments, join_style=1)
        if outline.is_empty:
            return trimesh.creation.box(extents=size)
        mesh = trimesh.creation.extrude_polygon(outline, height=height)
    except Exception:
        return trimesh.creation.box(extents=size)
    mesh.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2, [1, 0, 0]))
    mesh.apply_translation([0.0, height / 2, 0.0])
    return mesh


def _box(
    parts: List[Part],
    name: str,
    material: str,
    size: Tuple[float, float, float],
    center: Tuple[float, float, float],
) -> None:
    mesh = _rounded_box_mesh(size)
    mesh.apply_translation(center)
    parts.append(Part(name=name, mesh=mesh, material=material))


def _cylinder(
    parts: List[Part],
    name: str,
    material: str,
    radius: float,
    height: float,
    center: Tuple[float, float, float],
    axis: str = "y",
    sections: int = 16,
) -> None:
    mesh = trimesh.creation.cylinder(radius=radius, height=height, sections=sections)
    # trimesh builds cylinders along Z; rotate so the requested axis is vertical
    # or horizontal as intended.
    if axis == "y":
        mesh.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2, [1, 0, 0]))
    elif axis == "x":
        mesh.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2, [0, 1, 0]))
    mesh.apply_translation(center)
    parts.append(Part(name=name, mesh=mesh, material=material))


def _ellipsoid(
    parts: List[Part],
    name: str,
    material: str,
    size: Tuple[float, float, float],
    center: Tuple[float, float, float],
    subdivisions: int = 2,
) -> None:
    mesh = trimesh.creation.icosphere(subdivisions=subdivisions, radius=1.0)
    mesh.apply_scale([size[0] / 2, size[1] / 2, size[2] / 2])
    mesh.apply_translation(center)
    parts.append(Part(name=name, mesh=mesh, material=material))


def _superellipsoid_mesh(
    size: Tuple[float, float, float],
    exponent: float = 0.4,
    rings: int = 7,
    segments: int = 18,
) -> trimesh.Trimesh:
    """Cushion form: flat faces with genuinely rounded edges.

    A bevelled cube still reads as a box from every angle. A superellipsoid
    keeps the authored outer dimensions but curves the top, sides and corners
    the way foam and filling do.
    """
    half = [max(dimension, 0.002) / 2.0 for dimension in size]

    def signed_power(value: float, power: float) -> float:
        return math.copysign(abs(value) ** power, value)

    vertices: List[List[float]] = [[0.0, -half[1], 0.0]]
    for ring in range(1, rings):
        v = -math.pi / 2 + math.pi * ring / rings
        cv = signed_power(math.cos(v), exponent)
        sv = signed_power(math.sin(v), exponent)
        for segment in range(segments):
            u = -math.pi + 2 * math.pi * segment / segments
            vertices.append(
                [
                    half[0] * cv * signed_power(math.cos(u), exponent),
                    half[1] * sv,
                    half[2] * cv * signed_power(math.sin(u), exponent),
                ]
            )
    top = len(vertices)
    vertices.append([0.0, half[1], 0.0])

    faces: List[List[int]] = []
    for segment in range(segments):
        following = (segment + 1) % segments
        faces.append([0, 1 + following, 1 + segment])
        for ring in range(rings - 2):
            row = 1 + ring * segments
            next_row = row + segments
            faces.append([row + segment, row + following, next_row + following])
            faces.append([row + segment, next_row + following, next_row + segment])
        last = 1 + (rings - 2) * segments
        faces.append([last + segment, top, last + following])

    mesh = trimesh.Trimesh(
        vertices=np.asarray(vertices, dtype=np.float64),
        faces=np.asarray(faces, dtype=np.int64),
        process=True,
    )
    mesh.fix_normals()
    return mesh


def _cushion(
    parts: List[Part],
    name: str,
    material: str,
    size: Tuple[float, float, float],
    center: Tuple[float, float, float],
    exponent: float = 0.4,
) -> None:
    mesh = _superellipsoid_mesh(size, exponent)
    mesh.apply_translation(center)
    parts.append(Part(name=name, mesh=mesh, material=material))


def _legs(
    parts: List[Part],
    material: str,
    width: float,
    depth: float,
    height: float,
    inset: float = 0.09,
    radius: float = 0.025,
) -> None:
    for index, (sx, sz) in enumerate(
        [(-1, -1), (1, -1), (-1, 1), (1, 1)]
    ):
        _cylinder(
            parts,
            f"leg_{index}",
            material,
            radius,
            height,
            (sx * (width / 2 - inset), height / 2, sz * (depth / 2 - inset)),
            sections=10,
        )


# ---------------------------------------------------------------------------
# Living room
# ---------------------------------------------------------------------------
def _build_sofa(style: str) -> List[Part]:
    parts: List[Part] = []
    width, depth = 2.2, 0.92
    _legs(parts, "wood_dark", width, depth, 0.12, inset=0.16)
    _box(parts, "base", "fabric", (width, 0.24, depth), (0, 0.24, 0))
    _box(parts, "back_frame", "fabric", (width, 0.58, 0.18), (0, 0.58, -depth / 2 + 0.09))
    for side in (-1, 1):
        _box(
            parts,
            f"arm_{'l' if side < 0 else 'r'}",
            "fabric_dark",
            (0.17, 0.46, depth),
            (side * (width / 2 - 0.085), 0.47, 0),
        )
    for index, offset in enumerate((-0.53, 0.53)):
        _cushion(
            parts,
            f"seat_cushion_{index}",
            "fabric_light",
            (0.96, 0.18, 0.74),
            (offset, 0.43, 0.05),
        )
        _cushion(
            parts,
            f"back_cushion_{index}",
            "fabric_light",
            (0.94, 0.4, 0.18),
            (offset, 0.65, -depth / 2 + 0.2),
        )
    for index, offset in enumerate((-0.68, 0.68)):
        _cushion(
            parts,
            f"throw_pillow_{index}",
            "cushion",
            (0.34, 0.3, 0.14),
            (offset, 0.6, -0.18),
            exponent=0.5,
        )
    _box(parts, "plinth", "wood_dark", (width - 0.2, 0.06, depth - 0.2), (0, 0.15, 0))
    return parts


def _build_sectional(style: str) -> List[Part]:
    parts: List[Part] = []
    width, depth = 2.9, 1.75
    _legs(parts, "wood_dark", 2.2, 0.95, 0.12, inset=0.2)
    _box(parts, "base_main", "fabric", (2.2, 0.24, 0.95), (-0.35, 0.24, -0.4))
    _box(parts, "base_chaise", "fabric", (0.85, 0.24, 1.7), (0.98, 0.24, 0.03))
    _box(parts, "back", "fabric", (2.2, 0.58, 0.18), (-0.35, 0.58, -0.79))
    _box(parts, "arm_left", "fabric_dark", (0.17, 0.46, 0.95), (-1.37, 0.47, -0.4))
    _box(parts, "arm_right", "fabric_dark", (0.17, 0.46, 0.95), (0.72, 0.47, -0.4))
    for index, offset in enumerate((-0.85, -0.35)):
        _cushion(
            parts,
            f"seat_cushion_{index}",
            "fabric_light",
            (0.96, 0.18, 0.74),
            (offset, 0.43, -0.35),
        )
        _cushion(
            parts,
            f"back_cushion_{index}",
            "fabric_light",
            (0.9, 0.4, 0.18),
            (offset, 0.65, -0.62),
        )
    _cushion(parts, "chaise_cushion", "fabric_light", (0.72, 0.18, 1.5), (0.98, 0.43, 0.05))
    for index, offset in enumerate((-0.98, -0.62)):
        _cushion(
            parts,
            f"throw_pillow_{index}",
            "cushion",
            (0.32, 0.29, 0.14),
            (offset, 0.6, -0.52),
            exponent=0.5,
        )
    _box(parts, "plinth", "wood_dark", (2.0, 0.06, 0.75), (-0.35, 0.15, -0.4))
    return parts


def _build_armchair(style: str) -> List[Part]:
    parts: List[Part] = []
    width, depth = 0.9, 0.88
    _legs(parts, "wood_dark", width, depth, 0.13, inset=0.1)
    _box(parts, "base", "fabric", (width, 0.24, depth), (0, 0.25, 0))
    _box(parts, "back", "fabric", (width, 0.5, 0.16), (0, 0.6, -depth / 2 + 0.08))
    for side in (-1, 1):
        _box(
            parts,
            f"arm_{'l' if side < 0 else 'r'}",
            "fabric_dark",
            (0.12, 0.4, depth),
            (side * (width / 2 - 0.06), 0.49, 0),
        )
    _cushion(parts, "seat_cushion", "fabric_light", (0.62, 0.17, 0.62), (0, 0.42, 0.07))
    _cushion(parts, "back_cushion", "fabric_light", (0.6, 0.36, 0.16), (0, 0.63, -0.22))
    _cushion(
        parts,
        "throw_pillow",
        "cushion",
        (0.3, 0.27, 0.13),
        (0.16, 0.58, -0.16),
        exponent=0.5,
    )
    return parts


def _build_coffee_table(style: str) -> List[Part]:
    parts: List[Part] = []
    width, depth = 1.2, 0.65
    _box(parts, "top", "wood", (width, 0.055, depth), (0, 0.395, 0))
    _box(parts, "shelf", "wood_light", (width - 0.18, 0.035, depth - 0.14), (0, 0.16, 0))
    _legs(parts, "metal_dark", width, depth, 0.37, inset=0.08, radius=0.02)
    _box(parts, "book", "accent", (0.22, 0.035, 0.16), (-0.24, 0.44, 0.05))
    _cylinder(parts, "bowl", "ceramic", 0.09, 0.05, (0.28, 0.45, -0.04), sections=16)
    return parts


def _build_tv_stand(style: str) -> List[Part]:
    parts: List[Part] = []
    width, depth = 1.8, 0.44
    _legs(parts, "metal_dark", width, depth, 0.1, inset=0.1, radius=0.018)
    _box(parts, "body", "wood", (width, 0.34, depth), (0, 0.28, 0))
    _box(parts, "top", "wood_light", (width + 0.04, 0.04, depth + 0.03), (0, 0.47, 0))
    for index, offset in enumerate((-0.45, 0.45)):
        _box(parts, f"door_{index}", "wood_light", (0.84, 0.28, 0.02), (offset, 0.28, depth / 2))
        _box(parts, f"handle_{index}", "metal", (0.14, 0.015, 0.02), (offset, 0.4, depth / 2 + 0.02))
    _box(parts, "vent", "wood_dark", (0.5, 0.02, 0.01), (0, 0.16, depth / 2 + 0.01))
    return parts


def _build_television(style: str) -> List[Part]:
    parts: List[Part] = []
    _box(parts, "base", "metal_dark", (0.36, 0.025, 0.2), (0, 0.012, 0))
    _box(parts, "neck", "metal_dark", (0.06, 0.16, 0.04), (0, 0.1, -0.03))
    _box(parts, "panel", "screen", (1.24, 0.72, 0.045), (0, 0.42, 0))
    _box(parts, "frame", "metal_dark", (1.28, 0.76, 0.02), (0, 0.42, -0.032))
    _box(parts, "screen_glow", "art", (1.18, 0.66, 0.005), (0, 0.42, 0.026))
    return parts


def _build_rug(style: str) -> List[Part]:
    parts: List[Part] = []
    _box(parts, "rug", "rug", (2.6, 0.016, 1.7), (0, 0.008, 0))
    for side in (-1, 1):
        _box(parts, f"border_x_{side}", "cushion", (0.055, 0.004, 1.7), (side * 1.27, 0.018, 0))
        _box(parts, f"border_z_{side}", "cushion", (2.6, 0.004, 0.055), (0, 0.018, side * 0.82))
    return parts


def _build_floor_lamp(style: str) -> List[Part]:
    parts: List[Part] = []
    _cylinder(parts, "base", "metal_dark", 0.16, 0.03, (0, 0.015, 0), sections=20)
    _cylinder(parts, "pole", "metal", 0.018, 1.5, (0, 0.78, 0), sections=10)
    _cylinder(parts, "shade", "light", 0.2, 0.26, (0, 1.58, 0), sections=20)
    return parts


def _build_plant(style: str) -> List[Part]:
    parts: List[Part] = []
    _cylinder(parts, "pot", "ceramic", 0.19, 0.32, (0, 0.16, 0), sections=18)
    _cylinder(parts, "soil", "soil", 0.16, 0.02, (0, 0.325, 0), sections=18)
    _cylinder(parts, "stem", "wood_dark", 0.018, 0.55, (0, 0.6, 0), sections=8)
    for index, (x, z, y, sx) in enumerate(
        [(-0.12, 0.02, 0.78, 0.2), (0.12, -0.05, 0.9, 0.22), (0.02, 0.12, 1.02, 0.18)]
    ):
        _ellipsoid(parts, f"leaf_{index}", "plant", (sx, 0.06, sx * 0.7), (x, y, z))
    return parts


# ---------------------------------------------------------------------------
# Bedroom
# ---------------------------------------------------------------------------
def _build_bed(width: float, depth: float = 2.05):
    def builder(style: str) -> List[Part]:
        parts: List[Part] = []
        _legs(parts, "wood_dark", width, depth, 0.12, inset=0.1, radius=0.03)
        _box(parts, "frame", "wood", (width, 0.28, depth), (0, 0.26, 0))
        _cushion(parts, "mattress", "bedding", (width - 0.08, 0.22, depth - 0.1), (0, 0.51, 0.02), exponent=0.3)
        _cushion(
            parts,
            "duvet",
            "fabric_light",
            (width - 0.02, 0.14, depth * 0.62),
            (0, 0.64, 0.3),
            exponent=0.28,
        )
        if width >= 1.4:
            for index, offset in enumerate((-width / 4, width / 4)):
                _cushion(
                    parts,
                    f"pillow_{index}",
                    "bedding",
                    (0.55, 0.16, 0.36),
                    (offset, 0.7, -depth / 2 + 0.35),
                    exponent=0.5,
                )
        else:
            _cushion(
                parts,
                "pillow_0",
                "bedding",
                (0.6, 0.16, 0.36),
                (0, 0.7, -depth / 2 + 0.35),
                exponent=0.5,
            )
        _box(parts, "headboard", "fabric", (width + 0.08, 0.95, 0.09), (0, 0.62, -depth / 2 - 0.01))
        _cushion(
            parts,
            "throw",
            "cushion",
            (width - 0.06, 0.09, 0.44),
            (0, 0.72, depth / 2 - 0.55),
            exponent=0.35,
        )
        return parts

    return builder


def _build_nightstand(style: str) -> List[Part]:
    parts: List[Part] = []
    width, depth = 0.48, 0.42
    _legs(parts, "wood_dark", width, depth, 0.14, inset=0.07, radius=0.02)
    _box(parts, "body", "wood", (width, 0.4, depth), (0, 0.34, 0))
    _box(parts, "top", "wood_light", (width + 0.03, 0.03, depth + 0.02), (0, 0.56, 0))
    _box(parts, "drawer", "wood_light", (width - 0.08, 0.16, 0.02), (0, 0.42, depth / 2 + 0.01))
    _box(parts, "handle", "metal", (0.1, 0.012, 0.02), (0, 0.42, depth / 2 + 0.03))
    return parts


def _build_bed_lamp(style: str) -> List[Part]:
    parts: List[Part] = []
    _cylinder(parts, "base", "metal", 0.07, 0.02, (0, 0.01, 0), sections=14)
    _cylinder(parts, "stem", "metal_dark", 0.012, 0.2, (0, 0.12, 0), sections=8)
    _cylinder(parts, "shade", "light", 0.11, 0.14, (0, 0.28, 0), sections=16)
    return parts


def _build_wardrobe(width: float):
    def builder(style: str) -> List[Part]:
        parts: List[Part] = []
        depth = 0.6
        _box(parts, "body", "wood", (width, 2.06, depth), (0, 1.06, 0))
        _box(parts, "plinth", "wood_dark", (width - 0.08, 0.09, depth - 0.05), (0, 0.045, 0))
        _box(parts, "top", "wood_light", (width + 0.02, 0.03, depth + 0.02), (0, 2.105, 0))
        leaves = 3 if width >= 2.0 else 2
        leaf_width = (width - 0.08) / leaves
        for index in range(leaves):
            offset = -width / 2 + 0.04 + leaf_width * (index + 0.5)
            _box(
                parts,
                f"door_{index}",
                "wood_light",
                (leaf_width - 0.015, 1.88, 0.022),
                (offset, 1.08, depth / 2 + 0.011),
            )
            _box(
                parts,
                f"handle_{index}",
                "metal",
                (0.015, 0.28, 0.02),
                (offset + leaf_width / 2 - 0.07, 1.05, depth / 2 + 0.035),
            )
        return parts

    return builder


def _build_desk(style: str) -> List[Part]:
    parts: List[Part] = []
    width, depth = 1.2, 0.6
    _box(parts, "top", "wood", (width, 0.045, depth), (0, 0.735, 0))
    _legs(parts, "metal_dark", width, depth, 0.71, inset=0.06, radius=0.02)
    _box(parts, "drawer_unit", "wood_light", (0.36, 0.5, depth - 0.06), (0.4, 0.46, 0))
    for index in range(3):
        _box(parts, f"drawer_{index}", "wood", (0.3, 0.1, 0.02), (0.4, 0.64 - index * 0.16, depth / 2 - 0.02))
    _box(parts, "monitor", "screen", (0.52, 0.32, 0.03), (-0.16, 0.93, -0.12))
    _box(parts, "monitor_stand", "metal_dark", (0.05, 0.12, 0.03), (-0.16, 0.83, -0.12))
    _box(parts, "monitor_base", "metal_dark", (0.22, 0.015, 0.14), (-0.16, 0.765, -0.08))
    _box(parts, "keyboard", "ceramic", (0.36, 0.018, 0.12), (-0.16, 0.767, 0.12))
    return parts


def _build_desk_chair(style: str) -> List[Part]:
    parts: List[Part] = []
    _cylinder(parts, "post", "metal_dark", 0.03, 0.36, (0, 0.2, 0), sections=10)
    for index in range(5):
        angle = index * (2 * np.pi / 5)
        _box(
            parts,
            f"spoke_{index}",
            "metal_dark",
            (0.3, 0.025, 0.04),
            (np.cos(angle) * 0.14, 0.04, np.sin(angle) * 0.14),
        )
    _box(parts, "seat", "fabric", (0.48, 0.07, 0.46), (0, 0.44, 0))
    _box(parts, "back", "fabric", (0.44, 0.45, 0.06), (0, 0.7, -0.22))
    return parts


# ---------------------------------------------------------------------------
# Kitchen
# ---------------------------------------------------------------------------
def _build_kitchen_counter(width: float):
    def builder(style: str) -> List[Part]:
        parts: List[Part] = []
        depth = 0.62
        _box(parts, "base", "cabinet", (width, 0.82, depth), (0, 0.46, 0))
        _box(parts, "toe_kick", "wood_dark", (width - 0.04, 0.1, depth - 0.08), (0, 0.05, 0.03))
        _box(parts, "countertop", "stone", (width + 0.04, 0.045, depth + 0.03), (0, 0.895, 0))
        modules = max(2, int(round(width / 0.6)))
        module_width = (width - 0.06) / modules
        for index in range(modules):
            offset = -width / 2 + 0.03 + module_width * (index + 0.5)
            _box(
                parts,
                f"door_{index}",
                "cabinet",
                (module_width - 0.02, 0.64, 0.02),
                (offset, 0.46, depth / 2 + 0.01),
            )
            _box(
                parts,
                f"handle_{index}",
                "metal",
                (0.12 if module_width > 0.5 else 0.02, 0.014, 0.02),
                (offset, 0.72, depth / 2 + 0.03),
            )
        _box(parts, "splashback", "stone", (width, 0.56, 0.03), (0, 1.2, -depth / 2 + 0.02))
        upper_width = width - 0.1
        _box(parts, "upper_cabinet", "cabinet", (upper_width, 0.62, 0.33), (0, 1.82, -depth / 2 + 0.17))
        upper_modules = max(2, int(round(upper_width / 0.6)))
        upper_module = (upper_width - 0.04) / upper_modules
        for index in range(upper_modules):
            offset = -upper_width / 2 + 0.02 + upper_module * (index + 0.5)
            _box(
                parts,
                f"upper_door_{index}",
                "cabinet",
                (upper_module - 0.015, 0.58, 0.02),
                (offset, 1.82, -depth / 2 + 0.34),
            )
            _box(
                parts,
                f"upper_handle_{index}",
                "metal",
                (0.012, 0.14, 0.02),
                (offset + upper_module / 2 - 0.06, 1.72, -depth / 2 + 0.37),
            )
        return parts

    return builder


def _build_kitchen_sink(style: str) -> List[Part]:
    parts: List[Part] = []
    _box(parts, "rim", "metal", (0.62, 0.03, 0.46), (0, 0.02, 0))
    _box(parts, "basin", "metal_dark", (0.54, 0.02, 0.38), (0, 0.035, 0))
    _box(parts, "inner", "appliance", (0.46, 0.012, 0.3), (0, 0.03, 0))
    _cylinder(parts, "faucet", "metal", 0.018, 0.34, (0, 0.2, -0.16), sections=10)
    _box(parts, "spout", "metal", (0.03, 0.025, 0.2), (0, 0.34, -0.07))
    return parts


def _build_kitchen_stove(style: str) -> List[Part]:
    parts: List[Part] = []
    _box(parts, "cooktop", "screen", (0.62, 0.02, 0.5), (0, 0.02, 0))
    for index, (x, z) in enumerate([(-0.16, -0.12), (0.16, -0.12), (-0.16, 0.13), (0.16, 0.13)]):
        _cylinder(parts, f"burner_{index}", "metal_dark", 0.085, 0.012, (x, 0.035, z), sections=16)
    _box(parts, "control_panel", "metal", (0.5, 0.06, 0.06), (0, 0.07, 0.21))
    for index in range(4):
        _cylinder(
            parts,
            f"knob_{index}",
            "metal_dark",
            0.018,
            0.02,
            (-0.18 + index * 0.12, 0.075, 0.245),
            axis="z",
            sections=10,
        )
    return parts


def _build_refrigerator(style: str) -> List[Part]:
    parts: List[Part] = []
    width, depth = 0.72, 0.7
    _box(parts, "body", "appliance", (width, 1.86, depth), (0, 0.95, 0))
    _box(parts, "freezer_line", "metal_dark", (width + 0.01, 0.02, 0.01), (0, 1.28, depth / 2 + 0.005))
    _box(parts, "handle_main", "metal", (0.025, 0.5, 0.03), (width / 2 - 0.08, 0.9, depth / 2 + 0.03))
    _box(parts, "handle_freezer", "metal", (0.025, 0.3, 0.03), (width / 2 - 0.08, 1.55, depth / 2 + 0.03))
    _box(parts, "plinth", "metal_dark", (width - 0.06, 0.06, depth - 0.06), (0, 0.05, 0))
    return parts


# ---------------------------------------------------------------------------
# Dining
# ---------------------------------------------------------------------------
def _build_dining_table(width: float, depth: float):
    def builder(style: str) -> List[Part]:
        parts: List[Part] = []
        _box(parts, "top", "wood", (width, 0.06, depth), (0, 0.72, 0))
        _legs(parts, "wood_dark", width, depth, 0.7, inset=0.12, radius=0.035)
        _box(parts, "apron", "wood_light", (width - 0.2, 0.08, depth - 0.2), (0, 0.66, 0))
        return parts

    return builder


def _build_round_table(style: str) -> List[Part]:
    parts: List[Part] = []
    _cylinder(parts, "top", "wood", 0.6, 0.055, (0, 0.72, 0), sections=28)
    _cylinder(parts, "pedestal", "wood_dark", 0.07, 0.62, (0, 0.36, 0), sections=14)
    _cylinder(parts, "base", "wood_dark", 0.32, 0.06, (0, 0.04, 0), sections=24)
    return parts


def _build_dining_chair(style: str) -> List[Part]:
    parts: List[Part] = []
    width, depth = 0.46, 0.52
    _legs(parts, "wood_dark", width, depth, 0.44, inset=0.06, radius=0.02)
    _box(parts, "seat", "wood", (width, 0.055, depth), (0, 0.47, 0))
    _ellipsoid(parts, "cushion", "cushion", (0.4, 0.05, 0.4), (0, 0.51, 0.02))
    _box(parts, "back", "wood_light", (width, 0.48, 0.055), (0, 0.72, -depth / 2 + 0.03))
    return parts


def _build_pendant(style: str) -> List[Part]:
    parts: List[Part] = []
    _cylinder(parts, "cord", "metal_dark", 0.008, 0.55, (0, 0.28, 0), sections=6)
    _cylinder(parts, "shade", "metal_dark", 0.19, 0.22, (0, -0.06, 0), sections=20)
    _cylinder(parts, "bulb", "light", 0.09, 0.1, (0, -0.14, 0), sections=16)
    return parts


def _build_sideboard(style: str) -> List[Part]:
    parts: List[Part] = []
    width, depth = 1.4, 0.45
    _legs(parts, "metal_dark", width, depth, 0.16, inset=0.08, radius=0.018)
    _box(parts, "body", "wood", (width, 0.62, depth), (0, 0.49, 0))
    _box(parts, "top", "wood_light", (width + 0.03, 0.03, depth + 0.02), (0, 0.815, 0))
    for index, offset in enumerate((-0.35, 0.35)):
        _box(parts, f"door_{index}", "wood_light", (0.66, 0.56, 0.02), (offset, 0.49, depth / 2 + 0.01))
        _box(parts, f"handle_{index}", "metal", (0.1, 0.014, 0.02), (offset, 0.68, depth / 2 + 0.03))
    _cylinder(parts, "vase", "ceramic", 0.08, 0.22, (-0.42, 0.94, 0), sections=16)
    return parts


# ---------------------------------------------------------------------------
# Bathroom
# ---------------------------------------------------------------------------
def _build_toilet(style: str) -> List[Part]:
    parts: List[Part] = []
    _box(parts, "base", "ceramic", (0.36, 0.32, 0.5), (0, 0.16, 0.04))
    _cylinder(parts, "pedestal", "ceramic", 0.14, 0.28, (0, 0.28, 0.08), sections=20)
    _ellipsoid(parts, "bowl", "ceramic", (0.4, 0.18, 0.52), (0, 0.4, 0.06), subdivisions=3)
    _box(parts, "tank", "ceramic", (0.42, 0.42, 0.2), (0, 0.62, -0.24))
    _box(parts, "lid", "ceramic", (0.4, 0.03, 0.5), (0, 0.49, 0.04))
    _box(parts, "button", "metal", (0.05, 0.015, 0.05), (0, 0.835, -0.24))
    return parts


def _build_vanity(style: str) -> List[Part]:
    parts: List[Part] = []
    width, depth = 0.9, 0.5
    _box(parts, "cabinet", "wood", (width, 0.72, depth), (0, 0.42, 0))
    _box(parts, "counter", "stone", (width + 0.04, 0.04, depth + 0.02), (0, 0.8, 0))
    _cylinder(parts, "basin", "ceramic", 0.19, 0.12, (0, 0.86, 0.02), sections=22)
    _cylinder(parts, "faucet", "metal", 0.016, 0.22, (0, 0.93, -0.16), sections=10)
    _box(parts, "spout", "metal", (0.025, 0.02, 0.14), (0, 1.04, -0.1))
    for index, offset in enumerate((-0.2, 0.2)):
        _box(parts, f"door_{index}", "wood_light", (0.36, 0.62, 0.02), (offset, 0.42, depth / 2 + 0.01))
        _box(parts, f"handle_{index}", "metal_dark", (0.012, 0.16, 0.02), (offset, 0.58, depth / 2 + 0.03))
    return parts


def _build_wall_mirror(style: str) -> List[Part]:
    parts: List[Part] = []
    _box(parts, "frame", "metal", (0.72, 0.92, 0.035), (0, 0.46, 0))
    _box(parts, "glass", "glass", (0.66, 0.86, 0.01), (0, 0.46, 0.024))
    return parts


def _build_shower(style: str) -> List[Part]:
    parts: List[Part] = []
    size = 0.9
    _box(parts, "tray", "ceramic", (size, 0.08, size), (0, 0.04, 0))
    _box(parts, "glass_front", "glass", (size, 1.9, 0.02), (0, 1.03, size / 2 - 0.02))
    _box(parts, "glass_side", "glass", (0.02, 1.9, size), (size / 2 - 0.02, 1.03, 0))
    _cylinder(parts, "column", "metal", 0.02, 1.9, (-size / 2 + 0.06, 1.03, -size / 2 + 0.06), sections=10)
    _box(parts, "arm", "metal", (0.04, 0.04, 0.35), (-size / 2 + 0.06, 1.95, -size / 2 + 0.22))
    _cylinder(parts, "head", "metal", 0.11, 0.02, (-size / 2 + 0.12, 1.92, -size / 2 + 0.4), sections=18)
    _cylinder(parts, "drain", "metal_dark", 0.05, 0.01, (0, 0.085, 0), sections=12)
    return parts


def _build_bathtub(style: str) -> List[Part]:
    parts: List[Part] = []
    width, depth = 1.7, 0.76
    _box(parts, "shell", "ceramic", (width, 0.55, depth), (0, 0.31, 0))
    _box(parts, "rim", "ceramic", (width + 0.04, 0.05, depth + 0.02), (0, 0.58, 0))
    _box(parts, "basin", "appliance", (width - 0.28, 0.06, depth - 0.22), (0, 0.565, 0))
    _cylinder(parts, "faucet", "metal", 0.018, 0.3, (width / 2 - 0.12, 0.73, -depth / 2 + 0.12), sections=10)
    _box(parts, "spout", "metal", (0.14, 0.025, 0.025), (width / 2 - 0.2, 0.88, -depth / 2 + 0.12))
    _box(parts, "drain", "metal_dark", (0.1, 0.01, 0.1), (0, 0.6, 0.1))
    return parts


def _build_towel_rail(style: str) -> List[Part]:
    parts: List[Part] = []
    _box(parts, "rail", "metal", (0.6, 0.02, 0.02), (0, 0, 0))
    _box(parts, "towel", "fabric_light", (0.32, 0.34, 0.03), (0, -0.17, 0.01))
    return parts


# ---------------------------------------------------------------------------
# Entrance / hallway / balcony / decor
# ---------------------------------------------------------------------------
def _build_shoe_cabinet(style: str) -> List[Part]:
    parts: List[Part] = []
    width, depth = 1.0, 0.35
    _box(parts, "body", "wood", (width, 0.84, depth), (0, 0.45, 0))
    _box(parts, "top", "wood_light", (width + 0.03, 0.03, depth + 0.02), (0, 0.885, 0))
    _legs(parts, "metal_dark", width, depth, 0.12, inset=0.06, radius=0.015)
    for index, offset in enumerate((-0.24, 0.24)):
        _box(parts, f"door_{index}", "wood_light", (0.46, 0.7, 0.02), (offset, 0.45, depth / 2 + 0.01))
        _box(parts, f"handle_{index}", "metal", (0.1, 0.014, 0.02), (offset, 0.62, depth / 2 + 0.03))
    return parts


def _build_coat_rack(style: str) -> List[Part]:
    parts: List[Part] = []
    _cylinder(parts, "base", "metal_dark", 0.18, 0.03, (0, 0.015, 0), sections=18)
    _cylinder(parts, "pole", "metal", 0.02, 1.68, (0, 0.86, 0), sections=10)
    for index in range(3):
        angle = index * (2 * np.pi / 3)
        _box(
            parts,
            f"hook_{index}",
            "metal_dark",
            (0.14, 0.02, 0.02),
            (np.cos(angle) * 0.06, 1.58, np.sin(angle) * 0.06),
        )
    _box(parts, "coat", "fabric_dark", (0.28, 0.6, 0.12), (0.08, 1.24, 0.04))
    return parts


def _build_entry_bench(style: str) -> List[Part]:
    parts: List[Part] = []
    width, depth = 0.9, 0.36
    _legs(parts, "wood_dark", width, depth, 0.4, inset=0.07, radius=0.02)
    _box(parts, "seat", "wood", (width, 0.06, depth), (0, 0.43, 0))
    _ellipsoid(parts, "cushion", "cushion", (0.78, 0.07, 0.3), (0, 0.49, 0))
    return parts


def _build_balcony_chair(style: str) -> List[Part]:
    parts: List[Part] = []
    width, depth = 0.58, 0.6
    _legs(parts, "metal_dark", width, depth, 0.4, inset=0.06, radius=0.015)
    _box(parts, "seat", "wood_light", (width, 0.05, depth), (0, 0.42, 0))
    _box(parts, "back", "wood_light", (width, 0.42, 0.05), (0, 0.63, -depth / 2 + 0.03))
    _ellipsoid(parts, "cushion", "fabric_light", (0.46, 0.05, 0.44), (0, 0.46, 0.02))
    return parts


def _build_balcony_table(style: str) -> List[Part]:
    parts: List[Part] = []
    _cylinder(parts, "top", "wood", 0.25, 0.04, (0, 0.48, 0), sections=20)
    _cylinder(parts, "pole", "metal_dark", 0.03, 0.46, (0, 0.24, 0), sections=10)
    _cylinder(parts, "base", "metal_dark", 0.16, 0.03, (0, 0.015, 0), sections=16)
    return parts


def _build_art_frame(style: str) -> List[Part]:
    parts: List[Part] = []
    _box(parts, "frame", "wood_dark", (0.64, 0.48, 0.03), (0, 0, 0))
    _box(parts, "mat", "ceramic", (0.58, 0.42, 0.012), (0, 0, 0.02))
    _box(parts, "canvas", "art", (0.46, 0.31, 0.006), (0, 0, 0.029))
    return parts


# ---------------------------------------------------------------------------
# Asset registry
# ---------------------------------------------------------------------------
ASSETS: Dict[str, AssetSpec] = {
    "sofa": AssetSpec("sofa", "Sofa", (2.2, 0.88, 0.92), _build_sofa, category="seating"),
    "sectional": AssetSpec(
        "sectional", "Sectional sofa", (2.86, 0.88, 1.76), _build_sectional, category="seating"
    ),
    "armchair": AssetSpec("armchair", "Armchair", (0.9, 0.85, 0.88), _build_armchair, category="seating"),
    "coffee_table": AssetSpec(
        "coffee_table", "Coffee table", (1.2, 0.475, 0.65), _build_coffee_table, category="table"
    ),
    "tv_stand": AssetSpec("tv_stand", "TV stand", (1.84, 0.49, 0.485), _build_tv_stand, category="storage"),
    "television": AssetSpec(
        "television", "Television", (1.28, 0.8, 0.2), _build_television, collision=False, category="appliance"
    ),
    "rug": AssetSpec("rug", "Rug", (2.6, 0.025, 1.7), _build_rug, collision=False, category="decor"),
    "floor_lamp": AssetSpec(
        "floor_lamp", "Floor lamp", (0.4, 1.71, 0.4), _build_floor_lamp, category="lighting"
    ),
    "plant": AssetSpec("plant", "Plant", (0.45, 1.05, 0.374), _build_plant, category="decor"),
    "bed_single": AssetSpec("bed_single", "Single bed", (1.06, 1.095, 2.105), _build_bed(0.98)),
    "bed_double": AssetSpec("bed_double", "Double bed", (1.53, 1.095, 2.105), _build_bed(1.45)),
    "bed_queen": AssetSpec("bed_queen", "Queen bed", (1.73, 1.095, 2.105), _build_bed(1.65)),
    "bed_king": AssetSpec("bed_king", "King bed", (1.98, 1.095, 2.105), _build_bed(1.9)),
    "nightstand": AssetSpec(
        "nightstand", "Bedside table", (0.51, 0.575, 0.47), _build_nightstand, category="storage"
    ),
    "bed_lamp": AssetSpec(
        "bed_lamp", "Bedside lamp", (0.22, 0.35, 0.22), _build_bed_lamp, collision=False, category="lighting"
    ),
    "wardrobe": AssetSpec("wardrobe", "Wardrobe", (1.62, 2.12, 0.655), _build_wardrobe(1.6), category="storage"),
    "wardrobe_wide": AssetSpec(
        "wardrobe_wide", "Wardrobe", (2.22, 2.12, 0.655), _build_wardrobe(2.2), category="storage"
    ),
    "desk": AssetSpec("desk", "Desk", (1.2, 1.09, 0.6), _build_desk, category="table"),
    "desk_chair": AssetSpec(
        "desk_chair", "Desk chair", (0.553, 0.905, 0.48), _build_desk_chair, category="seating"
    ),
    "kitchen_counter_240": AssetSpec(
        "kitchen_counter_240",
        "Kitchen cabinets",
        (2.44, 2.13, 0.675),
        _build_kitchen_counter(2.4),
        category="kitchen",
    ),
    "kitchen_counter_180": AssetSpec(
        "kitchen_counter_180",
        "Kitchen cabinets",
        (1.84, 2.13, 0.675),
        _build_kitchen_counter(1.8),
        category="kitchen",
    ),
    "kitchen_counter_120": AssetSpec(
        "kitchen_counter_120",
        "Kitchen cabinets",
        (1.24, 2.13, 0.675),
        _build_kitchen_counter(1.2),
        category="kitchen",
    ),
    "kitchen_sink": AssetSpec(
        "kitchen_sink", "Sink", (0.62, 0.365, 0.46), _build_kitchen_sink, collision=False, category="fixture"
    ),
    "kitchen_stove": AssetSpec(
        "kitchen_stove", "Stove", (0.62, 0.09, 0.505), _build_kitchen_stove, collision=False, category="appliance"
    ),
    "refrigerator": AssetSpec(
        "refrigerator", "Refrigerator", (0.73, 1.86, 0.745), _build_refrigerator, category="appliance"
    ),
    "dining_table_6": AssetSpec(
        "dining_table_6", "Dining table", (1.8, 0.75, 1.0), _build_dining_table(1.8, 1.0), category="table"
    ),
    "dining_table_4": AssetSpec(
        "dining_table_4", "Dining table", (1.4, 0.75, 0.9), _build_dining_table(1.4, 0.9), category="table"
    ),
    "dining_table_round": AssetSpec(
        "dining_table_round", "Round dining table", (1.2, 0.738, 1.2), _build_round_table, category="table"
    ),
    "dining_chair": AssetSpec(
        "dining_chair", "Dining chair", (0.46, 0.96, 0.52), _build_dining_chair, category="seating"
    ),
    "pendant": AssetSpec(
        "pendant", "Pendant light", (0.38, 0.745, 0.38), _build_pendant, collision=False, category="lighting"
    ),
    "sideboard": AssetSpec("sideboard", "Sideboard", (1.43, 1.05, 0.5), _build_sideboard, category="storage"),
    "toilet": AssetSpec("toilet", "Toilet", (0.42, 0.842, 0.66), _build_toilet, category="fixture"),
    "vanity": AssetSpec("vanity", "Washbasin and vanity", (0.94, 0.99, 0.55), _build_vanity, category="fixture"),
    "wall_mirror": AssetSpec(
        "wall_mirror", "Mirror", (0.72, 0.92, 0.046), _build_wall_mirror, collision=False, category="decor"
    ),
    "shower": AssetSpec("shower", "Shower", (0.9, 1.98, 0.9), _build_shower, category="fixture"),
    "bathtub": AssetSpec("bathtub", "Bathtub", (1.74, 0.857, 0.78), _build_bathtub, category="fixture"),
    "towel_rail": AssetSpec(
        "towel_rail", "Towel rail", (0.6, 0.35, 0.035), _build_towel_rail, collision=False, category="decor"
    ),
    "shoe_cabinet": AssetSpec(
        "shoe_cabinet", "Shoe cabinet", (1.03, 0.9, 0.4), _build_shoe_cabinet, category="storage"
    ),
    "coat_rack": AssetSpec("coat_rack", "Coat rack", (0.4, 1.7, 0.355), _build_coat_rack, category="storage"),
    "entry_bench": AssetSpec(
        "entry_bench", "Entry bench", (0.9, 0.525, 0.36), _build_entry_bench, category="seating"
    ),
    "balcony_chair": AssetSpec(
        "balcony_chair", "Outdoor chair", (0.58, 0.84, 0.6), _build_balcony_chair, category="seating"
    ),
    "balcony_table": AssetSpec(
        "balcony_table", "Outdoor table", (0.5, 0.5, 0.5), _build_balcony_table, category="table"
    ),
    "art_frame": AssetSpec(
        "art_frame", "Wall art", (0.64, 0.48, 0.04), _build_art_frame, collision=False, category="decor"
    ),
}


def asset_spec(asset_id: str) -> AssetSpec:
    try:
        return ASSETS[asset_id]
    except KeyError as exc:
        raise KeyError(f"Unknown furniture asset '{asset_id}'") from exc


@lru_cache(maxsize=None)
def asset_visual_bounds(asset_id: str) -> Tuple[float, float, float, float, float, float]:
    """Exact local-space bounds of the built meshes: (min_x, min_y, min_z, max_x, max_y, max_z)."""
    parts = build_parts(asset_id, "modern")
    minimum = np.array([part.mesh.bounds[0] for part in parts]).min(axis=0)
    maximum = np.array([part.mesh.bounds[1] for part in parts]).max(axis=0)
    return (
        float(minimum[0]),
        float(minimum[1]),
        float(minimum[2]),
        float(maximum[0]),
        float(maximum[1]),
        float(maximum[2]),
    )


def build_parts(asset_id: str, style: str) -> List[Part]:
    """Build the shared local-space parts for an asset in a given style.

    Every asset is shifted so its lowest point sits at y=0; this keeps floor
    contact exact and makes surface stacking and collision bounds predictable.
    """
    if style not in STYLES:
        raise ValueError(f"Unknown interior style '{style}'")
    parts = asset_spec(asset_id).builder(style)
    minimum_y = min(float(part.mesh.bounds[0][1]) for part in parts)
    if abs(minimum_y) > 1e-9:
        for part in parts:
            part.mesh.apply_translation((0.0, -minimum_y, 0.0))
    for part in parts:
        part.mesh.metadata = {"asset_id": asset_id, "material": part.material}
    return parts


def material_palette(style: str) -> Dict[str, str]:
    if style not in PALETTES:
        raise ValueError(f"Unknown interior style '{style}'")
    return dict(PALETTES[style])


def material_config(style: str) -> Dict[str, Dict[str, object]]:
    palette = material_palette(style)
    config = {}
    for key, color in palette.items():
        alpha, metallic, roughness = _material_finish(key)
        config[key] = {
            "color": color,
            "base_color_factor": _hex_to_rgba(color, alpha),
            "metallic_factor": metallic,
            "roughness_factor": roughness,
            "alpha": alpha,
        }
    return config


def _material_finish(material: str) -> Tuple[float, float, float]:
    """Return (alpha, metallic, roughness) for a semantic material.

    Fabric, bedding and rugs are deliberately matte; timber, stone, ceramic
    and metal keep the tighter roughness values that read as their real
    surfaces under image-based lighting. Glass is alpha-blended.
    """
    if material == "glass":
        return 0.22, 0.0, 0.05
    if material in {"metal", "metal_dark"}:
        return 1.0, 0.72, 0.32 if material == "metal" else 0.4
    if material == "appliance":
        return 1.0, 0.35, 0.3
    if material == "screen":
        return 1.0, 0.25, 0.12
    if material in {"wood", "wood_light", "wood_dark"}:
        return 1.0, 0.0, 0.46
    if material in {"stone", "counter"}:
        return 1.0, 0.0, 0.32
    if material == "ceramic":
        return 1.0, 0.0, 0.2
    if material in {"cabinet", "accent", "art"}:
        return 1.0, 0.0, 0.55
    if material in {"fabric", "fabric_light", "fabric_dark", "cushion", "bedding", "rug"}:
        return 1.0, 0.0, 0.92
    if material in {"plant", "soil"}:
        return 1.0, 0.0, 0.78
    if material == "light":
        return 1.0, 0.0, 0.55
    return 1.0, 0.0, 0.72


def pbr_material(style: str, material: str) -> PBRMaterial:
    if material not in PALETTES[style]:
        raise KeyError(f"Unknown material '{material}' for style '{style}'")
    palette = PALETTES[style]
    alpha, metallic, roughness = _material_finish(material)
    kwargs: Dict[str, object] = {}
    if material == "light":
        glow = _hex_to_rgba(palette[material])[:3]
        kwargs["emissiveFactor"] = [min(1.0, value * 1.6) for value in glow]
    return PBRMaterial(
        name=f"{style}_{material}",
        baseColorFactor=_hex_to_rgba(palette[material], alpha),
        metallicFactor=metallic,
        roughnessFactor=roughness,
        alphaMode="BLEND" if alpha < 1.0 else "OPAQUE",
        doubleSided=material == "glass",
        **kwargs,
    )


def asset_library_payload(style: str = "modern") -> Dict:
    """Serialisable description of the procedural asset library."""
    if style not in STYLES:
        style = "modern"
    return {
        "style": style,
        "styles": [
            {"id": item, "label": STYLE_LABELS[item]} for item in STYLES
        ],
        "materials": material_config(style),
        "assets": [
            {
                "asset_id": spec.asset_id,
                "label": spec.label,
                "category": spec.category,
                "size_m": list(spec.size),
                "collision": spec.collision,
                "source": "procedural",
                "format": "glb",
            }
            for spec in sorted(ASSETS.values(), key=lambda item: item.asset_id)
        ],
    }
