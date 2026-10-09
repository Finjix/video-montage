"""Text-driven animation renderer. This module never opens reference videos."""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import subprocess

import cv2
import numpy as np

from common import LAB, FONT, FFMPEG, WIDTH, HEIGHT, FPS, FRAMES, REFERENCE_TEXT, EFFECTS, read_json, write_json, sha, glyph_alpha, signed_distance, shade, dynamic_flowers, probe


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
        raise ValueError("字幕必须是非空的单行文字，不能含换行或制表符")
    if len(text) > 32:
        raise ValueError("独立实验工具最多支持32个字符")
    spec = read_json(Path(parameter_dir) / "static.json")
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
        self.parameters = read_json(parameter_dir / f"{effect}.json")
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
                scale = geometry["size"] / read_json(self.parameter_dir / "static.json")["geometry"]["size"]
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
            image = settle_scene(image, text, geometry, read_json(self.parameter_dir / "static.json")["shader"], weights)
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


def render(effect: str, text: str, output_dir: Path, width=WIDTH, height=HEIGHT, parameter_dir=LAB / "parameters") -> Path:
    animation = Animation(effect, text, parameter_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / f"{EFFECTS[effect]}.mp4"
    if target.exists():
        raise FileExistsError(f"拒绝覆盖已有文件：{target}")
    partial = target.with_suffix(".partial.mp4")
    command = [str(FFMPEG), "-v", "error", "-nostdin", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
               "-s", f"{width}x{height}", "-r", str(FPS), "-i", "pipe:0", "-an", "-c:v", "libx264",
               "-preset", "medium", "-crf", "10", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(partial)]
    with (output_dir / f"{effect}-encode.log").open("wb") as log:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stderr=log)
        try:
            for n in range(FRAMES):
                process.stdin.write(animation.frame(n, width, height).tobytes())
            process.stdin.close()
            if process.wait() != 0:
                raise RuntimeError(f"FFmpeg encoding failed; see {log.name}")
            spec = probe(partial)
            if spec["frames"] != FRAMES or spec["fps"] != "60/1":
                raise RuntimeError(f"Unexpected encoded video specification: {spec}")
            partial.rename(target)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            if not process.stdin.closed:
                process.stdin.close()
            partial.unlink(missing_ok=True)
    write_json(output_dir / f"{effect}-render.json", {"effect": effect, "text": text, "output": str(target.resolve()),
               "output_sha256": sha(target), "spec": spec, "font_sha256": sha(FONT),
               "parameter_sha256": {p.name: sha(p) for p in [parameter_dir / "static.json", parameter_dir / f"{effect}.json"]},
               "runtime_reads_reference": False})
    return target


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
