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

def apply_polygon_mask(cropped_rgba, frame_info, factor=4):
    """CONFIRMED FIX (base): these atlases use TexturePacker's polygon packing
    mode (frames carry 'vertices' data), which allows irregularly-shaped
    sprites' bounding rectangles to overlap tightly-adjacent sprites. Masking
    the crop down to just the real polygon silhouette eliminates leaked
    neighbor fragments.

    UNCONFIRMED FOLLOW-UP FIX (this pass): the original hard 0/255 mask was
    reported to leave a visible sharp "crease" ring around items, since real
    sprite art has soft antialiased edges that a binary mask cuts through
    abruptly. This draws the mask at 4x resolution and downsamples with
    LANCZOS to get a smooth antialiased alpha edge instead. Verify this
    actually reduces the crease artifact on real puzzles before trusting it
    fully - last diagnostic test was inconclusive."""
    vertices_str = frame_info.get('vertices')
    if not vertices_str:
        return cropped_rgba
    w, h = cropped_rgba.size
    pts = [(x*factor, y*factor) for x, y in parse_vertices(vertices_str)]
    big_mask = Image.new('L', (w*factor, h*factor), 0)
    draw = ImageDraw.Draw(big_mask)
    draw.polygon(pts, fill=255)
    mask = big_mask.resize((w, h), Image.LANCZOS)
    r, g, b, a = cropped_rgba.split()
    new_a = Image.composite(a, Image.new('L', (w, h), 0), mask)
    cropped_rgba.putalpha(new_a)
    return cropped_rgba

def make_square_thumbnail(source_img, rect, out_path, size=400, pad_color=(34,34,34)):
    x, y, w, h = [int(v) for v in rect]
    cropped = source_img.crop((x, y, x + w, y + h)).convert("RGB")
    # CONFIRMED FIX: crop-to-fill (cover) instead of pad-to-fit, so square
    # thumbnails have zero padding/letterboxing regardless of source aspect.
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

    sample_key = next(iter(frames.keys()))
    m = re.match(r'p(\d+)_', sample_key)
    if not m:
        raise ValueError(f"Could not find puzzle id in plist frames for {zip_path}")
    puzzle_id = m.group(1)

    bg_key = f"p{puzzle_id}_background"
    bg_rect = parse_plist_rect(frames[bg_key]['textureRect'])
    canvas_width = bg_rect[2]
    canvas_height = bg_rect[3]

    source_img = Image.open(webp_path).convert("RGBA")

    # Build a NEW, pre-masked atlas image: every sprite/decor frame gets its
    # polygon mask baked in (as real alpha transparency) and is pasted back at
    # its original atlas coordinates. This keeps the existing rect-based
    # frontend rendering contract unchanged (game.js still just crops
    # sprite_rect out of the atlas) while eliminating leaked-neighbor artifacts.
    masked_atlas = Image.new("RGBA", source_img.size, (0, 0, 0, 0))

    # Background gets pasted as-is (no masking needed/applicable for the bg frame).
    bx, by, bw, bh = [int(v) for v in bg_rect]
    bg_crop = source_img.crop((bx, by, bx + bw, by + bh))
    masked_atlas.paste(bg_crop, (bx, by))

    def stamp_masked_frame(key):
        info = frames[key]
        rect = parse_plist_rect(info['textureRect'])
        x, y, w, h = [int(v) for v in rect]
        crop = source_img.crop((x, y, x + w, y + h))
        crop = apply_polygon_mask(crop, info)
        masked_atlas.paste(crop, (x, y), crop)

    items = []
    for shape in shapes:
        idx = shape['index']
        sprite_key = f"p{puzzle_id}_{idx}-0"
        thumb_key = f"p{puzzle_id}_{idx}-1"
        sprite_rect = parse_plist_rect(frames[sprite_key]['textureRect']) if sprite_key in frames else None
        thumb_rect = parse_plist_rect(frames[thumb_key]['textureRect']) if thumb_key in frames else None
        if sprite_rect is None:
            continue
        stamp_masked_frame(sprite_key)
        if thumb_key in frames:
            stamp_masked_frame(thumb_key)
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
        stamp_masked_frame(key)
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
    masked_atlas.save(os.path.join(out_dir, "atlas.webp"))

    make_square_thumbnail(masked_atlas, bg_rect, os.path.join(out_dir, "thumb.jpg"))
    make_bg_preview(masked_atlas, bg_rect, os.path.join(out_dir, "bg.jpg"))

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
