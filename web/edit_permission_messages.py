# Редактирование сообщений ограничений и медиа для предупреждений модерации.

import os
import re
import uuid
import hmac
import secrets
from flask import Blueprint, render_template, request, redirect, session
from bot.database import db

edit_permission_bp = Blueprint("edit_permission", __name__)

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "bot/images/permission_images")

os.makedirs(UPLOAD_DIR, exist_ok=True)


# Преобразует данные в нужный формат.
def convert_user_tag(msg: str) -> str:
    return re.sub(
        r"\{user\}",
        '<a href="tg://user?id={user_id}">{full_name}</a>',
        msg
    )


# Готовит сообщение прав к отображению в веб-форме.
def display_message(msg: str) -> str:
    return re.sub(
        r'<a href="tg:\/\/user\?id=\{user_id\}">\{full_name\}<\/a>',
        "{user}",
        msg
    )


# Обрабатывает редактирование текстов ограничений и кнопок.
@edit_permission_bp.route("/edit_permission_messages", methods=["GET", "POST"])
async def edit_permission_messages():

    if "username" not in session:
        return redirect("/login")

    session.setdefault("csrf_token", secrets.token_urlsafe(32))

    if request.method == "POST":
        sent_token = request.form.get("csrf_token", "")
        session_token = session.get("csrf_token", "")
        if not sent_token or not session_token or not hmac.compare_digest(sent_token, session_token):
            return redirect("/edit_permission_messages?csrf=0")

        media_rows = {}

        for key in request.form:
            if not key.startswith("media["):
                continue

            parts = re.findall(r"\[(.*?)\]", key)
            if len(parts) != 2:
                continue

            media_type, field = parts
            media_rows.setdefault(media_type, {})
            media_rows[media_type][field] = request.form.get(key)

        async with db() as cur:
            for media_type, row in media_rows.items():
                message = row.get("message", "")
                image_path = row.get("image_path", "")
                button_text = row.get("button_text", "")
                button_url = row.get("button_url", "")

                file = request.files.get(f"media[{media_type}][upload]")

                if media_type != "emoji" and file and file.filename:
                    ext = file.filename.rsplit(".", 1)[-1].lower()
                    if ext in ["jpg", "jpeg", "png", "gif"]:
                        new_name = f"img_{uuid.uuid4().hex}.{ext}"
                        full_path = os.path.join(UPLOAD_DIR, new_name)
                        file.save(full_path)
                        image_path = f"permission_images/{new_name}"

                message = convert_user_tag(message)

                await cur.execute("""
                    UPDATE permission_types SET
                        message     = ?,
                        image_path  = ?,
                        button_text = ?,
                        button_url  = ?
                    WHERE media_type = ?
                """, (
                    message,
                    image_path,
                    button_text,
                    button_url,
                    media_type
                ))

        return redirect("/edit_permission_messages?saved=1")

    async with db() as cur:
        await cur.execute("SELECT * FROM permission_types ORDER BY media_type ASC")
        rows = await cur.fetchall()

    media_list = []
    for r in rows:
        media_list.append({
            "media_type": r[0],
            "title": r[1],
            "message": display_message(r[2] or ""),
            "image_path": r[3],
            "button_text": r[4],
            "button_url": r[5],
        })

    return render_template(
        "edit_permission_messages.html",
        media_list=media_list,
        saved=request.args.get("saved"),
        csrf=request.args.get("csrf"),
        csrf_token=session["csrf_token"],
    )
