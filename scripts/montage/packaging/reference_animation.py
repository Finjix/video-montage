"""Calibrated text-driven entrance animations (bounce_up, shout_wave, ice_drift).

Font contours are shaded with fitted distance/height/light lattices; the module
never opens reference videos and works for any supplied text.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np

from montage.common import ROOT, read, sha
from montage import runtime
from montage.packaging import dynamic_flowers

LAB = ROOT / "assets/packaging/animations"
FONT = ROOT / "assets/packaging/fonts/WenYue-XinQingNianTi-W8.otf"
REFERENCE_TEXT = "无尽冬日"
WIDTH, HEIGHT, FPS, FRAMES = 1920, 3414, 60, 60
EFFECTS = ("bounce_up", "shout_wave", "ice_drift")


def coordinates(distance, shader):
    geometry = shader["geometry"]
    dn, yn, xn = [np.asarray(shader[key]) for key in ("distance", "height", "light_x")]
    d = np.minimum(np.interp(distance, dn, np.arange(len(dn))), len(dn) - 1.0001)
    yp = (np.arange(distance.shape[0])[:, None] - geometry["y"]) / geometry["size"]
    y = np.broadcast_to(np.minimum(np.interp(yp, yn, np.arange(len(yn))), len(yn) - 1.0001), distance.shape)
    xp = (np.arange(distance.shape[1])[None, :] - geometry["x"]) / shader["horizontal_width"]
    x = np.broadcast_to(np.minimum(np.interp(xp, xn, np.arange(len(xn))), len(xn) - 1.0001), distance.shape)
    return d, y, x


def layout(distance, shader, selected=None):
    d, y, x = coordinates(distance, shader)
    if selected is not None:
        d, y, x = d[selected], y[selected], x[selected]
    di, yi, xi = [np.floor(v).astype(np.int32) for v in (d, y, x)]
    dd, dy, dx = d - di, y - yi, x - xi
    nd, ny = len(shader["distance"]), len(shader["height"])
    indices, weights = [], []
    for ix in range(2):
        for iy in range(2):
            for id_ in range(2):
                indices.append(((xi + ix) * ny + yi + iy) * nd + di + id_)
                weights.append((dx if ix else 1 - dx) * (dy if iy else 1 - dy) * (dd if id_ else 1 - dd))
    return np.stack(indices, -1), np.stack(weights, -1).astype(np.float32)


def normals(distance, shader):
    nx = cv2.Sobel(distance, cv2.CV_32F, 1, 0, ksize=3) / 8
    ny = cv2.Sobel(distance, cv2.CV_32F, 0, 1, ksize=3) / 8
    length = np.maximum(np.hypot(nx, ny), .01)
    nx, ny = nx / length, ny / length
    ratio = shader["normal_scale"]
    outer = shader["color_cutoff"]
    gate = np.clip((3 * ratio - distance) / (2 * ratio), 0, 1) * np.clip((distance + outer) / (4 * ratio), 0, 1)
    return np.stack([np.ones(distance.shape), nx * gate, ny * gate, (nx * nx - ny * ny) * gate, 2 * nx * ny * gate], -1).astype(np.float32)


@lru_cache(maxsize=48)
def load_table(filename, digest, directory=str(LAB / "parameters")):
    root = Path(directory).resolve()
    path = (root / filename).resolve()
    if path.parent != root:
        raise ValueError("Shader tables must be inside the parameter directory")
    if not path.is_file() and root == (LAB / "parameters").resolve():
        path = runtime.animation(filename, digest)
    if sha(path) != digest:
        raise ValueError(f"Light shader changed: {path}")
    with np.load(path, allow_pickle=False) as data:
        return data["coefficients"].astype(np.float32)


def shade_light(distance, shader):
    indices, weights = layout(distance, shader)
    fields = normals(distance, shader)
    coefficients = shader.get("coefficients")
    if coefficients is None:
        coefficients = load_table(shader["table_file"], shader["table_sha256"], shader.get("_parameter_dir", str(LAB / "parameters")))
    coefficients = np.asarray(coefficients, np.float32)
    result = np.zeros((*distance.shape, 3), np.float32)
    for f in range(5):
        value = np.zeros_like(result)
        for corner in range(8):
            value += coefficients[f][indices[..., corner]] * weights[..., corner, None]
        result += value * fields[..., f, None]
    fade = shader.get("edge_fade", 0.)
    if fade:
        result *= np.clip((distance + shader["color_cutoff"]) / fade, 0., 1.)[:, :, None]
    result[distance < -shader["color_cutoff"]] = 0
    return np.clip(result, 0, 255)


def glyph_alpha(text: str, geometry: dict, shape: tuple[int, int], supersample: int = 4) -> np.ndarray:
    """Render supplied text directly at the requested resolution from font outlines."""
    height, width = shape
    image = np.zeros((height * supersample, width * supersample), np.float32)
    scale, shift = dynamic_flowers.font_profile(str(FONT))
    sx = geometry["size"] / 500 * scale * geometry["sx"]
    sy = geometry["size"] / 500 * scale
    x = geometry["x"]
    for char in text:
        pixels, step, left, top = dynamic_flowers.glyph(char, str(FONT))
        transform = np.array([[sx * supersample, 0, (x + left * sx) * supersample],
                              [0, sy * supersample, (geometry["y"] + shift * geometry["size"] / 500 + top * sy) * supersample]], np.float32)
        painted = cv2.warpAffine(pixels.astype(np.float32) / 255, transform,
                                 (width * supersample, height * supersample), flags=cv2.INTER_LINEAR)
        np.maximum(image, painted, out=image)
        x += step * sx + geometry["tracking"]
    return image


def signed_distance(alpha: np.ndarray, supersample: int = 4) -> np.ndarray:
    solid = (alpha >= .5).astype(np.uint8)
    inside = cv2.distanceTransform(solid, cv2.DIST_L2, cv2.DIST_MASK_PRECISE)
    outside = cv2.distanceTransform(1 - solid, cv2.DIST_L2, cv2.DIST_MASK_PRECISE)
    distance = (inside - outside + np.where(solid > 0, -.5, .5)) / supersample
    return cv2.resize(distance.astype(np.float32), (alpha.shape[1] // supersample, alpha.shape[0] // supersample), interpolation=cv2.INTER_AREA)


def shade(distance: np.ndarray, shader: dict) -> np.ndarray:
    if shader.get("lighting_grid"):
        return shade_light(distance, shader)
    geometry = shader["geometry"]
    dn, yn = np.asarray(shader["distance"]), np.asarray(shader["height"])
    d = np.minimum(np.interp(distance, dn, np.arange(len(dn))), len(dn) - 1.0001)
    yy = (np.arange(distance.shape[0]) - geometry["y"]) / geometry["size"]
    y = np.broadcast_to(np.minimum(np.interp(yy, yn, np.arange(len(yn))), len(yn) - 1.0001)[:, None], distance.shape)
    if "rgb" in shader:
        tables = np.asarray(shader["rgb"], np.float32)
    else:
        tables = load_table(shader["table_file"], shader["table_sha256"], shader.get("_parameter_dir", str(LAB / "parameters")))
    result = dynamic_flowers.sample_table(tables[0], d, y)
    nx = cv2.Sobel(distance, cv2.CV_32F, 1, 0, ksize=3) / 8
    ny = cv2.Sobel(distance, cv2.CV_32F, 0, 1, ksize=3) / 8
    norm = np.maximum(np.hypot(nx, ny), .01)
    nx, ny = nx / norm, ny / norm
    ratio = shader.get("normal_scale", 1.)
    outer = shader.get("outer_normal_radius", 18 * ratio)
    gate = np.clip((3 * ratio - distance) / (2 * ratio), 0, 1) * np.clip((distance + outer) / (4 * ratio), 0, 1)
    fields = [np.ones(distance.shape, np.float32), nx * gate, ny * gate, (nx * nx - ny * ny) * gate, 2 * nx * ny * gate]
    features = fields[1:]
    if shader.get("horizontal_order", 0):
        xx = np.broadcast_to((np.arange(distance.shape[1])[None, :] - geometry["x"]) / shader["horizontal_width"] * 2 - 1, distance.shape)
        xx = np.clip(xx, -1.5, 1.5)
        for power in range(1, shader["horizontal_order"] + 1):
            features.extend([field * xx ** power for field in fields])
    for table, feature in zip(tables[1:], features):
        result += dynamic_flowers.sample_table(table, d, y) * feature[:, :, None]
    return np.clip(result, 0, 255).astype(np.float32)


@dataclass
class TextSprite:
    rgb: np.ndarray
    alpha: np.ndarray
    x: float
    y: float
    shrink: float


@lru_cache(maxsize=8)
def text_sprite(text: str, parameter_dir: str = str(LAB / "parameters")) -> TextSprite:
    if not isinstance(text, str) or not text.strip() or any(c in text for c in "\n\r\t"):
        raise ValueError("subtitle text must be a nonempty single line without tabs or line breaks")
    if len(text) > 32:
        raise ValueError("reference animation supports at most 32 characters")
    spec = read(Path(parameter_dir) / "static.json")
    if sha(FONT) != spec["font_sha256"]:
        raise ValueError("Bundled font differs from the calibration font")
    geometry = spec["geometry"]
    advance = lambda value: dynamic_flowers.advance(value, geometry, str(FONT))
    extra = advance(text) - advance(REFERENCE_TEXT)
    width, height = max(240, round(spec["canvas"][0] + extra)), spec["canvas"][1]
    mask = glyph_alpha(text, geometry, (height, width))
    distance = signed_distance(mask)
    rgb = shade(distance, spec["shader"])
    rgb[distance < -45] = 0
    alpha = np.where(distance >= -8.5, 1., np.minimum(1., rgb.max(axis=2) / 237)).astype(np.float32)
    # Keep the maximum shout-wave expansion inside the frame for long text.
    shrink = min(1., (WIDTH - 60) / max(1., (width - 150) * 1.75 + 150))
    return TextSprite(rgb, alpha, spec["origin"][0] - extra / 2, spec["origin"][1], shrink)


def vertical_blur(rgb: np.ndarray, kernels: list[list[float]], sigma_x: float = 0.) -> tuple[np.ndarray, int]:
    radius = (len(kernels[0]) - 1) // 2
    padded = cv2.copyMakeBorder(rgb, radius, radius, 0, 0, cv2.BORDER_CONSTANT)
    if sigma_x > 0:
        padded = cv2.GaussianBlur(padded, (0, 1), sigma_x, borderType=cv2.BORDER_CONSTANT)
    channels = []
    for channel in range(3):
        kernel = np.array(kernels[channel], np.float32)[::-1]
        channels.append(cv2.sepFilter2D(padded[:, :, channel], -1, np.ones(1, np.float32), kernel, borderType=cv2.BORDER_CONSTANT))
    return np.clip(np.stack(channels, 2), 0, 255), radius


def horizontal_blur(rgb: np.ndarray, kernels: list[list[float]]) -> np.ndarray:
    return np.stack([cv2.sepFilter2D(rgb[:, :, c], -1, np.array(kernels[c], np.float32)[::-1],
                                    np.ones(1, np.float32), borderType=cv2.BORDER_CONSTANT) for c in range(3)], 2)


def place(canvas: np.ndarray, image: np.ndarray, x: float, y: float, additive=False) -> None:
    """Place an RGB sprite with subpixel sampling and intentional frame clipping."""
    ix, iy = int(np.floor(x)), int(np.floor(y))
    fx, fy = x - ix, y - iy
    if fx or fy:
        image = cv2.warpAffine(image, np.array([[1., 0., fx], [0., 1., fy]], np.float32),
                               (image.shape[1] + 1, image.shape[0] + 1), flags=cv2.INTER_LINEAR)
    x0, y0 = max(0, ix), max(0, iy)
    x1, y1 = min(canvas.shape[1], ix + image.shape[1]), min(canvas.shape[0], iy + image.shape[0])
    if x1 <= x0 or y1 <= y0:
        return
    region = canvas[y0:y1, x0:x1]
    pixels = image[y0 - iy:y1 - iy, x0 - ix:x1 - ix]
    if additive:
        region += pixels
    else:
        region[:] = pixels


def transform(rgb: np.ndarray, sprite: TextSprite, scale: float, shape: tuple[int, int], crop_origin: tuple[int, int]) -> np.ndarray:
    cx, cy = 960., 2358.
    matrix = np.array([[scale, 0, (sprite.x - cx) * scale + cx - crop_origin[0]],
                       [0, scale, (sprite.y - cy) * scale + cy - crop_origin[1]]], np.float32)
    return cv2.warpAffine(rgb, matrix, (shape[1], shape[0]), flags=cv2.INTER_LINEAR)


class Animation:
    def __init__(self, effect: str, text: str = REFERENCE_TEXT, parameter_dir: Path = LAB / "parameters"):
        if effect not in EFFECTS:
            raise ValueError(f"Unknown effect: {effect}")
        self.effect = effect
        self.parameter_dir = parameter_dir.resolve()
        self.sprite = text_sprite(text, str(parameter_dir.resolve()))
        self.parameters = read(parameter_dir / f"{effect}.json")
        self.text = text

    def canonical_frame(self, n: int) -> np.ndarray:
        if not 0 <= n < FRAMES:
            raise ValueError("Frame index must be in 0..59")
        canvas = np.zeros((HEIGHT, WIDTH, 3), np.float32)
        sprite = self.sprite
        if n >= 30:
            image, x, y = sprite.rgb, sprite.x, sprite.y
        elif self.effect == "bounce_up":
            item = self.parameters["frames"][n]
            if not item["visible"]:
                return canvas.astype(np.uint8)
            image, radius = vertical_blur(sprite.rgb, item["kernels"], item.get("sigma_x", 0.))
            x, y = sprite.x + item.get("dx", 0.), sprite.y + item["dy"] - radius
        elif self.effect == "shout_wave":
            image, x, y = self.shout_frame(n)
        else:
            image, x, y = self.ice_frame(n)
        if sprite.shrink < 1.:
            factor = sprite.shrink
            image = cv2.resize(image, None, fx=factor, fy=factor, interpolation=cv2.INTER_AREA)
            x = 960 + (x - 960) * factor
            y = 2358 + (y - 2358) * factor
        place(canvas, image, x, y)
        return np.clip(np.rint(canvas), 0, 255).astype(np.uint8)

    def shout_frame(self, n: int) -> tuple[np.ndarray, float, float]:
        item = self.parameters["frames"][n]
        post = item.get("post_kernels")
        def finish(image, origin):
            if post:
                image = horizontal_blur(image, post)
            return np.clip(image, 0, 255), origin[0], origin[1]
        sprite = self.sprite
        # Wider text gets a wider local effect band; no fixed-word sprites.
        width = max(1080, round(sprite.rgb.shape[1] * 1.85))
        origin = (round(960 - width / 2), 2130)
        shape = (460, width)
        if item.get("model") == "surface":
            geometry = dict(item["geometry"])
            # Center a replacement phrase using its actual font advance.
            extra = dynamic_flowers.advance(self.text, geometry, str(FONT)) - dynamic_flowers.advance(REFERENCE_TEXT, geometry, str(FONT))
            geometry["x"] += (width - 1080 - extra) / 2
            shader = {**item["shader"], "geometry": geometry, "horizontal_width": dynamic_flowers.advance(self.text, geometry, str(FONT)),
                      "_parameter_dir": str(self.parameter_dir)}
            distance = signed_distance(glyph_alpha(self.text, geometry, shape))
            image = shade(distance, shader)
            image[distance < -shader.get("color_cutoff", 19 * shader["normal_scale"])] = 0
            echo = np.zeros_like(image)
            for layer in item.get("layers", []):
                echo += transform(sprite.rgb, sprite, layer["scale"], shape, origin) * np.array(layer["weight"], np.float32)
            if item.get("echo_occlusion"):
                scale = geometry["size"] / read(self.parameter_dir / "static.json")["geometry"]["size"]
                alpha = np.where(distance >= -8.5 * scale, 1., 0.)
                echo *= 1 - alpha[:, :, None]
            image += echo
            return finish(image, origin)
        if item.get("model") == "zoom_psf":
            image = np.zeros((*shape, 3), np.float32)
            for layer in item["layers"]:
                image += transform(sprite.rgb, sprite, layer["scale"], shape, origin) * np.array(layer["weight"], np.float32)
            return finish(image, origin)
        main = transform(sprite.rgb, sprite, item["main_scale"], shape, origin)
        alpha = transform(sprite.alpha, sprite, item["main_scale"], shape, origin)
        echo = np.zeros_like(main)
        for layer in item.get("layers", []):
            echo += transform(sprite.rgb, sprite, layer["scale"], shape, origin) * np.array(layer["weight"], np.float32)
        image = main * np.array(item.get("main_gain", [1., 1., 1.]), np.float32) + echo * (1 - alpha[:, :, None])
        return finish(image, origin)

    def ice_frame(self, n: int) -> tuple[np.ndarray, float, float]:
        item = self.parameters["frames"][n]
        sprite = self.sprite
        if not item["visible"]:
            return np.zeros_like(sprite.rgb), sprite.x, sprite.y
        width = max(860, sprite.rgb.shape[1] + 130)
        origin = (round(960 - width / 2), 2120)
        shape = (480, width)
        count = min(len(self.text), int(n * max(0, len(self.text) - 1) / 21) + 1)
        text = self.text[:count]
        geometry = dict(item["geometry"])
        extra = dynamic_flowers.advance(self.text, geometry, str(FONT)) - dynamic_flowers.advance(REFERENCE_TEXT, geometry, str(FONT))
        geometry["x"] += (width - 860 - extra) / 2 + 30
        shader = {**item["shader"], "geometry": geometry,
                  "_parameter_dir": str(self.parameter_dir),
                  "horizontal_width": max(1., dynamic_flowers.advance(text, geometry, str(FONT)))}
        distance = signed_distance(glyph_alpha(text, geometry, shape))
        image = shade(distance, shader)
        image[distance < -shader["color_cutoff"]] = 0
        if item.get("settle_weights"):
            weights = np.interp(np.linspace(0, len(item["settle_weights"]) - 1, count),
                                np.arange(len(item["settle_weights"])), item["settle_weights"])
            image = settle_scene(image, text, geometry, read(self.parameter_dir / "static.json")["shader"], weights)
        word_advance = dynamic_flowers.advance(self.text, geometry, str(FONT))
        for particle in self.snow_particles(item, geometry, count):
            paint_snow(image, particle, geometry["x"], geometry["y"], word_advance)
        return np.clip(image, 0, 255), origin[0], origin[1]

    def snow_particles(self, item: dict, geometry: dict, visible_count: int):
        particles = item.get("particles", [])
        if self.text == REFERENCE_TEXT:
            return particles
        # Attach calibrated emitter tracks to glyph-local coordinates. Replacing
        # a phrase regenerates its glyph advances and repeats the local emitters.
        # A fixed seed adds a subpixel variation to repeated glyphs deterministically.
        advance = lambda value: dynamic_flowers.advance(value, geometry, str(FONT))
        reference_width = advance(REFERENCE_TEXT)
        source_widths = [advance(c) for c in REFERENCE_TEXT]
        source_starts = np.cumsum([0.] + [v + geometry["tracking"] for v in source_widths[:-1]])
        source_centres = source_starts + np.array(source_widths) / 2
        groups = [[] for _ in REFERENCE_TEXT]
        for particle in particles:
            px = particle["u"] * reference_width
            i = int(np.argmin(abs(source_centres - px)))
            groups[i].append((particle, (px - source_starts[i]) / source_widths[i]))
        width = advance(self.text)
        rng = np.random.default_rng(self.parameters.get("seed", 0))
        output, start = [], 0.
        for i, char in enumerate(self.text[:visible_count]):
            char_width = advance(char)
            group = min(3, i * 4 // len(self.text))
            for particle, local_u in groups[group]:
                jitter = float(rng.uniform(-.25, .25))
                output.append({**particle, "u": (start + local_u * char_width + jitter) / width})
            start += char_width + geometry["tracking"]
        return output

    def frame(self, n: int, width=WIDTH, height=HEIGHT) -> np.ndarray:
        if (width, height) not in ((WIDTH, HEIGHT), (1440, 2560)):
            raise ValueError("Supported sizes are 1920x3414 and 1440x2560")
        pixels = self.canonical_frame(n)
        if (width, height) != (WIDTH, HEIGHT):
            pixels = cv2.resize(pixels, (width, height), interpolation=cv2.INTER_AREA)
        return pixels


def paint_particle(image: np.ndarray, x: float, y: float, sigma: float, rgb: list[float]) -> None:
    radius = max(3, int(np.ceil(sigma * 4)))
    ix, iy = int(round(x)), int(round(y))
    x0, x1 = max(0, ix - radius), min(image.shape[1], ix + radius + 1)
    y0, y1 = max(0, iy - radius), min(image.shape[0], iy + radius + 1)
    if x0 >= x1 or y0 >= y1:
        return
    yy, xx = np.mgrid[y0:y1, x0:x1]
    spot = np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * sigma ** 2))
    image[y0:y1, x0:x1] += spot[:, :, None] * np.array(rgb, np.float32)


def paint_snow(image, particle, gx, gy, advance):
    x, y = gx + particle["u"] * advance, gy + particle["dy"]
    if not particle.get("components"):
        paint_particle(image, x, y, particle["sigma"], particle["rgb"])
        return
    radius = 12
    ix, iy = round(x), round(y)
    x0, x1 = max(0, ix - radius), min(image.shape[1], ix + radius + 1)
    y0, y1 = max(0, iy - radius), min(image.shape[0], iy + radius + 1)
    if x0 >= x1 or y0 >= y1:
        return
    yy, xx = np.mgrid[y0:y1, x0:x1]
    for component in particle["components"]:
        if component.get("kind") == "diamond":
            spot = np.zeros(xx.shape, np.float64)
            for ox in (-.375, -.125, .125, .375):
                for oy in (-.375, -.125, .125, .375):
                    spot += np.maximum(0., 1 - np.abs(xx + ox - x) / component["sx"] - np.abs(yy + oy - y) / component["sy"]) ** component.get("power", 1)
            spot /= 16
        else:
            spot = np.exp(-.5 * (((xx - x) / component["sx"]) ** 2 + ((yy - y) / component["sy"]) ** 2))
        image[y0:y1, x0:x1] += spot[:, :, None] * np.asarray(component["rgb"], np.float32)


def settle_scene(image, text, geometry, static_shader, weights):
    distance = signed_distance(glyph_alpha(text, geometry, image.shape[:2]))
    stable = shade(distance, {**static_shader, "geometry": geometry})
    result = image.copy()
    x = geometry["x"]
    for char, weight in zip(text, weights):
        if weight > 0:
            d = signed_distance(glyph_alpha(char, {**geometry, "x": x}, image.shape[:2]))
            alpha = np.clip(d + 9., 0, 1) * weight
            result = result * (1 - alpha[:, :, None]) + stable * alpha[:, :, None]
        x += dynamic_flowers.advance(char, geometry, str(FONT)) + geometry["tracking"]
    return result
