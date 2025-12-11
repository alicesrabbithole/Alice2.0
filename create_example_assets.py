# Generates example stocking/buildable assets for testing the StockingCog/Rumble listener.
# Creates:
#  - data/stocking_assets/buildables/snowman/base.png
#  - data/stocking_assets/buildables/snowman/parts/carrot.png
#  - data/stocking_assets/buildables/snowman/parts/hat.png
#  - data/stocking_assets/buildables/snowman/parts/scarf.png
#  - data/stocking_assets/stickers/snowman_sticker.png
#  - data/stocking_assets/stickers/cookie.png
#  - data/stocking_assets/stickers/candy_cane.png
# Also writes sample data/buildables.json and data/stickers.json (used by the cog).
#
# Usage:
#   python create_example_assets.py
#
# Requires Pillow:
#   pip install pillow

from pathlib import Path
from PIL import Image, ImageDraw

BASE_DIR = Path("data") / "stocking_assets"
BUILDABLE_DIR = BASE_DIR / "buildables" / "snowman"
PARTS_DIR = BUILDABLE_DIR / "parts"
STICKERS_DIR = BASE_DIR / "stickers"
DATA_DIR = Path("data")
DATA_DIR.mkdir(parents=True, exist_ok=True)
BASE_DIR.mkdir(parents=True, exist_ok=True)
BUILDABLE_DIR.mkdir(parents=True, exist_ok=True)
PARTS_DIR.mkdir(parents=True, exist_ok=True)
STICKERS_DIR.mkdir(parents=True, exist_ok=True)

# -- Create snowman base (600x800) --
W, H = 600, 800
base = Image.new("RGBA", (W, H), (0, 0, 0, 0))
draw = ImageDraw.Draw(base)

# Draw three snowball circles (bottom, middle, head)
# bottom
draw.ellipse((120, 420, 480, 780), fill=(255, 255, 255, 255), outline=(0, 0, 0, 255), width=4)
# middle
draw.ellipse((180, 220, 420, 460), fill=(255, 255, 255, 255), outline=(0, 0, 0, 255), width=4)
# head
draw.ellipse((240, 90, 360, 210), fill=(255, 255, 255, 255), outline=(0, 0, 0, 255), width=4)

# subtle shadow under middle
draw.ellipse((200, 430, 400, 460), fill=(220, 220, 220, 60))

base_path = BUILDABLE_DIR / "base.png"
base.save(base_path)

# -- Create carrot part (simple orange triangle) --
carrot = Image.new("RGBA", (120, 60), (0, 0, 0, 0))
d = ImageDraw.Draw(carrot)
d.polygon([(0, 30), (90, 10), (90, 50)], fill=(255, 140, 0, 255))  # triangular carrot
d.rectangle((90, 22, 119, 38), fill=(160, 82, 45, 255))  # stem / connector
carrot_path = PARTS_DIR / "carrot.png"
carrot.save(carrot_path)

# -- Create hat part (simple top hat) --
hat = Image.new("RGBA", (180, 100), (0, 0, 0, 0))
d = ImageDraw.Draw(hat)
d.rectangle((10, 30, 170, 80), fill=(20, 20, 20, 255))  # top part
d.rectangle((0, 75, 180, 95), fill=(15, 15, 15, 255))  # brim
hat_path = PARTS_DIR / "hat.png"
hat.save(hat_path)

# -- Create scarf part (simple band) --
scarf = Image.new("RGBA", (260, 80), (0, 0, 0, 0))
d = ImageDraw.Draw(scarf)
d.rectangle((0, 20, 260, 60), fill=(220, 20, 60, 255))  # scarf band
# a little tail
d.rectangle((30, 60, 80, 100), fill=(220, 20, 60, 255))
scarf_path = PARTS_DIR / "scarf.png"
scarf.save(scarf_path)

# -- Create small sticker icons (snowman face, cookie, candy cane) --
# snowman sticker (80x80)
s1 = Image.new("RGBA", (80, 80), (0, 0, 0, 0))
d = ImageDraw.Draw(s1)
d.ellipse((0, 20, 80, 80), fill=(255, 255, 255, 255), outline=(0, 0, 0, 255))
d.ellipse((20, 40, 34, 54), fill=(0, 0, 0, 255))  # eye
d.ellipse((46, 40, 60, 54), fill=(0, 0, 0, 255))  # eye
d.polygon([(36, 50), (60, 46), (36, 44)], fill=(255, 140, 0, 255))  # tiny carrot
s1_path = STICKERS_DIR / "snowman_sticker.png"
s1.save(s1_path)

# cookie sticker (64x64)
c = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
d = ImageDraw.Draw(c)
d.ellipse((0, 0, 64, 64), fill=(210, 140, 80, 255))
# chips
d.ellipse((10, 20, 18, 28), fill=(60, 30, 10, 255))
d.ellipse((36, 10, 44, 18), fill=(60, 30, 10, 255))
d.ellipse((42, 34, 50, 42), fill=(60, 30, 10, 255))
c_path = STICKERS_DIR / "cookie.png"
c.save(c_path)

# candy cane sticker (50x80)
cc = Image.new("RGBA", (40, 80), (0, 0, 0, 0))
d = ImageDraw.Draw(cc)
# draw stripes by rectangles/lines
for i in range(0, 80, 10):
    color = (255, 255, 255, 255) if (i // 10) % 2 == 0 else (220, 0, 60, 255)
    d.rectangle((10, i, 30, i + 10), fill=color)
# curved top: draw circle and mask
d.ellipse((0, -10, 40, 30), fill=(220, 0, 60, 255))
cc_path = STICKERS_DIR / "candy_cane.png"
cc.save(cc_path)

# -- Write sample data/stickers.json and data/buildables.json --
import json
stickers_def = {
    "snowman": {"file": "stickers/snowman_sticker.png", "slots": 1},
    "cookie": {"file": "stickers/cookie.png", "slots": 1},
    "candy": {"file": "stickers/candy_cane.png", "slots": 1}
}
with open(DATA_DIR / "stickers.json", "w", encoding="utf-8") as fh:
    json.dump(stickers_def, fh, ensure_ascii=False, indent=2)

buildables_def = {
    "snowman": {
        "base": "buildables/snowman/base.png",
        "parts": {
            "carrot": {"file": "buildables/snowman/parts/carrot.png", "offset": [350, 170], "z": 10},
            "hat": {"file": "buildables/snowman/parts/hat.png", "offset": [210, 60], "z": 30},
            "scarf": {"file": "buildables/snowman/parts/scarf.png", "offset": [170, 240], "z": 20}
        },
        "capacity_slots": 3,
        "role_on_complete": None
    }
}
with open(DATA_DIR / "buildables.json", "w", encoding="utf-8") as fh:
    json.dump(buildables_def, fh, ensure_ascii=False, indent=2)

print("Example assets and JSON written under data/stocking_assets and data/*.json")
print(" - Base: ", base_path)
print(" - Parts: ", carrot_path, hat_path, scarf_path)
print(" - Stickers: ", s1_path, c_path, cc_path)
print(" - Definitions: data/stickers.json, data/buildables.json")