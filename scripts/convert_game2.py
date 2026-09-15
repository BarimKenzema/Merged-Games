#!/usr/bin/env python3
import sys, os, re, json, glob
import UnityPy
import texture2ddecoder
from PIL import Image

def parse_sprite_name(name):
    n = name
    if n.lower().endswith('.png'):
        n = n[:-4]
    parts = n.split('_')
    if len(parts) < 5:
        return None
    level_id = parts[0]
    type_field = parts[1]
    rotation = 0
    ro_match = re.search(r'ro(-?\d+)', type_field)
    if ro_match:
        rotation = int(ro_match.group(1))
        type_field = type_field[:ro_match.start()]
    type_match = re.match(r'([a-z]+)(\d*)', type_field)
    item_type = type_match.group(1) if type_match else type_field
    item_number = int(type_match.group(2)) if type_match and type_match.group(2) else 0
    try:
        x = float(parts[2][1:])
        y = float(parts[3][1:])
        layer = int(parts[4][1:])
    except Exception:
        return None
    return {
        "level_id": level_id, "item_type": item_type, "item_number": item_number,
        "rotation": rotation, "x": x, "y": y, "layer": layer, "name": name
    }

def make_thumbnail_from_atlas(atlas_img, rect, out_path, max_w=320):
    x, y, w, h = rect
    cropped = atlas_img.crop((x, y, x + w, y + h)).convert("RGB")
    ratio = max_w / w
    new_size = (max_w, max(1, int(h * ratio)))
    thumb = cropped.resize(new_size, Image.LANCZOS)
    thumb.save(out_path, "JPEG", quality=75)

def convert_one(bundle_path, out_root):
    level_id = os.path.basename(bundle_path)
    env = UnityPy.load(bundle_path)

    tex_objs = [o.read() for o in env.objects if o.type.name == "Texture2D"]
    if not tex_objs:
        raise ValueError("No texture found")
    tex = tex_objs[0]
    w, h = tex.m_Width, tex.m_Height
    raw = tex.image_data
    decoded = texture2ddecoder.decode_astc(raw, w, h, 10, 10)
    atlas = Image.frombytes('RGBA', (w, h), decoded)
    atlas = atlas.transpose(Image.FLIP_TOP_BOTTOM)
    atlas_h = atlas.height

    sprite_rects = {}
    for obj in env.objects:
        if obj.type.name == "Sprite":
            d = obj.read()
            r = d.m_Rect
            x, y, sw, sh = int(r.x), int(r.y), int(r.width), int(r.height)
            top = atlas_h - y - sh
            sprite_rects[d.m_Name] = [x, top, sw, sh]

    level_tree = None
    mono_polys = {}
    for obj in env.objects:
        if obj.type.name == "MonoBehaviour":
            tree = obj.read_typetree()
            if 'hiddenPoints' in tree and 'decorPoints' in tree:
                level_tree = tree
            elif 'polygonPoints' in tree:
                mono_polys[obj.path_id] = tree

    if level_tree is None:
        raise ValueError("No Level data found")

    bg_name = None
    for name in sprite_rects:
        info = parse_sprite_name(name)
        if info and info['item_type'] == 'bg':
            bg_name = name
            break
    if bg_name is None:
        raise ValueError("No background sprite found")
    bg_rect = sprite_rects[bg_name]
    canvas_w, canvas_h = bg_rect[2], bg_rect[3]

    sprite_by_type_num = {}
    for name in sprite_rects:
        info = parse_sprite_name(name)
        if info:
            sprite_by_type_num[(info['item_type'], info['item_number'])] = (name, info)

    items = []
    for i, ref in enumerate(level_tree['hiddenPoints']):
        item_number = i + 1
        key = ('h', item_number)
        if key not in sprite_by_type_num:
            continue
        sprite_name, info = sprite_by_type_num[key]
        path_id = ref['m_PathID']
        poly_data = mono_polys.get(path_id)
        if poly_data is None:
            continue
        polygon_pixels = [
            {"x": p['x'] * 100.0, "y": -p['y'] * 100.0}
            for p in poly_data['polygonPoints']
        ]
        final_x = canvas_w / 2 + info['x']
        final_y = canvas_h / 2 - info['y']
        items.append({
            "index": i,
            "sprite_rect": sprite_rects[sprite_name],
            "thumb_rect": None,
            "x": final_x,
            "y": final_y,
            "rotation": info['rotation'],
            "zOrder": info['layer'],
            "hitbox_polygon": polygon_pixels
        })

    decor = []
    for name in sprite_rects:
        info = parse_sprite_name(name)
        if not info or info['item_type'] not in ('decor', 's'):
            continue
        final_x = canvas_w / 2 + info['x']
        final_y = canvas_h / 2 - info['y']
        decor.append({
            "sprite_rect": sprite_rects[name],
            "x": final_x,
            "y": final_y,
            "rotation": info['rotation'],
            "zOrder": info['layer']
        })

    puzzle_folder_name = f"game2_{level_id}"
    out_dir = os.path.join(out_root, puzzle_folder_name)
    os.makedirs(out_dir, exist_ok=True)
    atlas.save(os.path.join(out_dir, "atlas.png"))

    thumb_path = os.path.join(out_dir, "thumb.jpg")
    make_thumbnail_from_atlas(atlas, bg_rect, thumb_path)

    data = {
        "puzzle_id": puzzle_folder_name,
        "source_game": "game2",
        "atlas": "atlas.png",
        "thumbnail": "thumb.jpg",
        "canvas_width": canvas_w,
        "canvas_height": canvas_h,
        "background_rect": bg_rect,
        "items": items,
        "decor": decor
    }
    with open(os.path.join(out_dir, "data.json"), 'w') as f:
        json.dump(data, f)

    print(f"Converted {bundle_path} -> {out_dir}")

if __name__ == "__main__":
    raw_dir = sys.argv[1]
    out_root = sys.argv[2]
    os.makedirs(out_root, exist_ok=True)
    files = glob.glob(os.path.join(raw_dir, "*"))
    for fpath in files:
        try:
            convert_one(fpath, out_root)
        except Exception as e:
            print(f"FAILED {fpath}: {e}")
