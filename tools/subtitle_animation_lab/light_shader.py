"""Offline light calibration using the shared production shader."""
from common import *
from animation_shader import *

def fit_light(reference, text, geometry, prior_shader, iterations=100, node_spacing=32, cutoff=125):
    width = dynamic_flowers.advance(text, geometry, str(FONT))
    dn, yn = np.array(prior_shader["distance"]), np.array(prior_shader["height"])
    # The x coordinate follows the input phrase, rather than absolute pixels.
    xn = np.linspace(-cutoff / width, 1 + cutoff / width, max(8, round((width + cutoff * 2) / node_spacing) + 1))
    shader = {"lighting_grid": True, "geometry": geometry, "distance": dn.tolist(), "height": yn.tolist(),
              "light_x": xn.tolist(), "horizontal_width": width, "normal_scale": prior_shader["normal_scale"],
              "color_cutoff": cutoff}
    distance = signed_distance(glyph_alpha(text, geometry, reference.shape[:2]))
    selected = distance > -cutoff
    indices, weights = layout(distance, shader, selected)
    fields = normals(distance, shader)[selected]
    count = len(dn) * len(yn) * len(xn)
    weighted = [weights * fields[:, f:f + 1] for f in range(5)]
    target = reference[selected].astype(np.float32)

    def forward(coeff):
        result = np.zeros_like(target)
        for f in range(5):
            for corner in range(8):
                result += coeff[f][indices[:, corner]] * weighted[f][:, corner:corner + 1]
        return result

    def transpose(pixels):
        result = np.zeros((5, count, 3), np.float32)
        for f in range(5):
            for c in range(3):
                result[f, :, c] = np.bincount(indices.ravel(), (weighted[f] * pixels[:, c:c + 1]).ravel(), minlength=count)
        return result

    def penalty(coeff):
        grid = coeff.reshape(5, len(xn), len(yn), len(dn), 3)
        result = grid * .003
        for axis, strength in [(1, .07), (2, .08)]:
            front = [slice(None)] * 5
            middle, back = front.copy(), front.copy()
            front[axis], middle[axis], back[axis] = slice(None, -2), slice(1, -1), slice(2, None)
            second = grid[tuple(front)] - 2 * grid[tuple(middle)] + grid[tuple(back)]
            result[tuple(front)] += strength * second
            result[tuple(middle)] -= 2 * strength * second
            result[tuple(back)] += strength * second
        return result.reshape(coeff.shape)

    prior = np.asarray(prior_shader["rgb"], np.float32).reshape(5, len(yn) * len(dn), 3)
    coeff = np.tile(prior[:, None], (1, len(xn), 1, 1)).reshape(5, count, 3)
    diag = np.zeros((5, count, 1), np.float32)
    for f in range(5):
        diag[f, :, 0] = np.bincount(indices.ravel(), (weighted[f] ** 2).ravel(), minlength=count)
    diag += .6
    op = lambda c: transpose(forward(c)) + penalty(c)
    residual = transpose(target) - op(coeff)
    z = residual / diag
    direction = z.copy()
    rz = (residual * z).sum(axis=(0, 1))
    best, best_score = coeff.copy(), -1.
    for iteration in range(iterations):
        ad = op(direction)
        step = rz / np.maximum((direction * ad).sum(axis=(0, 1)), 1e-15)
        coeff += direction * step
        residual -= ad * step
        z = residual / diag
        new_rz = (residual * z).sum(axis=(0, 1))
        direction = z + direction * new_rz / np.maximum(rz, 1e-15)
        rz = new_rz
        if iteration % 20 == 19 or iteration == iterations - 1:
            image = np.zeros_like(reference)
            image[selected] = np.clip(np.rint(forward(coeff)), 0, 255).astype(np.uint8)
            score = foreground_ssim(reference, image)
            print(f"Light lattice {iteration + 1}: {score:.6f}", flush=True)
            if score > best_score:
                best, best_score = coeff.copy(), score
    shader["coefficients"] = best
    return shader, distance


def freeze(shader, parameter_dir: Path, filename: str):
    target = parameter_dir / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(target, coefficients=shader["coefficients"].astype(np.float32))
    return {**{k: v for k, v in shader.items() if k != "coefficients"}, "table_file": filename, "table_sha256": sha(target)}
