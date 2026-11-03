"""
Kanal/guruhlarga rejali post yuboruvchi bot (ChatGPT bilan) - TUGMALI versiya.

- Faqat httpx kerak (aiogram yo'q) -> Termux va Python 3.14 da muammosiz.
- Hamma narsa tugmalar orqali boshqariladi (/start yoki "Menyu" tugmasi).
- Bot admin bo'lgan kanal/guruhlardagi yangi postlarni saqlab boradi,
  AI shu postlarga qarab (til, uslub, mavzu) yangi post yozadi.
- Rejalar, kanallar, sozlamalar SQLite da saqlanadi.
"""
import asyncio
import logging
import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone

import httpx

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)  # loglarda bot tokeni chiqmasin
log = logging.getLogger("bot")


# ---------- sozlamalar (.env) ----------
def load_env(path: str = ".env") -> None:
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_env()
BOT_TOKEN = os.environ["BOT_TOKEN"]
OPENAI_KEY = os.environ.get("OPENAI_API_KEY", "")
GROQ_KEY = os.environ.get("GROQ_API_KEY", "")
# (url, kalit, standart model, nomi)
PROVIDERS = {
    "groq": ("https://api.groq.com/openai/v1/chat/completions", GROQ_KEY, "openai/gpt-oss-120b", "Groq"),
    "openai": ("https://api.openai.com/v1/chat/completions", OPENAI_KEY, "gpt-4o-mini", "OpenAI"),
}
PROVIDER = os.environ.get("AI_PROVIDER", "groq" if GROQ_KEY else "openai").lower()
if PROVIDER not in PROVIDERS:
    raise SystemExit("AI_PROVIDER faqat groq yoki openai bo'lishi mumkin")
ADMINS = {int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip()}
DB_PATH = os.environ.get("DB_PATH", "bot.db")
API = f"https://api.telegram.org/bot{BOT_TOKEN}"
http: httpx.AsyncClient
BOT_ID = 0

# ---------- baza ----------
db = sqlite3.connect(DB_PATH, check_same_thread=False)
db.row_factory = sqlite3.Row
db.executescript(
    """
CREATE TABLE IF NOT EXISTS chats(
    chat_id INTEGER PRIMARY KEY, title TEXT, type TEXT
);
CREATE TABLE IF NOT EXISTS posts(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER, text TEXT, date INTEGER
);
CREATE INDEX IF NOT EXISTS posts_chat ON posts(chat_id, id);
CREATE TABLE IF NOT EXISTS jobs(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER, hhmm TEXT, days TEXT DEFAULT '',
    mode TEXT, content TEXT,
    active INTEGER DEFAULT 1, last_run TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
"""
)
db.commit()


def get_set(key: str, default: str) -> str:
    r = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return r["value"] if r else default


def set_set(key: str, value: str) -> None:
    db.execute(
        "INSERT INTO settings(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    db.commit()


def cfg_model() -> str:
    return get_set("model", os.environ.get("AI_MODEL") or PROVIDERS[PROVIDER][2])


def cfg_ctx() -> int:
    return int(get_set("ctx", os.environ.get("CONTEXT_POSTS", "15")))


def cfg_tz() -> int:
    return int(get_set("tz", os.environ.get("TZ_OFFSET_HOURS", "5")))


def now_local() -> datetime:
    return datetime.now(timezone(timedelta(hours=cfg_tz())))


def upsert_chat(chat: dict) -> None:
    db.execute(
        "INSERT INTO chats(chat_id,title,type) VALUES(?,?,?) "
        "ON CONFLICT(chat_id) DO UPDATE SET title=excluded.title, type=excluded.type",
        (chat["id"], chat.get("title") or chat.get("username") or str(chat["id"]), chat["type"]),
    )
    db.commit()


def chat_title(chat_id: int) -> str:
    r = db.execute("SELECT title FROM chats WHERE chat_id=?", (chat_id,)).fetchone()
    return r["title"] if r else str(chat_id)


def save_post(chat_id: int, text: str, date: int) -> None:
    if not text or text.startswith("/"):
        return
    last = db.execute(
        "SELECT text FROM posts WHERE chat_id=? ORDER BY id DESC LIMIT 1", (chat_id,)
    ).fetchone()
    if last and last["text"] == text:
        return
    db.execute("INSERT INTO posts(chat_id,text,date) VALUES(?,?,?)", (chat_id, text, date))
    db.execute(
        "DELETE FROM posts WHERE chat_id=? AND id NOT IN "
        "(SELECT id FROM posts WHERE chat_id=? ORDER BY id DESC LIMIT 200)",
        (chat_id, chat_id),
    )
    db.commit()


def recent_posts(chat_id: int, n: int) -> list[str]:
    rows = db.execute(
        "SELECT text FROM posts WHERE chat_id=? ORDER BY id DESC LIMIT ?", (chat_id, n)
    ).fetchall()
    return [r["text"] for r in reversed(rows)]


def count_posts(chat_id: int) -> int:
    return db.execute("SELECT COUNT(*) FROM posts WHERE chat_id=?", (chat_id,)).fetchone()[0]


# ---------- Telegram API ----------
async def tg(method: str, **params):
    r = await http.post(f"{API}/{method}", json=params, timeout=70)
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(data.get("description") or f"{method} xato")
    return data["result"]


async def say(chat_id: int, text: str, markup: dict | None = None) -> None:
    chunks = [text[i : i + 4000] for i in range(0, len(text), 4000)] or [""]
    for i, c in enumerate(chunks):
        p = {"chat_id": chat_id, "text": c}
        if markup and i == len(chunks) - 1:
            p["reply_markup"] = markup
        await tg("sendMessage", **p)


async def show(uid: int, mid: int | None, text: str, markup: dict | None = None) -> None:
    """Xabarni tahrirlaydi (mid bo'lsa), bo'lmasa yangi yuboradi."""
    p = {"text": text[:4000]}
    if markup:
        p["reply_markup"] = markup
    if mid:
        try:
            await tg("editMessageText", chat_id=uid, message_id=mid, **p)
            return
        except RuntimeError as e:
            if "not modified" in str(e):
                return
    await tg("sendMessage", chat_id=uid, **p)


async def notify_admins(text: str, markup: dict | None = None) -> None:
    for a in ADMINS:
        try:
            await say(a, text, markup)
        except Exception as e:  # noqa: BLE001
            log.warning("admin %s ga yuborib bo'lmadi (u botda /start bosmagan bo'lishi mumkin): %s", a, e)


# ---------- tugmalar ----------
def btn(text: str, data: str) -> dict:
    return {"text": text, "callback_data": data}


def kb(rows: list) -> dict:
    return {"inline_keyboard": rows}


MAIN_KB = kb(
    [
        [btn("📢 Kanallarim", "m:chats"), btn("➕ Kanal qo'shish", "m:addch")],
        [btn("➕ Yangi reja", "m:new"), btn("📋 Rejalarim", "m:jobs")],
        [btn("🤖 AI post", "m:ai"), btn("✉️ Hozir yuborish", "m:send")],
        [btn("📥 Namuna qo'shish", "m:sample"), btn("⚙️ Sozlamalar", "m:set")],
    ]
)
CANCEL_KB = kb([[btn("❌ Bekor qilish", "cancel")]])
BACK_KB = kb([[btn("🏠 Menyu", "menu")]])
REPLY_KB = {"keyboard": [[{"text": "🏠 Menyu"}]], "resize_keyboard": True}
DAYS = ["Du", "Se", "Ch", "Pa", "Ju", "Sh", "Ya"]

ADD_HELP = (
    "➕ Kanal/guruh qo'shish\n\n"
    "1) Avval botni kanal/guruhga ADMIN qilib qo'shing "
    "(kanalda \"Post messages\" huquqi bilan). Shunda u o'zi ro'yxatga tushadi.\n\n"
    "2) Yoki shu yerga kanalning @username'ini, ID raqamini yoki kanaldan bitta postni "
    "forward qilib yuboring — men tekshirib qo'shaman."
)

state: dict[int, dict] = {}  # foydalanuvchi bosqichlari
pending: dict[int, dict] = {}  # yuborishni kutayotgan matnlar


# ---------- ChatGPT ----------
async def generate_post(chat_id: int, instruction: str) -> str:
    url, key, _, pname = PROVIDERS[PROVIDER]
    if not key:
        raise RuntimeError(f"{pname} kaliti berilmagan (.env)")
    posts = recent_posts(chat_id, cfg_ctx())
    history = "\n\n---\n\n".join(posts) if posts else "(hali postlar saqlanmagan)"
    system = (
        "Sen Telegram kanal/guruh uchun kontent yozuvchisan. Quyida shu kanaldagi "
        "oxirgi postlar berilgan. Ularning tili, uslubi, ohangi, uzunligi va "
        "formatiga (emoji, hashtag) mos holda YANGI post yoz. Oldingi postlarni "
        "takrorlama. Faqat post matnini qaytar, izoh yozma."
    )
    user = (
        f"Kanaldagi oxirgi postlar:\n\n{history}\n\n"
        f"Topshiriq: {instruction or 'Kanal mavzusiga mos yangi post yoz.'}"
    )
    r = await http.post(
        url,
        headers={"Authorization": f"Bearer {key}"},
        json={
            "model": cfg_model(),
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.8,
        },
        timeout=90,
    )
    data = r.json()
    if r.status_code != 200:
        raise RuntimeError(f"{pname}: {data.get('error', {}).get('message', r.text)}")
    return data["choices"][0]["message"]["content"].strip()


async def post_to_chat(chat_id: int, text: str) -> None:
    await say(chat_id, text)
    save_post(chat_id, text, int(datetime.now().timestamp()))


# ---------- rejalarni bajarish ----------
async def run_job(job: sqlite3.Row) -> None:
    try:
        text = await generate_post(job["chat_id"], job["content"]) if job["mode"] == "ai" else job["content"]
        await post_to_chat(job["chat_id"], text)
        log.info("job %s yuborildi -> %s", job["id"], job["chat_id"])
    except Exception as e:  # noqa: BLE001
        log.error("job %s xato: %s", job["id"], e)
        await notify_admins(f"⚠️ Reja #{job['id']} ({chat_title(job['chat_id'])}) xato berdi:\n{e}")
        raise


async def run_job_safe(job: sqlite3.Row) -> None:
    try:
        await run_job(job)
    except Exception:  # noqa: BLE001
        pass


async def scheduler() -> None:
    while True:
        try:
            now = now_local()
            today = now.strftime("%Y-%m-%d")
            for job in db.execute("SELECT * FROM jobs WHERE active=1").fetchall():
                if job["last_run"] == today:
                    continue
                if job["days"] and str(now.isoweekday()) not in job["days"].split(","):
                    continue
                h, m = map(int, job["hhmm"].split(":"))
                sched = now.replace(hour=h, minute=m, second=0, microsecond=0)
                if sched <= now < sched + timedelta(minutes=10):
                    db.execute("UPDATE jobs SET last_run=? WHERE id=?", (today, job["id"]))
                    db.commit()
                    asyncio.create_task(run_job_safe(job))
        except Exception as e:  # noqa: BLE001
            log.error("scheduler: %s", e)
        await asyncio.sleep(20)


# ---------- ko'rinishlar (ekranlar) ----------
def days_text(days: str) -> str:
    if not days:
        return "har kuni"
    return ", ".join(DAYS[int(d) - 1] for d in sorted(days.split(",")))


def job_line(j: sqlite3.Row) -> str:
    st = "▶️" if j["active"] else "⏸"
    return f"{st} {j['hhmm']} · {chat_title(j['chat_id'])[:16]} · {'AI' if j['mode'] == 'ai' else 'matn'}"


async def show_main(uid: int, mid: int | None = None) -> None:
    await show(uid, mid, "🏠 Asosiy menyu", MAIN_KB)


async def show_chats(uid: int, mid: int | None) -> None:
    rows = db.execute("SELECT * FROM chats ORDER BY title").fetchall()
    buttons = [[btn(f"{'📢' if c['type'] == 'channel' else '👥'} {c['title'][:32]}", f"ch:{c['chat_id']}")] for c in rows]
    buttons.append([btn("➕ Kanal qo'shish", "m:addch")])
    buttons.append([btn("🏠 Menyu", "menu")])
    text = "📢 Kanal va guruhlaringiz:" if rows else "Hali kanal yo'q. ➕ Kanal qo'shish tugmasini bosing."
    await show(uid, mid, text, kb(buttons))


async def show_channel(uid: int, mid: int | None, cid: int) -> None:
    if not db.execute("SELECT 1 FROM chats WHERE chat_id=?", (cid,)).fetchone():
        await show_chats(uid, mid)
        return
    n_jobs = db.execute("SELECT COUNT(*) FROM jobs WHERE chat_id=?", (cid,)).fetchone()[0]
    text = (
        f"📢 {chat_title(cid)}\nID: {cid}\n"
        f"Saqlangan namuna postlar: {count_posts(cid)}\nRejalar: {n_jobs}"
    )
    await show(
        uid,
        mid,
        text,
        kb(
            [
                [btn("➕ Reja qo'shish", f"nj:{cid}"), btn("📋 Rejalari", f"cj:{cid}")],
                [btn("🤖 AI post", f"ai:{cid}"), btn("✉️ Hozir yuborish", f"sd:{cid}")],
                [btn("🗑 Kanalni o'chirish", f"d:{cid}")],
                [btn("⬅️ Kanallar", "m:chats"), btn("🏠 Menyu", "menu")],
            ]
        ),
    )


async def pick_chat(uid: int, mid: int | None, prefix: str, title: str) -> None:
    rows = db.execute("SELECT * FROM chats ORDER BY title").fetchall()
    if not rows:
        await show(uid, mid, "Avval kanal qo'shing.", kb([[btn("➕ Kanal qo'shish", "m:addch")], [btn("🏠 Menyu", "menu")]]))
        return
    buttons = [[btn(f"{'📢' if c['type'] == 'channel' else '👥'} {c['title'][:32]}", f"{prefix}:{c['chat_id']}")] for c in rows]
    buttons.append([btn("🏠 Menyu", "menu")])
    await show(uid, mid, title, kb(buttons))


async def show_jobs(uid: int, mid: int | None, cid: int | None = None) -> None:
    if cid is None:
        rows = db.execute("SELECT * FROM jobs ORDER BY hhmm").fetchall()
    else:
        rows = db.execute("SELECT * FROM jobs WHERE chat_id=? ORDER BY hhmm", (cid,)).fetchall()
    buttons = [[btn(job_line(j), f"jv:{j['id']}")] for j in rows]
    buttons.append([btn("➕ Yangi reja", f"nj:{cid}" if cid else "m:new")])
    buttons.append([btn("⬅️ Orqaga", f"ch:{cid}" if cid else "menu"), btn("🏠 Menyu", "menu")])
    await show(uid, mid, "📋 Rejalar:" if rows else "Hali reja yo'q.", kb(buttons))


async def show_job(uid: int, mid: int | None, jid: int) -> None:
    j = db.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
    if not j:
        await show_jobs(uid, mid)
        return
    kind = "🤖 AI yozadi" if j["mode"] == "ai" else "📝 Tayyor matn"
    body = j["content"][:300] or "(topshiriqsiz — kanal mavzusiga qarab)"
    text = (
        f"Reja #{j['id']} — {'faol' if j['active'] else 'to‘xtatilgan'}\n"
        f"Kanal: {chat_title(j['chat_id'])}\nVaqt: {j['hhmm']} ({days_text(j['days'])})\n"
        f"Turi: {kind}\n\n{body}"
    )
    await show(
        uid,
        mid,
        text,
        kb(
            [
                [btn("⏸ To'xtatish" if j["active"] else "▶️ Davom ettirish", f"jt:{jid}"), btn("🚀 Hozir yuborish", f"jr:{jid}")],
                [btn("🗑 O'chirish", f"jx:{jid}")],
                [btn("⬅️ Rejalar", "m:jobs"), btn("🏠 Menyu", "menu")],
            ]
        ),
    )


def days_kb(selected: set[int]) -> dict:
    row = [btn(("✅ " if i + 1 in selected else "") + DAYS[i], f"jd:{i + 1}") for i in range(7)]
    return kb(
        [
            row[:4],
            row[4:],
            [btn("📅 Har kuni", "jd:all"), btn("➡️ Tayyor", "jd:ok")],
            [btn("❌ Bekor qilish", "cancel")],
        ]
    )


async def show_settings(uid: int, mid: int | None) -> None:
    text = (
        "⚙️ Sozlamalar\n\n"
        f"🧠 AI: {PROVIDERS[PROVIDER][3]} · {cfg_model()}\n"
        f"📚 AI nechta oxirgi postga qaraydi: {cfg_ctx()}\n"
        f"🕒 Vaqt zonasi: UTC+{cfg_tz()}  (hozir {now_local().strftime('%H:%M')})"
    )
    await show(
        uid,
        mid,
        text,
        kb(
            [
                [btn("📃 Mavjud modellar (tanlash)", "set:list")],
                [btn("🧠 Model nomini qo'lda yozish", "set:model")],
                [btn("📚 Postlar soni", "set:ctx")],
                [btn("🕒 Vaqt zonasi", "set:tz")],
                [btn("🏠 Menyu", "menu")],
            ]
        ),
    )


def confirm_job_text(st: dict) -> str:
    kind = "🤖 AI yozadi" if st["mode"] == "ai" else "📝 Tayyor matn"
    body = st.get("content") or "(topshiriqsiz — kanal mavzusiga qarab)"
    return (
        "Reja:\n"
        f"Kanal: {chat_title(st['chat_id'])}\nVaqt: {st['hhmm']} ({days_text(','.join(map(str, sorted(st['days']))))})\n"
        f"Turi: {kind}\n\n{body[:500]}\n\nSaqlaymizmi?"
    )


# ---------- kanal qo'shish / o'chirish ----------
def parse_chat_ref(text: str):
    t = text.strip()
    if re.fullmatch(r"-?\d+", t):
        return int(t)
    m = re.search(r"t\.me/([A-Za-z0-9_]{5,})", t)
    if m:
        return "@" + m.group(1)
    if re.fullmatch(r"@[A-Za-z0-9_]{5,}", t):
        return t
    if re.fullmatch(r"[A-Za-z0-9_]{5,}", t):
        return "@" + t
    return None


async def verify_and_add(ref) -> dict:
    chat = await tg("getChat", chat_id=ref)
    if chat["type"] not in ("channel", "group", "supergroup"):
        raise RuntimeError("Bu kanal yoki guruh emas")
    if chat.get("is_direct_messages"):
        raise RuntimeError("Bu kanalning \"to'g'ridan-to'g'ri xabarlar\" chati. Kanalning o'zini (@username) qo'shing.")
    m = await tg("getChatMember", chat_id=chat["id"], user_id=BOT_ID)
    if m["status"] not in ("administrator", "creator"):
        raise RuntimeError("Bot bu yerda admin emas. Avval botni admin qiling.")
    if chat["type"] == "channel" and not m.get("can_post_messages", False):
        raise RuntimeError("Botda \"Post messages\" (xabar yuborish) huquqi yo'q.")
    upsert_chat(chat)
    return chat


async def delete_channel(cid: int, leave: bool) -> None:
    db.execute("DELETE FROM jobs WHERE chat_id=?", (cid,))
    db.execute("DELETE FROM posts WHERE chat_id=?", (cid,))
    db.execute("DELETE FROM chats WHERE chat_id=?", (cid,))
    db.commit()
    if leave:
        try:
            await tg("leaveChat", chat_id=cid)
        except Exception as e:  # noqa: BLE001
            log.warning("leaveChat: %s", e)


# ---------- AI / yuborish oqimlari ----------
async def do_ai(uid: int, mid: int | None, cid: int, instr: str) -> None:
    if mid:
        await show(uid, mid, "⏳ AI yozmoqda...")
    try:
        text = await generate_post(cid, instr)
    except Exception as e:  # noqa: BLE001
        await show(uid, mid, f"⚠️ Xato: {e}", BACK_KB)
        return
    pending[uid] = {"chat_id": cid, "instr": instr, "text": text}
    await show(
        uid,
        mid,
        f"✍️ {chat_title(cid)} uchun post:\n\n{text}",
        kb(
            [
                [btn("✅ Yuborish", "aok"), btn("🔄 Qayta yozish", "are")],
                [btn("❌ Bekor qilish", "cancel")],
            ]
        ),
    )


# ---------- callback (tugma bosilganda) ----------
async def on_callback(cb: dict) -> None:
    uid = cb["from"]["id"]
    try:
        await tg("answerCallbackQuery", callback_query_id=cb["id"])
    except Exception:  # noqa: BLE001
        pass
    if uid not in ADMINS or "message" not in cb:
        return
    mid = cb["message"]["message_id"]
    data = cb.get("data", "")

    if data in ("menu", "cancel"):
        state.pop(uid, None)
        pending.pop(uid, None)
        await show_main(uid, mid)

    # --- kanallar ---
    elif data == "m:chats":
        await show_chats(uid, mid)
    elif data.startswith("ch:"):
        await show_channel(uid, mid, int(data[3:]))
    elif data == "m:addch":
        state[uid] = {"step": "addch"}
        await show(uid, mid, ADD_HELP, CANCEL_KB)
    elif data.startswith("d:"):
        cid = int(data[2:])
        await show(
            uid,
            mid,
            f"“{chat_title(cid)}” ni o'chirasizmi?\nShu kanalning rejalari va saqlangan postlari ham o'chadi.",
            kb(
                [
                    [btn("🗑 Faqat ro'yxatdan", f"dx:{cid}:0")],
                    [btn("🗑 Ro'yxatdan + botni kanaldan chiqarish", f"dx:{cid}:1")],
                    [btn("⬅️ Bekor qilish", f"ch:{cid}")],
                ]
            ),
        )
    elif data.startswith("dx:"):
        _, cid, leave = data.split(":")
        title = chat_title(int(cid))
        await delete_channel(int(cid), leave == "1")
        await show(uid, mid, f"✅ “{title}” o'chirildi.", kb([[btn("📢 Kanallarim", "m:chats"), btn("🏠 Menyu", "menu")]]))

    # --- rejalar ---
    elif data == "m:jobs":
        await show_jobs(uid, mid)
    elif data.startswith("cj:"):
        await show_jobs(uid, mid, int(data[3:]))
    elif data.startswith("jv:"):
        await show_job(uid, mid, int(data[3:]))
    elif data.startswith("jt:"):
        jid = int(data[3:])
        db.execute("UPDATE jobs SET active=1-active WHERE id=?", (jid,))
        db.commit()
        await show_job(uid, mid, jid)
    elif data.startswith("jr:"):
        j = db.execute("SELECT * FROM jobs WHERE id=?", (int(data[3:]),)).fetchone()
        if j:
            await show(uid, mid, "⏳ Yuborilmoqda...")
            try:
                await run_job(j)
                await show(uid, mid, "✅ Yuborildi.", kb([[btn("⬅️ Reja", f"jv:{j['id']}"), btn("🏠 Menyu", "menu")]]))
            except Exception as e:  # noqa: BLE001
                await show(uid, mid, f"⚠️ Xato: {e}", BACK_KB)
    elif data.startswith("jx:"):
        db.execute("DELETE FROM jobs WHERE id=?", (int(data[3:]),))
        db.commit()
        await show_jobs(uid, mid)

    # --- yangi reja ustasi ---
    elif data == "m:new":
        await pick_chat(uid, mid, "nj", "➕ Qaysi kanalga reja qo'shamiz?")
    elif data.startswith("nj:"):
        cid = int(data[3:])
        state[uid] = {"step": "time", "chat_id": cid, "days": set()}
        await show(uid, mid, f"📢 {chat_title(cid)}\n\n🕒 Vaqtni yozing (soat:daqiqa), masalan: 09:00 yoki 18:30", CANCEL_KB)
    elif data.startswith("jd:") and state.get(uid, {}).get("step") == "days":
        st = state[uid]
        v = data[3:]
        if v == "all":
            st["days"] = set()
            st["step"] = "mode"
            await show(uid, mid, "Qanday post yuborilsin?", kb([[btn("📝 Tayyor matn", "jm:fixed")], [btn("🤖 AI yozsin", "jm:ai")], [btn("❌ Bekor qilish", "cancel")]]))
        elif v == "ok":
            st["step"] = "mode"
            await show(uid, mid, "Qanday post yuborilsin?", kb([[btn("📝 Tayyor matn", "jm:fixed")], [btn("🤖 AI yozsin", "jm:ai")], [btn("❌ Bekor qilish", "cancel")]]))
        else:
            n = int(v)
            st["days"] ^= {n}
            await show(uid, mid, "📅 Hafta kunlarini tanlang (hech narsa tanlamasangiz — har kuni):", days_kb(st["days"]))
    elif data.startswith("jm:") and state.get(uid, {}).get("step") in ("mode", "content"):
        st = state[uid]
        v = data[3:]
        if v == "skip":
            st["content"] = ""
            st["step"] = "confirm"
            await show(uid, mid, confirm_job_text(st), kb([[btn("✅ Saqlash", "js:ok"), btn("❌ Bekor", "cancel")]]))
        else:
            st["mode"] = v
            st["step"] = "content"
            if v == "fixed":
                await show(uid, mid, "📝 Yuboriladigan matnni yozing:", CANCEL_KB)
            else:
                await show(
                    uid,
                    mid,
                    "🤖 AI uchun topshiriq yozing (masalan: \"kunlik motivatsiya\").\nYoki o'tkazib yuboring — AI kanal mavzusiga qarab o'zi yozadi.",
                    kb([[btn("⏭ Topshiriqsiz", "jm:skip")], [btn("❌ Bekor qilish", "cancel")]]),
                )
    elif data == "js:ok" and state.get(uid, {}).get("step") == "confirm":
        st = state.pop(uid)
        db.execute(
            "INSERT INTO jobs(chat_id,hhmm,days,mode,content) VALUES(?,?,?,?,?)",
            (st["chat_id"], st["hhmm"], ",".join(map(str, sorted(st["days"]))), st["mode"], st.get("content", "")),
        )
        db.commit()
        await show(uid, mid, "✅ Reja saqlandi.", kb([[btn("📋 Rejalarim", "m:jobs"), btn("🏠 Menyu", "menu")]]))

    # --- AI post ---
    elif data == "m:ai":
        await pick_chat(uid, mid, "ai", "🤖 Qaysi kanal uchun post yozamiz?")
    elif data.startswith("ai:"):
        cid = int(data[3:])
        state[uid] = {"step": "ai_topic", "chat_id": cid}
        await show(
            uid,
            mid,
            f"📢 {chat_title(cid)}\n\nMavzu yoki topshiriqni yozing.\nYoki AI kanalga qarab o'zi tanlasin:",
            kb([[btn("⏭ Topshiriqsiz", f"ai0:{cid}")], [btn("❌ Bekor qilish", "cancel")]]),
        )
    elif data.startswith("ai0:"):
        state.pop(uid, None)
        await do_ai(uid, mid, int(data[4:]), "")
    elif data == "are" and uid in pending:
        p = pending[uid]
        await do_ai(uid, mid, p["chat_id"], p.get("instr", ""))
    elif data == "aok" and uid in pending:
        p = pending.pop(uid)
        try:
            await post_to_chat(p["chat_id"], p["text"])
            await show(uid, mid, f"✅ {chat_title(p['chat_id'])} ga yuborildi.", BACK_KB)
        except Exception as e:  # noqa: BLE001
            await show(uid, mid, f"⚠️ Yuborib bo'lmadi: {e}", BACK_KB)

    # --- hozir yuborish ---
    elif data == "m:send":
        await pick_chat(uid, mid, "sd", "✉️ Qaysi kanalga yuboramiz?")
    elif data.startswith("sd:"):
        cid = int(data[3:])
        state[uid] = {"step": "send_text", "chat_id": cid}
        await show(uid, mid, f"📢 {chat_title(cid)}\n\nYuboriladigan matnni yozing:", CANCEL_KB)

    # --- namuna ---
    elif data == "m:sample":
        lines = [f"• {c['title']}: {count_posts(c['chat_id'])} ta" for c in db.execute("SELECT * FROM chats").fetchall()]
        await show(
            uid,
            mid,
            "📥 Namuna qo'shish\n\nKanaldagi eski postlarni shu botga forward qiling — "
            "AI ularning uslubini o'rganadi. Yangi postlar o'zi saqlanadi.\n\n"
            "Saqlangan postlar:\n" + ("\n".join(lines) if lines else "(kanal yo'q)"),
            BACK_KB,
        )

    # --- sozlamalar ---
    elif data == "m:set":
        await show_settings(uid, mid)
    elif data == "set:model":
        state[uid] = {"step": "set_model"}
        await show(uid, mid, f"Hozirgi model: {cfg_model()}\n\nYangi model nomini yozing (masalan: openai/gpt-oss-120b):", CANCEL_KB)
    elif data == "set:list":
        url, key, _, _ = PROVIDERS[PROVIDER]
        try:
            r = await http.get(url.replace("/chat/completions", "/models"), headers={"Authorization": f"Bearer {key}"}, timeout=30)
            ids = sorted(m["id"] for m in r.json()["data"])
        except Exception as e:  # noqa: BLE001
            await show(uid, mid, f"⚠️ Modellar ro'yxatini olib bo'lmadi: {e}", kb([[btn("⬅️ Orqaga", "m:set")]]))
            return
        skip = ("whisper", "tts", "guard", "embed", "orpheus", "playai", "distil")
        ids = [i for i in ids if len(i) <= 55 and not any(s in i for s in skip)][:25]
        rows = [[btn(("✅ " if i == cfg_model() else "") + i, f"sm:{i}")] for i in ids]
        rows.append([btn("⬅️ Orqaga", "m:set")])
        await show(uid, mid, "Modelni tanlang (hozirgisi ✅ bilan):", kb(rows))
    elif data.startswith("sm:"):
        set_set("model", data[3:])
        await show_settings(uid, mid)
    elif data == "set:ctx":
        await show(uid, mid, "AI nechta oxirgi postga qarasin?", kb([[btn(str(n), f"sc:{n}") for n in (5, 10, 15, 20, 30)], [btn("⬅️ Orqaga", "m:set")]]))
    elif data.startswith("sc:"):
        set_set("ctx", data[3:])
        await show_settings(uid, mid)
    elif data == "set:tz":
        await show(uid, mid, "Vaqt zonasini tanlang (Toshkent/Farg'ona = UTC+5):", kb([[btn(f"UTC+{n}", f"stz:{n}") for n in (3, 4, 5, 6, 7)], [btn("⬅️ Orqaga", "m:set")]]))
    elif data.startswith("stz:"):
        set_set("tz", data[4:])
        await show_settings(uid, mid)


# ---------- matn xabarlar ----------
async def on_private_message(msg: dict) -> None:
    uid = msg["from"]["id"]
    if uid not in ADMINS:
        return
    text = (msg.get("text") or msg.get("caption") or "").strip()
    origin = msg.get("forward_origin")
    st = state.get(uid)

    if text in ("/start", "/menu", "🏠 Menyu"):
        state.pop(uid, None)
        pending.pop(uid, None)
        if text == "/start":
            await say(uid, "Salom! Bot tayyor. Pastdagi \"🏠 Menyu\" tugmasi har doim menyuni ochadi.", REPLY_KB)
        await show_main(uid)
        return
    if text == "/cancel":
        state.pop(uid, None)
        pending.pop(uid, None)
        await show_main(uid)
        return

    # forward qilingan kanal posti
    if origin and origin.get("type") == "channel":
        ch = origin["chat"]
        if st and st["step"] == "addch":
            await try_add(uid, ch["id"])
            return
        if db.execute("SELECT 1 FROM chats WHERE chat_id=?", (ch["id"],)).fetchone():
            save_post(ch["id"], text, msg["date"])
            await say(uid, f"📥 Namuna saqlandi ({chat_title(ch['id'])}).")
        else:
            await say(uid, "Bu kanal ro'yxatda yo'q. Avval ➕ Kanal qo'shish orqali qo'shing.", kb([[btn("➕ Kanal qo'shish", "m:addch")]]))
        return

    if not st:
        await show_main(uid)
        return
    step = st["step"]

    if step == "addch":
        ref = parse_chat_ref(text)
        if ref is None:
            await say(uid, "Tushunmadim. @username, ID raqam yuboring yoki kanaldan post forward qiling.", CANCEL_KB)
            return
        await try_add(uid, ref)

    elif step == "time":
        m = re.fullmatch(r"(\d{1,2})[:.](\d{2})", text)
        if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
            await say(uid, "Vaqt noto'g'ri. Masalan: 09:00 yoki 18:30", CANCEL_KB)
            return
        st["hhmm"] = f"{int(m.group(1)):02d}:{m.group(2)}"
        st["step"] = "days"
        await say(uid, "📅 Hafta kunlarini tanlang (hech narsa tanlamasangiz — har kuni):", days_kb(st["days"]))

    elif step == "content":
        if not text:
            await say(uid, "Matn bo'sh bo'lmasin.", CANCEL_KB)
            return
        st["content"] = text
        st["step"] = "confirm"
        await say(uid, confirm_job_text(st), kb([[btn("✅ Saqlash", "js:ok"), btn("❌ Bekor", "cancel")]]))

    elif step == "ai_topic":
        state.pop(uid, None)
        await do_ai(uid, None, st["chat_id"], text)

    elif step == "send_text":
        if not text:
            await say(uid, "Matn bo'sh bo'lmasin.", CANCEL_KB)
            return
        state.pop(uid, None)
        pending[uid] = {"chat_id": st["chat_id"], "text": text}
        await say(
            uid,
            f"✉️ {chat_title(st['chat_id'])} ga quyidagi matn yuboriladi:\n\n{text}",
            kb([[btn("✅ Yuborish", "aok"), btn("❌ Bekor qilish", "cancel")]]),
        )

    elif step == "set_model":
        if not re.fullmatch(r"[\w.\-:/]{2,60}", text):
            await say(uid, "Model nomi noto'g'ri.", CANCEL_KB)
            return
        state.pop(uid, None)
        set_set("model", text)
        await show_settings(uid, None)


async def try_add(uid: int, ref) -> None:
    try:
        chat = await verify_and_add(ref)
    except Exception as e:  # noqa: BLE001
        await say(uid, f"⚠️ Qo'shib bo'lmadi: {e}", kb([[btn("🔁 Qayta urinish", "m:addch")], [btn("🏠 Menyu", "menu")]]))
        return
    state.pop(uid, None)
    await say(uid, f"✅ Qo'shildi: {chat.get('title')}", kb([[btn("📢 Kanalni ochish", f"ch:{chat['id']}")], [btn("🏠 Menyu", "menu")]]))


# ---------- yangilanishlar ----------
async def handle_update(u: dict) -> None:
    if "callback_query" in u:
        await on_callback(u["callback_query"])

    elif "channel_post" in u or "edited_channel_post" in u:
        p = u.get("channel_post") or u["edited_channel_post"]
        upsert_chat(p["chat"])
        if "channel_post" in u:
            save_post(p["chat"]["id"], p.get("text") or p.get("caption") or "", p["date"])

    elif "my_chat_member" in u:
        mc = u["my_chat_member"]
        chat = mc["chat"]
        status = mc["new_chat_member"]["status"]
        if chat["type"] in ("channel", "group", "supergroup") and not chat.get("is_direct_messages"):
            if status in ("administrator", "member"):
                upsert_chat(chat)
                await notify_admins(
                    f"➕ Qo'shildim: {chat.get('title')} (holat: {status})",
                    kb([[btn("📢 Kanalni ochish", f"ch:{chat['id']}")]]),
                )
            elif status in ("left", "kicked"):
                db.execute("DELETE FROM chats WHERE chat_id=?", (chat["id"],))
                db.execute("DELETE FROM jobs WHERE chat_id=?", (chat["id"],))
                db.commit()
                await notify_admins(f"➖ Chiqarildim: {chat.get('title')}. Uning rejalari o'chirildi.")

    elif "message" in u:
        msg = u["message"]
        chat = msg["chat"]
        if chat["type"] == "private":
            await on_private_message(msg)
        elif chat["type"] in ("group", "supergroup") and not chat.get("is_direct_messages"):
            upsert_chat(chat)
            save_post(chat["id"], msg.get("text") or msg.get("caption") or "", msg["date"])


async def poll() -> None:
    offset = 0
    allowed = ["message", "callback_query", "channel_post", "edited_channel_post", "my_chat_member"]
    while True:
        try:
            updates = await tg("getUpdates", offset=offset, timeout=30, allowed_updates=allowed)
            for u in updates:
                offset = u["update_id"] + 1
                try:
                    await handle_update(u)
                except Exception as e:  # noqa: BLE001
                    log.error("update xato: %s", e)
        except Exception as e:  # noqa: BLE001
            log.error("poll (internet uzilgan bo'lishi mumkin): %r", e)
            await asyncio.sleep(5)


async def main() -> None:
    global http, BOT_ID
    if not ADMINS:
        raise SystemExit("ADMIN_IDS ni .env ga yozing (o'z Telegram ID raqamingiz)")
    http = httpx.AsyncClient()
    me = await tg("getMe")
    BOT_ID = me["id"]
    await tg("deleteWebhook")
    try:
        await tg("setMyCommands", commands=[{"command": "menu", "description": "Asosiy menyu"}])
    except Exception:  # noqa: BLE001
        pass
    log.info("Bot ishga tushdi: @%s | vaqt: %s", me["username"], now_local())
    await asyncio.gather(poll(), scheduler())


if __name__ == "__main__":
    asyncio.run(main())
