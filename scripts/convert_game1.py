#!/usr/bin/env python3
import sys, os, re, zipfile, json, plistlib, msgpack, shutil, glob
from PIL import Image, ImageDraw

def parse_plist_rect(s):
    nums = re.findall(r'-?\d+\.?\d*', s)
    return [float(n) for n in nums]

def parse_vertices(s):
    nums = [float(n) for n in s.split()]
    return list(zip(nums[0::2], nums[1::2]))

def rect_to_polygon(rx, ry):
    return [
        {"x": -rx, "y": -ry},
        {"x": rx, "y": -ry},
        {"x": rx, "y": ry},
        {"x": -rx, "y": ry},
    ]

def apply_polygon_mask(cropped_rgba, vertices_str, factor=4):
    if not vertices_str:
        return cropped_rgba
    w, h = cropped_rgba.size
    pts = [(x*factor, y*factor) for x, y in parse_vertices(vertices_str)]
    big_mask = Image.new('L', (w*factor, h*factor), 0)
    ImageDraw.Draw(big_mask).polygon(pts, fill=255)
    mask = big_mask.resize((w, h), Image.LANCZOS)
    r, g, b, a = cropped_rgba.split()
    new_a = Image.composite(a, Image.new('L', (w, h), 0), mask)
    cropped_rgba.putalpha(new_a)
    return cropped_rgba

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

def convert_one(zip_path, out_root):
    base_name = os.path.splitext(os.path.basename(zip_path))[0]
    tmp_dir = f"/tmp/g1_{base_name}"
    os.makedirs(tmp_dir, exist_ok=True)
    with zipfile.ZipFile(zip_path, 'r') as z:
        z.extractall(tmp_dir)

    webp_path = os.path.join(tmp_dir, "0.webp")
    plist_path = os.path.join(tmp_dir, "0.plist")
    bin_path = os.path.join(tmp_dir, "data.bin")

    with open(plist_path, 'rb') as f:
        plist_data = plistlib.load(f)
    frames = plist_data['frames']

    with open(bin_path, 'rb') as f:
        raw = msgpack.unpackb(f.read(), raw=False)
    shapes = raw.get('shapes', [])
    layers = raw.get('layers', [])

    bg_key = next((k for k in frames if k.endswith('_background')), None)
    if bg_key is None:
        raise ValueError(f"No frame ending in '_background' found for {zip_path}")
    m = re.match(r'p(\d+)_background$', bg_key)
    puzzle_id = m.group(1) if m else bg_key.rsplit('_background', 1)[0].lstrip('p')

    bg_rect = parse_plist_rect(frames[bg_key]['textureRect'])
    canvas_width = bg_rect[2]
    canvas_height = bg_rect[3]

    source_img = Image.open(webp_path).convert("RGBA")

    masked_images = {}
    bx, by, bw, bh = [int(v) for v in bg_rect]
    masked_images[bg_key] = source_img.crop((bx, by, bx + bw, by + bh))

    def build_masked(key):
        # RESTORED: masking IS necessary - confirmed by regression test.
        # Polygon-packed atlases legitimately overlap sprites' rectangular
        # bounds (only the polygons themselves are guaranteed non-overlapping),
        # so cropping a raw rectangle can and does pick up real neighbor pixels.
        info = frames[key]
        rect = parse_plist_rect(info['textureRect'])
        x, y, w, h = [int(v) for v in rect]
        crop = source_img.crop((x, y, x + w, y + h))
        crop = apply_polygon_mask(crop, info.get('vertices'))
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
    new_atlas.save(os.path.join(out_dir, "atlas.webp"), lossless=True, quality=100, method=6)

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

    shutil.rmtree(tmp_dir)
    print(f"Converted {zip_path} -> {out_dir}")

if __name__ == "__main__":
    raw_dir = sys.argv[1]
    out_root = sys.argv[2]
    os.makedirs(out_root, exist_ok=True)
    zips = glob.glob(os.path.join(raw_dir, "*"))
    for z in zips:
        try:
            convert_one(z, out_root)
        except Exception as e:
            print(f"FAILED {z}: {e}")
