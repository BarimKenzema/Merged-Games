#!/usr/bin/env python3
import sys, os, re, json, glob
import UnityPy
import texture2ddecoder
from PIL import Image

def load_ledger(path):
    if not path:
        return set()
    try:
        with open(path) as f:
            return set(line.strip() for line in f if line.strip())
    except FileNotFoundError:
        return set()

def append_ledger(path, level_id):
    if not path:
        return
    with open(path, 'a') as f:
        f.write(level_id + '\n')

def parse_sprite_name(name):
    n = name
    if n.lower().endswith('.png'):
        n = n[:-4]
    parts = n.split('_')
    is_shadow_variant = False
    if parts and parts[-1] == 'shadow':
        is_shadow_variant = True
        parts = parts[:-1]
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
    if is_shadow_variant:
        item_type = item_type + 'shadow'
    return {
        "level_id": level_id, "item_type": item_type, "item_number": item_number,
        "rotation": rotation, "x": x, "y": y, "layer": layer, "name": name
    }

def make_square_thumbnail(source_img, rect, out_path, size=300, pad_color=(34,34,34)):
    x, y, w, h = rect
    cropped = source_img.crop((x, y, x + w, y + h)).convert("RGB")
    scale = max(size / w, size / h)
    new_w, new_h = max(1, int(w*scale)), max(1, int(h*scale))
    resized = cropped.resize((new_w, new_h), Image.LANCZOS)
    left = (new_w - size) // 2
    top = (new_h - size) // 2
    canvas = resized.crop((left, top, left + size, top + size))
    canvas.save(out_path, "WEBP", quality=80, method=6)

def make_bg_preview(source_img, rect, out_path, max_dim=1000):
    x, y, w, h = rect
    cropped = source_img.crop((x, y, x + w, y + h)).convert("RGB")
    scale = min(1.0, max_dim / max(w, h))
    new_size = (max(1,int(w*scale)), max(1,int(h*scale)))
    resized = cropped.resize(new_size, Image.LANCZOS) if scale < 1.0 else cropped
    resized.save(out_path, "JPEG", quality=85)

def resolve_polygon(path_id, mono_by_pathid, depth=0):
    if depth > 3:
        return None, None
    tree = mono_by_pathid.get(path_id)
    if tree is None:
        return None, None
    if 'polygonPoints' in tree:
        return tree, depth
    if 'item' in tree and isinstance(tree['item'], dict) and 'm_PathID' in tree['item']:
        return resolve_polygon(tree['item']['m_PathID'], mono_by_pathid, depth+1)
    return None, None

def convert_one(bundle_path, out_root, ledger=None, ledger_path=None):
    level_id = os.path.basename(bundle_path)

    if ledger is not None and level_id in ledger:
        print(f"SKIPPED (duplicate, already in ledger) {bundle_path}: level_id={level_id}")
        return

    env = UnityPy.load(bundle_path)

    tex_objs = [o.read() for o in env.objects if o.type.name == "Texture2D"]
    if not tex_objs:
        raise ValueError("No texture found")
    tex = tex_objs[0]
    w, h = tex.m_Width, tex.m_Height
    raw = tex.image_data
    decoded = texture2ddecoder.decode_astc(raw, w, h, 10, 10)
    atlas = Image.frombytes('RGBA', (w, h), decoded, 'raw', 'BGRA')
    atlas = atlas.transpose(Image.FLIP_TOP_BOTTOM)
    atlas_h = atlas.height

    sprite_rects = {}
    for obj in env.objects:
        if obj.type.name == "Sprite":
            d = obj.read()
            r = d.m_RD.textureRect
            x, y, sw, sh = int(round(r.x)), int(round(r.y)), int(round(r.width)), int(round(r.height))
            top = atlas_h - y - sh
            sprite_rects[d.m_Name] = [x, top, sw, sh]

    level_tree = None
    mono_by_pathid = {}
    for obj in env.objects:
        if obj.type.name == "MonoBehaviour":
            tree = obj.read_typetree()
            mono_by_pathid[obj.path_id] = tree
            if 'hiddenPoints' in tree and 'decorPoints' in tree:
                level_tree = tree

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
        poly_data, hops = resolve_polygon(path_id, mono_by_pathid)
        if poly_data is None:
            continue
        polygon_pixels = [
            {"x": p['x'] * 100.0, "y": -p['y'] * 100.0}
            for p in poly_data['polygonPoints']
        ]
        final_x = canvas_w / 2 + info['x']
        if hops == 0:
            final_y = canvas_h / 2 - info['y']
        else:
            final_y = canvas_h / 2 + info['y']
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
        if not info or info['item_type'] not in ('decor', 's', 'hshadow'):
            continue
        final_x = canvas_w / 2 + info['x']
        final_y = canvas_h / 2 + info['y']
        decor_entry = {
            "sprite_rect": sprite_rects[name],
            "x": final_x,
            "y": final_y,
            "rotation": info['rotation'],
            "zOrder": info['layer']
        }
        if info['item_type'] in ('hshadow', 's') and info['item_number'] > 0:
            decor_entry['linked_item_index'] = info['item_number'] - 1
        decor.append(decor_entry)

    puzzle_folder_name = f"game2_{level_id}"
    out_dir = os.path.join(out_root, puzzle_folder_name)
    os.makedirs(out_dir, exist_ok=True)
    # Switched from lossless PNG to lossy WebP (quality=90) for size reduction.
    # Reverted to lossy: Game 2 was never implicated in the crease bug (its
    # sprites come pre-masked from the original Unity asset alpha - no custom
    # polygon cutout is applied by us here). This switch is purely about file
    # size, independent of the Game 1 crease investigation.
    atlas.save(os.path.join(out_dir, "atlas.webp"), quality=40, method=6)

    make_square_thumbnail(atlas, bg_rect, os.path.join(out_dir, "thumb.webp"))
    make_bg_preview(atlas, bg_rect, os.path.join(out_dir, "bg.webp"))

    data = {
        "puzzle_id": puzzle_folder_name,
        "source_game": "game2",
        "atlas": "atlas.webp",
        "thumbnail": "thumb.webp",
        "background": "bg.webp",
        "canvas_width": canvas_w,
        "canvas_height": canvas_h,
        "background_rect": bg_rect,
        "items": items,
        "decor": decor
    }
    with open(os.path.join(out_dir, "data.json"), 'w') as f:
        json.dump(data, f)

    if ledger is not None:
        append_ledger(ledger_path, level_id)
        ledger.add(level_id)

    print(f"Converted {bundle_path} -> {out_dir}  (items={len(items)}, decor={len(decor)})")

if __name__ == "__main__":
    raw_dir = sys.argv[1]
    out_root = sys.argv[2]
    ledger_path = sys.argv[3] if len(sys.argv) > 3 else None
    ledger = load_ledger(ledger_path)
    os.makedirs(out_root, exist_ok=True)
    files = glob.glob(os.path.join(raw_dir, "*"))
    for fpath in files:
        try:
            convert_one(fpath, out_root, ledger, ledger_path)
        except Exception as e:
            print(f"FAILED {fpath}: {e}")
