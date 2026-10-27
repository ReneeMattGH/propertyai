"""Procedural PBR surface materials for reconstructed architecture.

The floor-plan-only workflow has no photographs of the real surfaces, so the
architecture would otherwise render as flat vertex-coloured slabs. This module
generates small, deterministic, tileable texture sets (base colour + normal
map) for oak flooring, stone tile, painted plaster and ceiling plaster. The
images are embedded in the exported GLB and reused across every wall and room.

These are generated finishes, not photographs of the property. Callers surface
that limitation to users in the reconstruction metadata.
"""
from __future__ import annotations

import math
from functools import lru_cache
from typing import Dict, Tuple

import numpy as np
from PIL import Image
from trimesh.visual.material import PBRMaterial

TEXTURE_TILE_METERS: Dict[str, float] = {
    "floor_wood": 2.4,
    "floor_tile": 2.0,
    "wall_plaster": 1.8,
    "ceiling_plaster": 2.4,
}

STYLE_TINTS: Dict[str, Dict[str, str]] = {
    "modern": {
        "floor": "#a1764c",
        "floor_light": "#c9a170",
        "tile": "#cbc2b2",
        "wall": "#e9e2d5",
        "ceiling": "#f7f3ea",
    },
    "minimalist": {
        "floor": "#c3aa87",
        "floor_light": "#ddc7a5",
        "tile": "#ded8cc",
        "wall": "#f0ece4",
        "ceiling": "#faf7f1",
    },
    "scandinavian": {
        "floor": "#c69b6a",
        "floor_light": "#e0bc8f",
        "tile": "#d8d1c3",
        "wall": "#eee8dc",
        "ceiling": "#f9f5ed",
    },
    "warm_luxury": {
        "floor": "#95693f",
        "floor_light": "#b5875a",
        "tile": "#d2c6b3",
        "wall": "#e7ddcc",
        "ceiling": "#f4eddf",
    },
}

TRIM_COLOR = "#e7e0d2"
GLASS_COLOR = "#c6dbe3"


def _rgb(value: str) -> np.ndarray:
    value = value.lstrip("#")
    return np.array(
        [int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)],
        dtype=np.float64,
    )


def _clamp_image(array: np.ndarray) -> Image.Image:
    return Image.fromarray(np.clip(array, 0, 255).astype(np.uint8), mode="RGB")


def _smooth_noise(
    size: int, cells: int, rng: np.random.Generator, octaves: int = 3
) -> np.ndarray:
    """Low-frequency tileable value noise in the 0..1 range."""
    total = np.zeros((size, size), dtype=np.float64)
    weight = 0.0
    amplitude = 1.0
    for octave in range(octaves):
        count = max(2, cells * (2**octave))
        grid = rng.random((count, count))
        grid = np.pad(grid, ((0, 1), (0, 1)), mode="wrap")
        image = Image.fromarray((grid * 255).astype(np.uint8), mode="L").resize(
            (size, size), Image.Resampling.BICUBIC
        )
        total += amplitude * (np.asarray(image, dtype=np.float64) / 255.0)
        weight += amplitude
        amplitude *= 0.5
    return total / weight


def _normal_from_height(height: np.ndarray, strength: float) -> Image.Image:
    """Convert a height field to a tangent-space normal map."""
    grad_y, grad_x = np.gradient(height)
    nx = -grad_x * strength
    ny = grad_y * strength
    nz = np.ones_like(height)
    length = np.sqrt(nx * nx + ny * ny + nz * nz)
    normal = np.stack(
        [nx / length * 0.5 + 0.5, ny / length * 0.5 + 0.5, nz / length * 0.5 + 0.5],
        axis=-1,
    )
    return _clamp_image(normal * 255.0)


def _wood_floor(style: str) -> Tuple[Image.Image, Image.Image]:
    size = 512
    rng = np.random.default_rng(20261026)
    tint = STYLE_TINTS.get(style, STYLE_TINTS["modern"])
    base = _rgb(tint["floor"])
    light = _rgb(tint["floor_light"])

    # Vertical planks roughly 0.19 m wide with staggered end joints.
    plank_width = 42
    plank_count = math.ceil(size / plank_width)
    color = np.zeros((size, size, 3), dtype=np.float64)
    height = np.zeros((size, size), dtype=np.float64)
    fine = _smooth_noise(size, 32, rng, octaves=2)
    grain_warp = _smooth_noise(size, 8, rng, octaves=2) * 0.6

    ys = np.arange(size)
    for plank in range(plank_count):
        x0 = plank * plank_width
        x1 = min(size, x0 + plank_width)
        xs = np.arange(x0, x1)
        shade = 0.82 + 0.18 * rng.random()
        plank_color = base * (0.55 + 0.45 * shade) + light * (0.45 * shade)
        # One staggered end joint splits each plank into two boards.
        joint = int((0.25 + 0.5 * rng.random()) * size)
        mask_top = ys < joint
        grain_field = (
            np.sin(
                xs[None, :] / plank_width * 7.0
                + grain_warp[:, x0:x1] * 9.0
                + ys[:, None] * 0.012
            )
            * 0.04
            * (0.6 + 0.4 * fine[:, x0:x1])
        )
        color[:, x0:x1] = plank_color[None, None, :] + grain_field[:, :, None] * 255.0
        # A slightly different tone either side of the joint.
        color[~mask_top, x0:x1] *= 0.96
        height[:, x0:x1] = 0.45 + grain_field * 1.6
        color[joint, x0:x1] *= 0.78
        height[joint, x0:x1] -= 0.25

    # Long plank separations.
    for plank in range(plank_count + 1):
        x = min(size - 1, plank * plank_width)
        color[:, x] *= 0.84
        height[:, x] -= 0.3
    # Soft ambient variation so large floors do not look printed.
    variation = (_smooth_noise(size, 4, rng, octaves=2) - 0.5) * 0.05
    color *= 1.0 + variation[:, :, None]
    height += variation * 0.4
    return _clamp_image(color), _normal_from_height(height, 3.0)


def _stone_tile(style: str) -> Tuple[Image.Image, Image.Image]:
    size = 512
    rng = np.random.default_rng(912663)
    tint = STYLE_TINTS.get(style, STYLE_TINTS["modern"])
    base = _rgb(tint["tile"])
    tile_px = size // 4
    color = np.zeros((size, size, 3), dtype=np.float64)
    height = np.zeros((size, size), dtype=np.float64)
    mottle = _smooth_noise(size, 24, rng, octaves=3)
    for row in range(4):
        for column in range(4):
            shade = 0.94 + 0.06 * rng.random()
            tile = base[None, None, :] * shade
            tile = tile + (
                mottle[
                    row * tile_px : (row + 1) * tile_px,
                    column * tile_px : (column + 1) * tile_px,
                ]
                - 0.5
            )[:, :, None] * 14
            color[
                row * tile_px : (row + 1) * tile_px,
                column * tile_px : (column + 1) * tile_px,
            ] = tile
            height[
                row * tile_px : (row + 1) * tile_px,
                column * tile_px : (column + 1) * tile_px,
            ] = 0.6 + mottle[
                row * tile_px : (row + 1) * tile_px,
                column * tile_px : (column + 1) * tile_px,
            ] * 0.2
    grout = 3
    for index in range(5):
        position = min(size - 1, index * tile_px)
        color[:, position : position + grout] *= 0.82
        color[position : position + grout, :] *= 0.82
        height[:, position : position + grout] -= 0.5
        height[position : position + grout, :] -= 0.5
    return _clamp_image(color), _normal_from_height(height, 1.8)


def _plaster(
    style: str, key: str = "wall"
) -> Tuple[Image.Image, Image.Image]:
    size = 512
    rng = np.random.default_rng(55021 if key == "wall" else 42217)
    tint = STYLE_TINTS.get(style, STYLE_TINTS["modern"])
    base = _rgb(tint["wall"] if key == "wall" else tint["ceiling"])
    broad = _smooth_noise(size, 5, rng, octaves=3) - 0.5
    fine = _smooth_noise(size, 96, rng, octaves=2) - 0.5
    # Painted plaster is mostly smooth: keep low-frequency tonal drift and only
    # a faint orange-peel relief. Strong height fields read as exterior stucco.
    height = broad * 0.35 + fine * 0.7
    # Faint trowel sweep, most visible on walls.
    yy, xx = np.mgrid[0:size, 0:size]
    sweep = np.sin((yy / size) * math.pi * 3.0 + (xx / size) * math.pi * 1.4) * 0.0025
    color = base[None, None, :] * (1.0 + broad[:, :, None] * 0.022 + sweep[:, :, None])
    color += (fine[:, :, None] * 2.6)
    return _clamp_image(color), _normal_from_height(height, 0.85 if key == "wall" else 0.4)


@lru_cache(maxsize=32)
def _textures(key: str, style: str) -> Tuple[Image.Image, Image.Image]:
    if key == "floor_wood":
        return _wood_floor(style)
    if key == "floor_tile":
        return _stone_tile(style)
    if key == "wall_plaster":
        return _plaster(style, "wall")
    if key == "ceiling_plaster":
        return _plaster(style, "ceiling")
    raise KeyError(f"Unknown surface material '{key}'")


@lru_cache(maxsize=32)
def surface_material(key: str, style: str = "modern") -> PBRMaterial:
    """Return a cached PBR material for an architectural surface."""
    if style not in STYLE_TINTS:
        style = "modern"
    if key == "arch_trim":
        return PBRMaterial(
            name="arch_trim",
            baseColorFactor=[
                *( _rgb(TRIM_COLOR) / 255.0 ),
                1.0,
            ],
            metallicFactor=0.0,
            roughnessFactor=0.62,
            alphaMode="OPAQUE",
            doubleSided=False,
        )
    if key == "arch_glass":
        return PBRMaterial(
            name="arch_glass",
            baseColorFactor=[*(_rgb(GLASS_COLOR) / 255.0), 0.2],
            metallicFactor=0.0,
            roughnessFactor=0.05,
            alphaMode="BLEND",
            doubleSided=True,
        )
    base, normal = _textures(key, style)
    roughness = {
        "floor_wood": 0.42,
        "floor_tile": 0.36,
        "wall_plaster": 0.9,
        "ceiling_plaster": 0.94,
    }[key]
    return PBRMaterial(
        name=f"arch_{key}",
        baseColorFactor=[1.0, 1.0, 1.0, 1.0],
        baseColorTexture=base,
        normalTexture=normal,
        metallicFactor=0.0,
        roughnessFactor=roughness,
        alphaMode="OPAQUE",
        doubleSided=False,
    )


def tile_size(key: str) -> float:
    return TEXTURE_TILE_METERS.get(key, 2.0)
