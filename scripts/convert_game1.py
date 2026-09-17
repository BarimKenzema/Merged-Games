#!/usr/bin/env python3
import sys, os, re, zipfile, json, plistlib, msgpack, shutil, glob
import numpy as np
from PIL import Image, ImageDraw

def parse_plist_rect(s):
    nums = re.findall(r'-?\d+\.?\d*', s)
    return [float(n) for n in nums]

def parse_vertices(s):
    nums = [float(n) for n in s.split()]
    return list(zip(nums[0::2], nums[1::2]))

def parse_triangles(s):
    nums = [int(n) for n in s.split()]
    return list(zip(nums[0::3], nums[1::3], nums[2::3]))

def rect_to_polygon(rx, ry):
    return [
        {"x": -rx, "y": -ry},
        {"x": rx, "y": -ry},
        {"x": rx, "y": ry},
        {"x": -rx, "y": ry},
    ]

def load_ledger(path):
    """Returns the set of puzzle IDs already converted in past runs (across
    ALL workflow runs, base-APK or bulk-pack alike, since both write to the
    same committed ledger file). Missing file = empty set (first-ever run)."""
    if not path:
        return set()
    try:
        with open(path) as f:
            return set(line.strip() for line in f if line.strip())
    except FileNotFoundError:
        return set()

def append_ledger(path, puzzle_id):
    if not path:
        return
    with open(path, 'a') as f:
        f.write(puzzle_id + '\n')

def _neighbor(arr, dy, dx):
    pad_width = ((1,1),(1,1)) + ((0,0),) * (arr.ndim - 2)
    padded = np.pad(arr, pad_width, mode='edge')
    h, w = arr.shape[0], arr.shape[1]
    return padded[1+dy:1+dy+h, 1+dx:1+dx+w, ...]

def decontaminate_edges(img_rgba, alpha_threshold=250, iterations=8):
    arr = np.array(img_rgba).astype(np.float32)
    rgb = arr[:,:,:3].copy()
    alpha = arr[:,:,3]
    filled = alpha >= alpha_threshold
    for _ in range(iterations):
        if filled.all():
            break
        unfilled = ~filled
        sum_rgb = np.zeros_like(rgb)
        count = np.zeros(alpha.shape, dtype=np.float32)
        for dy, dx in [(-1,0),(1,0),(0,-1),(0,1)]:
            n_filled = _neighbor(filled, dy, dx)
            n_rgb = _neighbor(rgb, dy, dx)
            sum_rgb[n_filled] += n_rgb[n_filled]
            count[n_filled] += 1
        newly = unfilled & (count > 0)
        if not newly.any():
            break
        rgb[newly] = sum_rgb[newly] / count[newly][:, None]
        filled = filled | newly
    out = arr.copy()
    out[:,:,:3] = rgb
    return Image.fromarray(np.clip(out,0,255).astype(np.uint8), 'RGBA')

def _shift(arr, dy, dx):
    padded = np.pad(arr, ((1,1),(1,1)), mode='constant', constant_values=False)
    h, w = arr.shape[0], arr.shape[1]
    return padded[1+dy:1+dy+h, 1+dx:1+dx+w]

def _dilate_mask(mask_bool, px=1):
    out = mask_bool.copy()
    for _ in range(px):
        grown = out.copy()
        for dy, dx in [(-1,0),(1,0),(0,-1),(0,1)]:
            grown |= _shift(out, dy, dx)
        out = grown
    return out

def _spread_edge_color(arr, mask_bool, spread_px):
    """DEFENSIVE FIX (crease investigation): extends the polygon-interior's
    own real edge color outward into the immediately surrounding, now-
    invisible (alpha=0) margin, using the same grow-nearest-neighbor
    technique as decontaminate_edges(), but seeded from the TRUE polygon
    shape instead of an alpha threshold. Multiple isolated tests (Python/
    Pillow resize, real-browser Canvas2D at 1:1 and scaled, both against
    solid backgrounds) failed to reproduce the crease in isolation, meaning
    the exact leak pathway (background compositing? CSS transform scaling?
    mobile GPU specifics?) was not pinned down. This fix does not depend on
    knowing the exact pathway: regardless of WHICH mechanism ends up
    sampling slightly past an item's true edge, it will now find a
    plausible extension of the item's own color instead of unrelated
    neighbor/background color. Only touches pixels that are already fully
    invisible (alpha stays 0) - cannot affect hitboxes or visible size."""
    rgb = arr[:, :, :3].astype(np.float32).copy()
    filled = mask_bool.copy()
    for _ in range(spread_px):
        if filled.all():
            break
        unfilled = ~filled
        sum_rgb = np.zeros_like(rgb)
        count = np.zeros(filled.shape, dtype=np.float32)
        for dy, dx in [(-1,0),(1,0),(0,-1),(0,1)]:
            n_filled = _neighbor(filled, dy, dx)
            n_rgb = _neighbor(rgb, dy, dx)
            sum_rgb[n_filled] += n_rgb[n_filled]
            count[n_filled] += 1
        newly = unfilled & (count > 0)
        if not newly.any():
            break
        rgb[newly] = sum_rgb[newly] / count[newly][:, None]
        filled = filled | newly
    out = arr.copy()
    out[:, :, :3] = np.clip(rgb, 0, 255).astype(np.uint8)
    return out

def apply_polygon_mask(cropped_rgba, vertices_str, triangles_str, factor=4, dilate_px=0, spread_px=4):
    """'vertices' is a triangulated MESH vertex list (paired with
    'triangles'), not a perimeter outline. Filling actual triangles +
    binary threshold (dilate_px=0) is the confirmed-final fix for the
    fragment/overlap bug. spread_px adds edge-color bleeding into the
    invisible margin as a defensive measure against the still-unresolved
    crease issue - see _spread_edge_color()."""
    if not vertices_str or not triangles_str:
        return cropped_rgba
    w, h = cropped_rgba.size
    pts = parse_vertices(vertices_str)
    tris = parse_triangles(triangles_str)
    big_mask = Image.new('L', (w*factor, h*factor), 0)
    draw = ImageDraw.Draw(big_mask)
    for tri in tris:
        tri_pts = [(pts[i][0]*factor, pts[i][1]*factor) for i in tri]
        draw.polygon(tri_pts, fill=255)
    small_mask = big_mask.resize((w, h), Image.BOX)
    mask_bool = np.array(small_mask) > 10
    if dilate_px > 0:
        mask_bool = _dilate_mask(mask_bool, dilate_px)
    arr = np.array(cropped_rgba)
    if spread_px > 0:
        arr = _spread_edge_color(arr, mask_bool, spread_px)
    arr[..., 3] = np.where(mask_bool, arr[..., 3], 0)
    return Image.fromarray(arr, 'RGBA')

def pack_images(images_dict, padding=2):
    items = sorted(images_dict.items(), key=lambda kv: -kv[1].height)
    max_w = max(4096, max((img.width for _, img in items), default=0) + padding*2)
    x_cursor, y_cursor, row_height = padding, padding, 0
    positions = {}
    for key, img in items:
        w, h = img.size
        if x_cursor + w + padding > max_w:
            x_cursor = padding
            y_cursor += row_height + padding
            row_height = 0
        positions[key] = (x_cursor, y_cursor)
        row_height = max(row_height, h)
        x_cursor += w + padding
    atlas_h = y_cursor + row_height + padding
    return positions, max_w, atlas_h

def make_square_thumbnail(source_img, rect, out_path, size=400, pad_color=(34,34,34)):
    x, y, w, h = [int(v) for v in rect]
    cropped = source_img.crop((x, y, x + w, y + h)).convert("RGB")
    scale = max(size / w, size / h)
    new_w, new_h = max(1, int(w*scale)), max(1, int(h*scale))
    resized = cropped.resize((new_w, new_h), Image.LANCZOS)
    left = (new_w - size) // 2
    top = (new_h - size) // 2
    canvas = resized.crop((left, top, left + size, top + size))
    canvas.save(out_path, "JPEG", quality=80)

def make_bg_preview(source_img, rect, out_path, max_dim=1000):
    x, y, w, h = [int(v) for v in rect]
    cropped = source_img.crop((x, y, x + w, y + h)).convert("RGB")
    scale = min(1.0, max_dim / max(w, h))
    new_size = (max(1,int(w*scale)), max(1,int(h*scale)))
    resized = cropped.resize(new_size, Image.LANCZOS) if scale < 1.0 else cropped
    resized.save(out_path, "JPEG", quality=85)

def load_all_pages(tmp_dir):
    """CONFIRMED FIX: some puzzles split their atlas across MULTIPLE numbered
    pages (e.g. `0.webp`/`0.plist` PLUS `1.webp`/`1.plist`). Confirmed via
    direct zip inspection of puzzle 355765: all item frames live on page 0,
    but the entire background frame lives alone on page 1. This function
    discovers and merges ALL numbered pages generically."""
    frames = {}
    frame_page = {}
    page_images = {}
    for plist_path in sorted(glob.glob(os.path.join(tmp_dir, "*.plist"))):
        page_str = os.path.splitext(os.path.basename(plist_path))[0]
        try:
            page_num = int(page_str)
        except ValueError:
            continue
        webp_path = os.path.join(tmp_dir, f"{page_num}.webp")
        if not os.path.exists(webp_path):
            continue
        with open(plist_path, 'rb') as f:
            plist_data = plistlib.load(f)
        page_frames = plist_data.get('frames', {})
        for k, v in page_frames.items():
            frames[k] = v
            frame_page[k] = page_num
        page_images[page_num] = Image.open(webp_path).convert("RGBA")
    return frames, frame_page, page_images

def convert_one(zip_path, out_root, ledger=None, ledger_path=None):
    base_name = os.path.splitext(os.path.basename(zip_path))[0]
    tmp_dir = f"/tmp/g1_{base_name}"
    os.makedirs(tmp_dir, exist_ok=True)
    with zipfile.ZipFile(zip_path, 'r') as z:
        z.extractall(tmp_dir)

    bin_path = os.path.join(tmp_dir, "data.bin")

    frames, frame_page, page_images = load_all_pages(tmp_dir)
    if not frames:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise ValueError(f"No plist/webp atlas pages found for {zip_path}")

    with open(bin_path, 'rb') as f:
        raw = msgpack.unpackb(f.read(), raw=False)
    shapes = raw.get('shapes', [])
    layers = raw.get('layers', [])

    bg_key = next((k for k in frames if k.endswith('_background')), None)
    if bg_key is None:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise ValueError(f"No frame ending in '_background' found for {zip_path}")
    m = re.match(r'p(\d+)_background$', bg_key)
    puzzle_id = m.group(1) if m else bg_key.rsplit('_background', 1)[0].lstrip('p')

    if ledger is not None and puzzle_id in ledger:
        print(f"SKIPPED (duplicate, already in ledger) {zip_path}: puzzle_id={puzzle_id}")
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return

    bg_rect = parse_plist_rect(frames[bg_key]['textureRect'])
    bg_source_img = page_images[frame_page[bg_key]]

    masked_images = {}
    bx, by, bw, bh = [int(v) for v in bg_rect]
    masked_images[bg_key] = bg_source_img.crop((bx, by, bx + bw, by + bh))
    canvas_width, canvas_height = masked_images[bg_key].size

    def build_masked(key):
        info = frames[key]
        rect = parse_plist_rect(info['textureRect'])
        x, y, w, h = [int(v) for v in rect]
        page_img = page_images[frame_page[key]]
        crop = page_img.crop((x, y, x + w, y + h))
        crop = decontaminate_edges(crop)
        crop = apply_polygon_mask(crop, info.get('vertices'), info.get('triangles'))
        masked_images[key] = crop

    item_meta = []
    for shape in shapes:
        idx = shape['index']
        sprite_key = f"p{puzzle_id}_{idx}-0"
        thumb_key = f"p{puzzle_id}_{idx}-1"
        if sprite_key not in frames:
            continue
        build_masked(sprite_key)
        has_thumb = thumb_key in frames
        if has_thumb:
            build_masked(thumb_key)
        item_meta.append({
            "index": idx, "sprite_key": sprite_key,
            "thumb_key": thumb_key if has_thumb else None,
            "x": shape['x'], "y": canvas_height - shape['y'],
            "rotation": shape.get('rotation', 0), "zOrder": shape.get('zOrder', 0),
            "hitbox_polygon": rect_to_polygon(shape['rx'], shape['ry'])
        })

    decor_meta = []
    for layer in layers:
        key = f"p{puzzle_id}_{layer['name']}"
        if key not in frames:
            continue
        build_masked(key)
        decor_meta.append({
            "key": key, "x": layer['x'], "y": canvas_height - layer['y'],
            "rotation": 0, "zOrder": layer.get('zOrder', 0)
        })

    positions, atlas_w, atlas_h = pack_images(masked_images)
    new_atlas = Image.new("RGBA", (atlas_w, atlas_h), (0, 0, 0, 0))
    new_rects = {}
    for key, img in masked_images.items():
        px, py = positions[key]
        new_atlas.paste(img, (px, py), img)
        new_rects[key] = [px, py, img.width, img.height]

    items = []
    for meta in item_meta:
        items.append({
            "index": meta["index"],
            "sprite_rect": new_rects[meta["sprite_key"]],
            "thumb_rect": new_rects[meta["thumb_key"]] if meta["thumb_key"] else None,
            "x": meta["x"], "y": meta["y"],
            "rotation": meta["rotation"], "zOrder": meta["zOrder"],
            "hitbox_polygon": meta["hitbox_polygon"]
        })

    decor = []
    for meta in decor_meta:
        decor.append({
            "sprite_rect": new_rects[meta["key"]],
            "x": meta["x"], "y": meta["y"],
            "rotation": meta["rotation"], "zOrder": meta["zOrder"]
        })

    new_bg_rect = new_rects[bg_key]

    puzzle_folder_name = f"game1_{puzzle_id}"
    out_dir = os.path.join(out_root, puzzle_folder_name)
    os.makedirs(out_dir, exist_ok=True)
    # Switched from lossless to lossy (quality=90): the crease bug was
    # confirmed caused by mask SHAPE, not WebP compression (Theory 2 in
    # project history was tested and disproven). Lossless was leftover
    # caution from that disproven theory. Safe to shrink file size now.
    # Reverted to lossy: switching to lossless did NOT fix the crease issue
    # (confirmed by direct user test), so there's no reason to pay its
    # size/build-time cost while we diagnose the real cause via CREASE_DEBUG_DIR.
    new_atlas.save(os.path.join(out_dir, "atlas.webp"), quality=90, method=6)

    make_square_thumbnail(new_atlas, new_bg_rect, os.path.join(out_dir, "thumb.jpg"))
    make_bg_preview(new_atlas, new_bg_rect, os.path.join(out_dir, "bg.jpg"))

    data = {
        "puzzle_id": puzzle_folder_name,
        "source_game": "game1",
        "atlas": "atlas.webp",
        "thumbnail": "thumb.jpg",
        "background": "bg.jpg",
        "canvas_width": canvas_width,
        "canvas_height": canvas_height,
        "background_rect": new_bg_rect,
        "items": items,
        "decor": decor
    }
    with open(os.path.join(out_dir, "data.json"), 'w') as f:
        json.dump(data, f)

    if ledger is not None:
        append_ledger(ledger_path, puzzle_id)
        ledger.add(puzzle_id)

    shutil.rmtree(tmp_dir, ignore_errors=True)
    print(f"Converted {zip_path} -> {out_dir}")

if __name__ == "__main__":
    raw_dir = sys.argv[1]
    out_root = sys.argv[2]
    ledger_path = sys.argv[3] if len(sys.argv) > 3 else None
    ledger = load_ledger(ledger_path)
    os.makedirs(out_root, exist_ok=True)
    zips = glob.glob(os.path.join(raw_dir, "*"))
    for z in zips:
        try:
            convert_one(z, out_root, ledger, ledger_path)
        except Exception as e:
            print(f"FAILED {z}: {e}")
