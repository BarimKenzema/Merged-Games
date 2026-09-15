#!/usr/bin/env python3
import sys, os, re, zipfile, json, plistlib, msgpack, shutil, glob
from PIL import Image

def parse_plist_rect(s):
    nums = re.findall(r'-?\d+\.?\d*', s)
    return [float(n) for n in nums]

def rect_to_polygon(rx, ry):
    return [
        {"x": -rx, "y": -ry},
        {"x": rx, "y": -ry},
        {"x": rx, "y": ry},
        {"x": -rx, "y": ry},
    ]

def make_square_thumbnail(source_img, rect, out_path, size=400, pad_color=(34,34,34)):
    x, y, w, h = [int(v) for v in rect]
    cropped = source_img.crop((x, y, x + w, y + h)).convert("RGB")
    scale = min(size / w, size / h)
    new_w, new_h = max(1, int(w*scale)), max(1, int(h*scale))
    resized = cropped.resize((new_w, new_h), Image.LANCZOS)
    canvas = Image.new("RGB", (size, size), pad_color)
    canvas.paste(resized, ((size-new_w)//2, (size-new_h)//2))
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

    sample_key = next(iter(frames.keys()))
    m = re.match(r'p(\d+)_', sample_key)
    if not m:
        raise ValueError(f"Could not find puzzle id in plist frames for {zip_path}")
    puzzle_id = m.group(1)

    bg_key = f"p{puzzle_id}_background"
    bg_rect = parse_plist_rect(frames[bg_key]['textureRect'])
    canvas_width = bg_rect[2]
    canvas_height = bg_rect[3]

    items = []
    for shape in shapes:
        idx = shape['index']
        sprite_key = f"p{puzzle_id}_{idx}-0"
        thumb_key = f"p{puzzle_id}_{idx}-1"
        sprite_rect = parse_plist_rect(frames[sprite_key]['textureRect']) if sprite_key in frames else None
        thumb_rect = parse_plist_rect(frames[thumb_key]['textureRect']) if thumb_key in frames else None
        if sprite_rect is None:
            continue
        items.append({
            "index": idx,
            "sprite_rect": sprite_rect,
            "thumb_rect": thumb_rect,
            "x": shape['x'],
            "y": canvas_height - shape['y'],
            "rotation": shape.get('rotation', 0),
            "zOrder": shape.get('zOrder', 0),
            "hitbox_polygon": rect_to_polygon(shape['rx'], shape['ry'])
        })

    decor = []
    for layer in layers:
        name = layer['name']
        key = f"p{puzzle_id}_{name}"
        if key not in frames:
            continue
        rect = parse_plist_rect(frames[key]['textureRect'])
        decor.append({
            "sprite_rect": rect,
            "x": layer['x'],
            "y": canvas_height - layer['y'],
            "rotation": 0,
            "zOrder": layer.get('zOrder', 0)
        })

    puzzle_folder_name = f"game1_{puzzle_id}"
    out_dir = os.path.join(out_root, puzzle_folder_name)
    os.makedirs(out_dir, exist_ok=True)
    shutil.copy(webp_path, os.path.join(out_dir, "atlas.webp"))

    source_img = Image.open(webp_path)
    make_square_thumbnail(source_img, bg_rect, os.path.join(out_dir, "thumb.jpg"))
    make_bg_preview(source_img, bg_rect, os.path.join(out_dir, "bg.jpg"))

    data = {
        "puzzle_id": puzzle_folder_name,
        "source_game": "game1",
        "atlas": "atlas.webp",
        "thumbnail": "thumb.jpg",
        "background": "bg.jpg",
        "canvas_width": canvas_width,
        "canvas_height": canvas_height,
        "background_rect": bg_rect,
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
