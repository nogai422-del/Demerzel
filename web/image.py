# Выдача и on-the-fly подготовка изображений для веб-панели.

import os
import re
from flask import Blueprint, request, send_file, abort
from PIL import Image, ImageOps

image_bp = Blueprint("image", __name__)

BASE_DIR = os.path.dirname(os.path.dirname(__file__))

ALLOWED = {
    "levels": os.path.join(BASE_DIR, "bot/images/rank_images"),
    "images": os.path.join(BASE_DIR, "bot/images/vibe_images"),
    "permission": os.path.join(BASE_DIR, "bot/images/permission_images"),
    "scheduled": os.path.join(BASE_DIR, "bot/images/scheduled_images"),
}

MAX_SIDE = 2048
JPEG_QUALITY = 90


# Отдает изображение из папки rank_images по имени файла.
@image_bp.route("/image")
def serve_image():
    type_ = request.args.get("type")
    if type_ not in ALLOWED:
        abort(400)

    file = os.path.basename(request.args.get("img", ""))

    if not re.search(r"\.(jpe?g|png|gif)$", file, re.I):
        abort(404)

    folder = ALLOWED[type_]
    original = os.path.join(folder, file)

    if not os.path.isfile(original):
        abort(404)

    base_name, ext = os.path.splitext(file)
    ext = ext.lower().lstrip(".")
    jpg_name = f"{base_name}.jpg"
    jpg_path = os.path.join(folder, jpg_name)

    if os.path.isfile(jpg_path) and jpg_path != original:
        return send_file(jpg_path, conditional=True, max_age=86400)

    try:
        img = Image.open(original)
        img = ImageOps.exif_transpose(img)

        if ext == "gif" and getattr(img, "is_animated", False):
            img.seek(0)

        w, h = img.size
        if max(w, h) > MAX_SIDE:
            img.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)

        if img.mode in ("RGBA", "LA", "P"):
            img = img.convert("RGB")

        img.save(
            jpg_path,
            format="JPEG",
            quality=JPEG_QUALITY,
            optimize=True,
            progressive=True,
        )

        if original != jpg_path:
            try:
                os.remove(original)
            except OSError:
                pass

        return send_file(jpg_path, conditional=True, max_age=86400)

    except Exception as e:
        print("IMAGE ERROR:", e)
        return send_file(original, conditional=True, max_age=86400)
