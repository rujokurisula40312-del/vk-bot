"""
VK-бот — standalone версия (отдельный репозиторий).
Всё в одном файле, нет зависимостей от других модулей.

Переменные окружения (Railway → Variables):
  VK_TOKEN, VK_GROUP_ID, VK_ALLOWED_USERS
  CLAUDE_API_KEY
  GEMINI_API_KEY
  GOOGLE_CREDS  (JSON одной строкой)
  SPREADSHEET_ID_1, SPREADSHEET_ID_2, SPREADSHEET_ID_NUTRITION,
  SPREADSHEET_ID_GUIDES, SPREADSHEET_ID_SUBS, SPREADSHEET_ID_TOURISTS,
  SPREADSHEET_ID_TASKS, PASSWORDS_SHEET_ID
  CALENDAR_WORK, CALENDAR_FAMILY, CALENDAR_PERSONAL
  NOTION_TOKEN, NOTION_PARENT_PAGE_ID
  NUTRITION_OWNER_USER_ID
"""

import os, json, re, math, time, base64, asyncio, hashlib
import datetime, logging, random
import aiohttp
import anthropic
import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build as google_build
from vkbottle import Bot, Keyboard, Text, EMPTY_KEYBOARD
from vkbottle.bot import Message

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════════
# КОНФИГ ИЗ ПЕРЕМЕННЫХ ОКРУЖЕНИЯ
# ══════════════════════════════════════════════════════════════════

def _e(key, default=""):
    return os.environ.get(key, default)

def _ids(key):
    return [int(x.strip()) for x in _e(key).split(",") if x.strip().isdigit()]

VK_TOKEN          = _e("VK_TOKEN")
VK_GROUP_ID       = int(_e("VK_GROUP_ID", "0"))
VK_ALLOWED_USERS  = _ids("VK_ALLOWED_USERS")

CLAUDE_API_KEY    = _e("CLAUDE_API_KEY")
CLAUDE_MODEL      = "claude-haiku-4-5-20251001"
CLAUDE_VISION     = "claude-sonnet-4-6"

GEMINI_KEY        = _e("GEMINI_API_KEY")
GEMINI_KEY_2      = _e("GEMINI_API_KEY_2")
GEMINI_KEYS       = [k for k in [GEMINI_KEY, GEMINI_KEY_2] if k]
GEMINI_MODEL      = "gemini-2.5-flash"

_gcreds           = _e("GOOGLE_CREDS")
GOOGLE_CREDS      = json.loads(_gcreds) if _gcreds else None
GOOGLE_SCOPES     = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/calendar",
]

SP1               = _e("SPREADSHEET_ID_1")        # ДДС + Прогрев
SP2               = _e("SPREADSHEET_ID_2")        # КУДиР
SP_NUTR           = _e("SPREADSHEET_ID_NUTRITION")
SP_GUIDES         = _e("SPREADSHEET_ID_GUIDES")
SP_GUIDES_ALL     = _e("SPREADSHEET_ID_GUIDES_ALL")
SP_GUIDES_CO      = _e("SPREADSHEET_ID_GUIDES_COMPANIES")
SP_SUBS           = _e("SPREADSHEET_ID_SUBS")
SP_TOURISTS       = _e("SPREADSHEET_ID_TOURISTS")
SP_TASKS          = _e("SPREADSHEET_ID_TASKS")
SP_PASSES         = _e("PASSWORDS_SHEET_ID")

CAL_WORK          = _e("CALENDAR_WORK")
CAL_FAM           = _e("CALENDAR_FAMILY")
CAL_PER           = _e("CALENDAR_PERSONAL")
TIMEZONE          = "Europe/Moscow"

NOTION_TOKEN      = _e("NOTION_TOKEN")
NOTION_PARENT     = _e("NOTION_PARENT_PAGE_ID")

NUTR_OWNER        = int(_e("NUTRITION_OWNER_USER_ID", "0"))

UON_API_KEY       = _e("UON_API_KEY")
UON_ACCOUNT_ID    = _e("UON_ACCOUNT_ID")

# ══════════════════════════════════════════════════════════════════
# ИНИЦИАЛИЗАЦИЯ КЛИЕНТОВ
# ══════════════════════════════════════════════════════════════════

if not VK_TOKEN:
    raise SystemExit("VK_TOKEN не задан!")

log.info("=== VK BOT STARTUP ===")
log.info(f"VK_TOKEN set: {bool(VK_TOKEN)}, len={len(VK_TOKEN)}")
log.info(f"VK_GROUP_ID: {VK_GROUP_ID}")
log.info(f"VK_ALLOWED_USERS: {VK_ALLOWED_USERS}")
log.info(f"CLAUDE_API_KEY set: {bool(CLAUDE_API_KEY)}")
log.info(f"GOOGLE_CREDS set: {bool(GOOGLE_CREDS)}")

bot = Bot(token=VK_TOKEN)
log.info("vkbottle Bot created OK")

try:
    claude = anthropic.Anthropic(api_key=CLAUDE_API_KEY)
    log.info("anthropic client OK")
except Exception as e:
    log.error(f"anthropic init FAILED: {e}")
    claude = None

try:
    creds   = Credentials.from_service_account_info(GOOGLE_CREDS, scopes=GOOGLE_SCOPES) if GOOGLE_CREDS else None
    gc      = gspread.authorize(creds) if creds else None
    cal_svc = google_build("calendar", "v3", credentials=creds) if creds else None
    log.info(f"Google init OK: creds={bool(creds)}, gc={bool(gc)}, cal={bool(cal_svc)}")
except Exception as e:
    log.error(f"Google init FAILED: {e}")
    creds = gc = cal_svc = None

_gidx = 0
def _gemini_key():
    global _gidx
    k = GEMINI_KEYS[_gidx % len(GEMINI_KEYS)] if GEMINI_KEYS else ""
    _gidx += 1
    return k

# ══════════════════════════════════════════════════════════════════
# УТИЛИТЫ
# ══════════════════════════════════════════════════════════════════

def _now():
    tz = datetime.timezone(datetime.timedelta(hours=3))
    return datetime.datetime.now(tz)

def _today():
    return _now().strftime("%d.%m.%Y")

def _rand():
    return random.randint(1, 2**31)

def fmt(val):
    try:
        v = float(str(val).replace(",", ".").replace(" ", ""))
        return f"{v:,.2f}".replace(",", " ").replace(".", ",")
    except Exception:
        return str(val) if val else "0"

def safe_phone(p):
    if not p: return ""
    d = re.sub(r"\D", "", str(p))
    if len(d) == 11 and d[0] in "78":
        return f"+7 ({d[1:4]}) {d[4:7]}-{d[7:9]}-{d[9:11]}"
    return str(p)

def normalize(t):
    return re.sub(r"\s+", " ", t.lower().strip())

def fuzzy(query, text):
    q = normalize(query)
    t = normalize(text)
    if q in t: return True
    words = q.split()
    return sum(1 for w in words if w in t) >= max(1, len(words) * 0.6)

def strip_html(t):
    t = re.sub(r"<b>(.*?)</b>", r"\1", t, flags=re.DOTALL)
    t = re.sub(r"<i>(.*?)</i>", r"\1", t, flags=re.DOTALL)
    t = re.sub(r"<code>(.*?)</code>", r"`\1`", t, flags=re.DOTALL)
    return re.sub(r"<[^>]+>", "", t).strip()

def split_text(text, limit=4000):
    if len(text) <= limit:
        return [text]
    parts, cur = [], ""
    for line in text.split("\n"):
        if len(cur) + len(line) + 1 > limit:
            if cur: parts.append(cur.strip())
            cur = line
        else:
            cur += ("\n" if cur else "") + line
    if cur: parts.append(cur.strip())
    return parts or [text[:limit]]

def sheets_retry(fn, *args, retries=3, **kwargs):
    for i in range(retries):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            if i == retries - 1: raise
            time.sleep(2 ** i)

def parse_json(raw):
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    m = re.search(r"[\[{].*[\]}]", raw, re.DOTALL)
    if m:
        raw = m.group(0)
    return json.loads(raw)

def build_content(docs):
    content = []
    for d in docs:
        if d.get("type") == "image":
            content.append({"type": "image", "source": {
                "type": "base64",
                "media_type": d["media_type"],
                "data": d["data"],
            }})
        elif d.get("type") == "text":
            content.append({"type": "text", "text": d["text"]})
    return content

# ══════════════════════════════════════════════════════════════════
# STATE MACHINE
# ══════════════════════════════════════════════════════════════════

states:       dict[int, dict] = {}
batch:        dict[int, list] = {}
guide_buf:    dict[int, list] = {}
tourist_buf:  dict[int, list] = {}
warmup_st:    dict[int, dict] = {}
auth_time:    dict[int, float] = {}

def clear(uid):
    for d in (states, batch, guide_buf, tourist_buf, warmup_st):
        d.pop(uid, None)

def allowed(uid):
    return not VK_ALLOWED_USERS or uid in VK_ALLOWED_USERS

# ══════════════════════════════════════════════════════════════════
# VK: МЕДИА
# ══════════════════════════════════════════════════════════════════

async def get_atts(message: Message):
    result = []
    if not message.attachments:
        return result
    for att in message.attachments:
        try:
            t = att.type.value
            if t == "photo":
                sizes = sorted(att.photo.sizes, key=lambda s: s.width * s.height)
                url = sizes[-1].url
                async with aiohttp.ClientSession() as s:
                    async with s.get(url) as r:
                        data = await r.read()
                result.append({"type": "image", "media_type": "image/jpeg",
                               "data": base64.b64encode(data).decode()})
            elif t == "doc":
                doc = att.doc
                async with aiohttp.ClientSession() as s:
                    async with s.get(doc.url) as r:
                        data = await r.read()
                ext = (doc.ext or "").lower()
                if ext in ("jpg", "jpeg", "png", "webp"):
                    mt = f"image/{ext if ext != 'jpg' else 'jpeg'}"
                    result.append({"type": "image", "media_type": mt,
                                   "data": base64.b64encode(data).decode()})
                else:
                    result.append({"type": "text", "text": f"[Документ: {doc.title}.{ext}]"})
        except Exception as e:
            log.warning(f"att error: {e}")
    return result

async def upload_photo(peer_id, img_bytes):
    try:
        srv = await bot.api.photos.get_messages_upload_server(peer_id=peer_id)
        async with aiohttp.ClientSession() as s:
            data = aiohttp.FormData()
            data.add_field("photo", img_bytes, filename="photo.png", content_type="image/png")
            async with s.post(srv.upload_url, data=data) as r:
                resp = await r.json(content_type=None)
        saved = await bot.api.photos.save_messages_photo(
            photo=resp["photo"], server=resp["server"], hash=resp["hash"])
        p = saved[0]
        return f"photo{p.owner_id}_{p.id}"
    except Exception as e:
        log.warning(f"photo upload: {e}")
        return None

# ══════════════════════════════════════════════════════════════════
# VK: ОТПРАВКА
# ══════════════════════════════════════════════════════════════════

async def send(peer_id, text, keyboard=None, attachment=None):
    text = strip_html(str(text))
    parts = split_text(text)
    for i, part in enumerate(parts):
        kw = {"peer_id": peer_id, "message": part, "random_id": _rand()}
        if i == len(parts) - 1:
            if keyboard is not None: kw["keyboard"] = keyboard
            if attachment: kw["attachment"] = attachment
        await bot.api.messages.send(**kw)

# ══════════════════════════════════════════════════════════════════
# КЛАВИАТУРЫ
# ══════════════════════════════════════════════════════════════════

def kb(*rows, one_time=False):
    k = Keyboard(one_time=one_time)
    for i, row in enumerate(rows):
        if i > 0: k.row()
        for label in row:
            k.add(Text(label))
    return k.get_json()

def kb_main():
    return kb(["₽ Финучёт", "▶ Календарь"],
              ["📚 База знаний", "📦 Заказы"],
              ["📋 Задачи", "🔧 Полезное"],
              ["👤 Личное", "🔍 Поиск по базе"])

# Раздел «Личное» — как в Telegram-боте.
# Питание уже есть; Рефлексия и Мои поездки пока заглушки до портирования.
def kb_lichnoe():
    return kb(["🍎 Питание", "🪞 Рефлексия"],
              ["🌍 Мои поездки", "◀ Главная"])

def kb_fin():
    return kb(["Таблица 1 (ДДС)", "Таблица 2 (КУДиР)"],
              ["📊 Финотчёт", "🔄 Подписки"],
              ["🔥 Прогрев", "◀ Главная"])

def kb_know():
    return kb(["🗺 Гиды", "🌍 Мои поездки"],
              ["📁 Медиатека", "📘 Инструкции"],
              ["◀ Главная"])

def kb_orders():
    return kb(["Добавить туриста", "Найти туриста"],
              ["База туристов", "🔎 U-ON"],
              ["◀ Главная"])

# Подменю U-ON: поиск и оперативные дайджесты заявок.
def kb_uon():
    return kb(["🔎 Поиск заявки", "📋 Брони сегодня"],
              ["✈️ Вылеты", "🛬 Возвращения"],
              ["📊 Дедлайны ТО", "◀ Заказы"])

def kb_tasks():
    return kb(["Новая задача", "Все задачи"],
              ["Задачи по людям", "Выполнить задачу"],
              ["◀ Главная"])

def kb_useful():
    return kb(["🔐 Пароли", "💱 Курс валют"],
              ["◀ Главная"])

# Подменю «💱 Курс валют» — выбор источника по ТО.
def kb_currency():
    return kb(["📊 Tour-kassa (сегодня)", "📊 Tour-kassa (завтра)"],
              ["🛳 CruClub", "✈️ PAC Group"],
              ["🚢 Ла Вояж", "◀ Главная"])

def kb_cancel():
    return kb(["❌ Отмена"])

def kb_ok():
    return kb(["✅ Готово", "❌ Отмена"])

def kb_collect():
    return kb(["✅ Обработать", "❌ Отмена"])

def kb_tourist():
    return kb(["👤 Портрет клиента", "📝 Скрипт продаж"],
              ["◀ Главная"])

def kb_nutr():
    return kb(["📊 Сегодня", "📅 Неделя"],
              ["📈 Месяц", "◀ Главная"])

def kb_warmup():
    return kb(["➕ Новый прогрев", "📊 Добавить статистику"],
              ["📈 Отчёт", "◀ Финучёт"])

def kb_cal():
    return kb(["📅 План на сегодня", "📆 На неделю"],
              ["➕ Добавить событие", "◀ Главная"])

def kb_passes():
    return kb(["🔍 Найти пароль", "➕ Добавить пароль"],
              ["📋 Все пароли", "◀ Главная"])

# ══════════════════════════════════════════════════════════════════
# CLAUDE
# ══════════════════════════════════════════════════════════════════

async def ask_claude(system, content, model=None, max_tokens=1500):
    resp = await asyncio.to_thread(
        claude.messages.create,
        model=model or CLAUDE_MODEL,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": content}],
    )
    return resp.content[0].text.strip()

# ══════════════════════════════════════════════════════════════════
# ПРОМТЫ
# ══════════════════════════════════════════════════════════════════

SYS_FIN = """Ты — финансовый ассистент. Анализируй документ и верни ТОЛЬКО JSON:
{"date":"ДД.ММ.ГГГГ","amount":число,"comment":"описание","order_number":"номер или null",
"note":"чек/пп или null","note_type":"пп|ФД|null","tour_operator_raw":"ТО или null",
"doc_type":"чек|платежка|сбп|комиссия|crm|возврат|выписка|другое",
"is_incoming":true/false,"tour_month":"MM или null","tour_year":"ГГГГ или null",
"is_refund":false,"bank_name":"банк или null","crm_order_number":"CRM номер или null"}
Без текста вокруг."""

SYS_FIN_BATCH = """Ты — финансовый ассистент. Верни ТОЛЬКО JSON массив таких же объектов.
Один документ = одна операция. Без текста вокруг."""

SYS_GUIDE = """Извлеки данные гида. Верни JSON (или массив если несколько):
{"country":"СТРАНА","city":"город или null","guide_name":"имя","contacts":"контакты","description":"описание"}
Без текста вокруг."""

SYS_TOURIST = """Извлеки данные туриста. Верни JSON:
{"name":"ФИО","phone":"телефон","destination":"куда","dates":"когда","budget":"бюджет",
"group":"состав","wishes":"пожелания","source":"откуда","comments":"доп.инфо"}
Без текста вокруг."""

SYS_PORTRAIT = "Составь психологический портрет клиента. Без markdown и звёздочек. 5 блоков: тип личности, потребности, страхи, триггеры покупки, рекомендации."

SYS_SCRIPT = "Составь скрипт продаж (5 этапов). Без markdown. По методологиям Подреза, Гребенюка, Трейси."

SYS_SUBS = """Определи параметры подписки. Верни JSON:
{"name":"название","category":"категория","amount":число,"period_months":число,"monthly_cost":число}
Без текста вокруг."""

SYS_NUTR = """Определи КБЖУ блюда. Верни JSON:
{"meal_name":"название","kcal":число,"protein":число,"fat":число,"carbs":число}
Без текста вокруг."""

SYS_CAL = """Сегодня: {today}, Москва UTC+3. Разбери запрос о событии. Верни JSON:
{{"action":"create|list","title":"название","date":"YYYY-MM-DD","time":"HH:MM или null",
"calendar":"work|family|personal","description":""}}
Без текста вокруг."""

SYS_WARMUP_OCR = """Перед тобой скрины статистики Telegram-канала.
Для каждого поста: дата, просмотры, лайки, репосты.
Верни JSON: {"посты":[{"дата":"ДД.ММ.ГГ","просмотры":N,"лайки":N,"репосты":N}],
"по_датам":{"ДД.ММ.ГГ":{"постов":N,"сумма_просмотров":N,"лайки":N,"репосты":N}}}
Без текста вокруг."""

# ══════════════════════════════════════════════════════════════════
# ТУРОПЕРАТОРЫ
# ══════════════════════════════════════════════════════════════════

TO_MAP = {
    "пак групп": "Pac Group", "pac group": "Pac Group",
    "tez tour": "Tez Tour", "тез тур": "Tez Tour",
    "coral": "Coral Travel", "корал": "Coral Travel",
    "sunmar": "Sunmar", "санмар": "Sunmar",
    "pegas": "Pegas", "пегас": "Pegas",
    "anex": "Anex", "анекс": "Anex",
    "инфофлот": "Инфофлот",
    "библио": "Библио", "biblio": "Библио",
    "самотур": "Самотур",
    "круиз": "CruClub", "cruclub": "CruClub",
}

def map_to(raw):
    if not raw: return ""
    low = raw.lower()
    for k, v in TO_MAP.items():
        if k in low: return v
    return raw.strip()

def article(d):
    to = map_to(d.get("tour_operator_raw"))
    if d.get("is_refund"): return "04. Прочие расходы. Возврат"
    if d.get("is_incoming"): return "01. Доходы. Анастасия"
    if "комиссия" in (d.get("doc_type") or ""): return "03. Переменные расходы. Комиссия банка"
    if to: return f"02. Оплата туроператору. {to}"
    return "04. Прочие расходы"

def fin_card(d, tbl):
    lines = [f"📋 Таблица {tbl}",
             f"📅 {d.get('date','?')}",
             f"💰 {fmt(d.get('amount'))} ₽",
             f"💬 {d.get('comment','—')}"]
    if d.get("order_number"): lines.append(f"🔖 Бронь: {d['order_number']}")
    to = map_to(d.get("tour_operator_raw"))
    if to: lines.append(f"🏢 ТО: {to}")
    lines.append("📥 Приход" if d.get("is_incoming") else "📤 Расход")
    if d.get("is_refund"): lines.append("↩️ Возврат")
    return "\n".join(lines)

def write_fin_row(sheet, d, tbl):
    row = [d.get("date", _today()), fmt(d.get("amount")),
           d.get("comment",""), article(d),
           d.get("order_number") or d.get("crm_order_number") or "",
           d.get("note") or "", d.get("note_type") or "",
           map_to(d.get("tour_operator_raw")),
           "Да" if d.get("is_incoming") else "Нет",
           d.get("bank_name") or "",
           d.get("tour_month") or "", d.get("tour_year") or ""]
    sheets_retry(sheet.append_row, row, value_input_option="USER_ENTERED")

# ══════════════════════════════════════════════════════════════════
# ОБРАБОТЧИКИ — НАВИГАЦИЯ
# ══════════════════════════════════════════════════════════════════

@bot.on.message(text=["Начать", "/start", "Старт", "старт"])
async def cmd_start(msg: Message):
    if not allowed(msg.from_id): return
    clear(msg.from_id)
    await send(msg.peer_id, "Привет! Выбери раздел:", kb_main())

@bot.on.message(text="◀ Главная")
async def go_main(msg: Message):
    if not allowed(msg.from_id): return
    clear(msg.from_id)
    await send(msg.peer_id, "Главное меню:", kb_main())

@bot.on.message(text="₽ Финучёт")
async def go_fin(msg: Message):
    if not allowed(msg.from_id): return
    clear(msg.from_id)
    await send(msg.peer_id, "Финучёт:", kb_fin())

@bot.on.message(text="📚 База знаний")
async def go_know(msg: Message):
    if not allowed(msg.from_id): return
    clear(msg.from_id)
    await send(msg.peer_id, "База знаний:", kb_know())

@bot.on.message(text="📦 Заказы")
async def go_orders(msg: Message):
    if not allowed(msg.from_id): return
    clear(msg.from_id)
    await send(msg.peer_id, "Туристы:", kb_orders())

@bot.on.message(text="📋 Задачи")
async def go_tasks(msg: Message):
    if not allowed(msg.from_id): return
    clear(msg.from_id)
    await send(msg.peer_id, "Задачи:", kb_tasks())

@bot.on.message(text="🔧 Полезное")
async def go_useful(msg: Message):
    if not allowed(msg.from_id): return
    clear(msg.from_id)
    await send(msg.peer_id, "Полезное:", kb_useful())

@bot.on.message(text="👤 Личное")
async def go_lichnoe(msg: Message):
    if not allowed(msg.from_id): return
    clear(msg.from_id)
    await send(msg.peer_id, "👤 Личное:", kb_lichnoe())

@bot.on.message(text="🪞 Рефлексия")
async def go_reflection_stub(msg: Message):
    if not allowed(msg.from_id): return
    await send(msg.peer_id,
               "🪞 Рефлексия в VK ещё не подключена — она есть в Telegram, "
               "переношу следующим заходом. Пока пиши заметки туда.",
               kb_lichnoe())

@bot.on.message(text="🌍 Мои поездки")
async def go_travel_stub(msg: Message):
    if not allowed(msg.from_id): return
    await send(msg.peer_id,
               "🌍 Мои поездки в VK ещё не подключены — они есть в Telegram, "
               "переношу следующим заходом.",
               kb_lichnoe())

@bot.on.message(text="🔍 Поиск по базе")
async def go_search_stub(msg: Message):
    if not allowed(msg.from_id): return
    await send(msg.peer_id,
               "🔍 Поиск по базе знаний (Notion) в VK пока не подключён. "
               "В Telegram он работает — там быстрый и глубокий поиск по инструкциям. "
               "Переношу следующим заходом.",
               kb_main())

@bot.on.message(text="◀ Финучёт")
async def back_fin(msg: Message):
    if not allowed(msg.from_id): return
    clear(msg.from_id)
    await send(msg.peer_id, "Финучёт:", kb_fin())

# ══════════════════════════════════════════════════════════════════
# ФИНУЧЁТ
# ══════════════════════════════════════════════════════════════════

@bot.on.message(text=["Таблица 1 (ДДС)", "Таблица 2 (КУДиР)"])
async def start_fin(msg: Message):
    uid = msg.from_id
    if not allowed(uid): return
    tbl = 1 if "1" in (msg.text or "") else 2
    clear(uid)
    states[uid] = {"tbl": tbl}
    batch[uid] = []
    await send(msg.peer_id,
               f"Таблица {tbl}. Отправляй фото чеков и платёжек.\nКогда всё — нажми ✅ Обработать.",
               kb_collect())

@bot.on.message(text="✅ Обработать")
async def process_fin(msg: Message):
    uid = msg.from_id
    if not allowed(uid): return
    st = states.get(uid, {})
    tbl = st.get("tbl")
    if tbl not in (1, 2): return
    docs = batch.get(uid, [])
    if not docs:
        await send(msg.peer_id, "Сначала отправь документы.")
        return
    await send(msg.peer_id, "⏳ Анализирую...")
    try:
        cnt = build_content(docs)
        if len(docs) == 1:
            raw = await ask_claude(SYS_FIN, cnt)
            data_list = [parse_json(raw)]
        else:
            raw = await ask_claude(SYS_FIN_BATCH, cnt)
            parsed = parse_json(raw)
            data_list = parsed if isinstance(parsed, list) else [parsed]
    except Exception as e:
        await send(msg.peer_id, f"Ошибка анализа: {e}", kb_fin())
        return

    ops = [d for d in data_list if d.get("doc_type") != "crm"]
    if not ops:
        await send(msg.peer_id, "Только CRM-скрины, операций нет.", kb_fin())
        return

    sp_id = SP1 if tbl == 1 else SP2
    sp = sheets_retry(gc.open_by_key, sp_id)
    sheet = sp.sheet1

    for d in ops:
        write_fin_row(sheet, d, tbl)
        await send(msg.peer_id, f"✅ Записано!\n\n{fin_card(d, tbl)}")

    batch.pop(uid, None)
    states.pop(uid, None)
    if len(ops) > 1:
        await send(msg.peer_id, f"Всего записей: {len(ops)}", kb_fin())
    else:
        await send(msg.peer_id, "Готово!", kb_fin())

@bot.on.message(text="📊 Финотчёт")
async def finreport(msg: Message):
    uid = msg.from_id
    if not allowed(uid): return
    await send(msg.peer_id, "⏳ Считаю...")
    try:
        rows = sheets_retry(gc.open_by_key, SP1).sheet1.get_all_values()
    except Exception as e:
        await send(msg.peer_id, f"Ошибка: {e}", kb_fin()); return

    tin = tout = 0.0
    by_art: dict = {}
    for r in rows[1:]:
        try:
            amt = float(str(r[1]).replace(",", ".").replace(" ", ""))
            is_in = str(r[8]).lower() in ("да", "true")
            art = r[3] if len(r) > 3 else "—"
            if is_in: tin += amt
            else:
                tout += amt
                by_art[art] = by_art.get(art, 0) + amt
        except: continue

    lines = ["📊 Финотчёт", f"📥 Приход: {fmt(tin)} ₽",
             f"📤 Расход: {fmt(tout)} ₽", f"💰 Итого: {fmt(tin - tout)} ₽", "", "По статьям:"]
    for a, v in sorted(by_art.items(), key=lambda x: -x[1]):
        lines.append(f"  {a}: {fmt(v)} ₽")
    await send(msg.peer_id, "\n".join(lines), kb_fin())

# ══════════════════════════════════════════════════════════════════
# ПОДПИСКИ
# ══════════════════════════════════════════════════════════════════

@bot.on.message(text="🔄 Подписки")
async def cmd_subs(msg: Message):
    uid = msg.from_id
    if not allowed(uid): return
    clear(uid)
    states[uid] = {"sec": "subs"}
    await send(msg.peer_id, "Подписки. Пришли описание или фото для добавления.",
               kb(["📋 Все подписки"], ["◀ Финучёт"]))

@bot.on.message(text="📋 Все подписки")
async def subs_list(msg: Message):
    if not allowed(msg.from_id): return
    try:
        rows = sheets_retry(gc.open_by_key, SP_SUBS).sheet1.get_all_values()
    except Exception as e:
        await send(msg.peer_id, f"Ошибка: {e}"); return
    if len(rows) <= 1:
        await send(msg.peer_id, "Подписок нет.")
        return
    total = 0.0
    lines = ["📋 Подписки:", ""]
    for r in rows[1:]:
        if len(r) < 4: continue
        monthly = r[4] if len(r) > 4 else "0"
        try: total += float(str(monthly).replace(",", "."))
        except: pass
        lines.append(f"• {r[0]} — {r[2]} ₽/{r[3]} мес. [{r[1]}]")
    lines.append(f"\nИтого/мес: {fmt(total)} ₽")
    await send(msg.peer_id, "\n".join(lines))

# ══════════════════════════════════════════════════════════════════
# ГИДЫ
# ══════════════════════════════════════════════════════════════════

@bot.on.message(text="🗺 Гиды")
async def cmd_guides(msg: Message):
    uid = msg.from_id
    if not allowed(uid): return
    clear(uid)
    states[uid] = {"sec": "guides"}
    await send(msg.peer_id, "База гидов.",
               kb(["🔍 Найти гида", "➕ Добавить гида"], ["◀ База знаний"]))

@bot.on.message(text="🔍 Найти гида")
async def guide_search(msg: Message):
    uid = msg.from_id
    if not allowed(uid): return
    states[uid] = {"sec": "guide_search"}
    await send(msg.peer_id, "Напиши запрос (страна, город, имя):", kb_cancel())

@bot.on.message(text="➕ Добавить гида")
async def guide_add(msg: Message):
    uid = msg.from_id
    if not allowed(uid): return
    states[uid] = {"sec": "guide_add"}
    guide_buf[uid] = []
    await send(msg.peer_id, "Пришли контакты гида (текст, фото визитки).\nПотом ✅ Готово.", kb_ok())

# ══════════════════════════════════════════════════════════════════
# ТУРИСТЫ
# ══════════════════════════════════════════════════════════════════

@bot.on.message(text="Добавить туриста")
async def tourist_add(msg: Message):
    uid = msg.from_id
    if not allowed(uid): return
    clear(uid)
    states[uid] = {"sec": "tourist_add"}
    tourist_buf[uid] = []
    await send(msg.peer_id, "Пришли переписку с туристом. Потом ✅ Готово.", kb_ok())

@bot.on.message(text="Найти туриста")
async def tourist_find(msg: Message):
    uid = msg.from_id
    if not allowed(uid): return
    states[uid] = {"sec": "tourist_find"}
    await send(msg.peer_id, "Имя или телефон:", kb_cancel())

@bot.on.message(text="База туристов")
async def tourist_list(msg: Message):
    if not allowed(msg.from_id): return
    try:
        rows = sheets_retry(gc.open_by_key, SP_TOURISTS).sheet1.get_all_values()
    except Exception as e:
        await send(msg.peer_id, f"Ошибка: {e}"); return
    if len(rows) <= 1:
        await send(msg.peer_id, "База пуста."); return
    lines = [f"👥 Туристов: {len(rows)-1}", ""]
    for r in rows[1:20]:
        lines.append(f"• {r[0]} | {r[1] if len(r)>1 else ''} | {r[2] if len(r)>2 else ''}")
    await send(msg.peer_id, "\n".join(lines), kb_orders())

# ══════════════════════════════════════════════════════════════════
# ЗАДАЧИ
# ══════════════════════════════════════════════════════════════════

@bot.on.message(text="Новая задача")
async def task_new(msg: Message):
    uid = msg.from_id
    if not allowed(uid): return
    states[uid] = {"sec": "task_new"}
    await send(msg.peer_id, "Опиши задачу:", kb_cancel())

@bot.on.message(text="Все задачи")
async def tasks_all(msg: Message):
    if not allowed(msg.from_id): return
    try:
        rows = sheets_retry(gc.open_by_key, SP_TASKS).sheet1.get_all_values()
    except Exception as e:
        await send(msg.peer_id, f"Ошибка: {e}"); return
    open_t = [r for r in rows[1:] if str(r[3] if len(r) > 3 else "").strip() != "✅"]
    if not open_t:
        await send(msg.peer_id, "Открытых задач нет 🎉", kb_tasks()); return
    lines = [f"📋 Открытых: {len(open_t)}", ""]
    for r in open_t[:20]:
        lines.append(f"• [{r[0]}] {r[1]}" + (f" ({r[2]})" if len(r) > 2 and r[2] else ""))
    await send(msg.peer_id, "\n".join(lines), kb_tasks())

@bot.on.message(text="Задачи по людям")
async def tasks_person(msg: Message):
    uid = msg.from_id
    if not allowed(uid): return
    states[uid] = {"sec": "task_person"}
    await send(msg.peer_id, "Имя человека:", kb_cancel())

@bot.on.message(text="Выполнить задачу")
async def task_done(msg: Message):
    uid = msg.from_id
    if not allowed(uid): return
    states[uid] = {"sec": "task_done"}
    await send(msg.peer_id, "Часть текста задачи:", kb_cancel())

# ══════════════════════════════════════════════════════════════════
# ПРОГРЕВ
# ══════════════════════════════════════════════════════════════════

@bot.on.message(text="🔥 Прогрев")
async def cmd_warmup(msg: Message):
    uid = msg.from_id
    if not allowed(uid): return
    clear(uid)
    await send(msg.peer_id, "Прогрев канала.", kb_warmup())

@bot.on.message(text="➕ Новый прогрев")
async def warmup_new(msg: Message):
    uid = msg.from_id
    if not allowed(uid): return
    warmup_st[uid] = {"step": "name"}
    await send(msg.peer_id, "Название прогрева (например: Июнь 2026):", kb_cancel())

@bot.on.message(text="📊 Добавить статистику")
async def warmup_stats(msg: Message):
    uid = msg.from_id
    if not allowed(uid): return
    try:
        sp = sheets_retry(gc.open_by_key, SP1)
        try:
            ws = sp.worksheet("Прогревы")
            active = [r for r in ws.get_all_values()[1:] if len(r) > 4 and r[4] == "active"]
        except: active = []
    except Exception as e:
        await send(msg.peer_id, f"Ошибка: {e}"); return
    if not active:
        await send(msg.peer_id, "Нет активных прогревов. Создай новый.")
        return
    warmup_st[uid] = {
        "step": "photos",
        "wid": active[0][0], "wname": active[0][1],
        "plan_sum": float(active[0][2] or 0),
        "subs": int(active[0][5] if len(active[0]) > 5 else 0),
        "photos": [],
    }
    await send(msg.peer_id,
               f"Прогрев: {active[0][1]}\nОтправляй скрины статистики Telegram.",
               kb(["✅ Готово (прогрев)", "❌ Отмена"]))

@bot.on.message(text="📈 Отчёт")
async def warmup_report(msg: Message):
    if not allowed(msg.from_id): return
    try:
        sp = sheets_retry(gc.open_by_key, SP1)
        ws = sp.worksheet("Прогрев_дни")
        rows = ws.get_all_values()
    except Exception as e:
        await send(msg.peer_id, f"Нет данных: {e}"); return
    if len(rows) <= 1:
        await send(msg.peer_id, "Данных по прогревам пока нет."); return
    last = rows[-1]
    lines = ["📈 Последний день прогрева:", f"Прогрев: {last[1]}", f"Дата: {last[2]}",
             f"Постов: {last[3]}", f"Просмотров: {last[4]}",
             f"Продаж: {last[9]}", f"Сумма: {last[10]} ₽", f"% плана: {last[12]}%"]
    await send(msg.peer_id, "\n".join(lines), kb_warmup())

# ══════════════════════════════════════════════════════════════════
# ПИТАНИЕ
# ══════════════════════════════════════════════════════════════════

@bot.on.message(text="🍎 Питание")
async def cmd_nutr(msg: Message):
    uid = msg.from_id
    if not allowed(uid): return
    if NUTR_OWNER and uid != NUTR_OWNER:
        await send(msg.peer_id, "Дневник питания доступен только владельцу."); return
    clear(uid)
    states[uid] = {"sec": "nutr"}
    await send(msg.peer_id, "Дневник питания. Пришли фото еды или напиши что съела.", kb_nutr())

@bot.on.message(text=["📊 Сегодня", "📅 Неделя", "📈 Месяц"])
async def nutr_report(msg: Message):
    uid = msg.from_id
    if not allowed(uid): return
    t = msg.text or ""
    period = "day" if "Сегодня" in t else "week" if "Неделя" in t else "month"
    now = _now().date()
    cutoff = now if period == "day" else now - datetime.timedelta(days=7 if period == "week" else 30)
    try:
        rows = sheets_retry(gc.open_by_key, SP_NUTR).sheet1.get_all_values()
    except Exception as e:
        await send(msg.peer_id, f"Ошибка: {e}"); return
    kcal = p = f = c = count = 0.0
    for r in rows[1:]:
        if len(r) < 8: continue
        try:
            dt = datetime.datetime.fromisoformat(r[2]).date()
            if dt < cutoff or dt > now: continue
            kcal += float(r[4] or 0); p += float(r[5] or 0)
            f += float(r[6] or 0); c += float(r[7] or 0); count += 1
        except: continue
    label = {"day": "Сегодня", "week": "Неделя", "month": "Месяц"}[period]
    if count == 0:
        await send(msg.peer_id, f"{label}: записей нет."); return
    await send(msg.peer_id,
               f"🍽 {label} ({int(count)} приёмов):\n🔥 {fmt(kcal)} ккал\n"
               f"🥩 Б: {fmt(p)} г | 🧈 Ж: {fmt(f)} г | 🍞 У: {fmt(c)} г")

# ══════════════════════════════════════════════════════════════════
# КАЛЕНДАРЬ
# ══════════════════════════════════════════════════════════════════

@bot.on.message(text="▶ Календарь")
async def cmd_cal(msg: Message):
    uid = msg.from_id
    if not allowed(uid): return
    clear(uid)
    states[uid] = {"sec": "cal"}
    await send(msg.peer_id, "Календарь.", kb_cal())

@bot.on.message(text=["📅 План на сегодня", "📆 На неделю"])
async def cal_list(msg: Message):
    if not allowed(msg.from_id): return
    days = 1 if "сегодня" in (msg.text or "").lower() else 7
    now = _now()
    tmin = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    tmax = (now + datetime.timedelta(days=days)).replace(hour=23, minute=59, second=59).isoformat()
    events = []
    for cal in [CAL_WORK, CAL_FAM, CAL_PER]:
        if not cal: continue
        try:
            res = await asyncio.to_thread(
                cal_svc.events().list(
                    calendarId=cal, timeMin=tmin, timeMax=tmax,
                    singleEvents=True, orderBy="startTime").execute)
            events.extend(res.get("items", []))
        except: pass
    if not events:
        await send(msg.peer_id, "Событий нет 🎉"); return
    lines = [f"📅 {'Сегодня' if days==1 else 'Неделя'}:", ""]
    for ev in sorted(events, key=lambda e: e.get("start",{}).get("dateTime", e.get("start",{}).get("date",""))):
        s = ev.get("start", {})
        dt_s = s.get("dateTime", s.get("date", ""))
        try:
            if "T" in dt_s:
                t_label = datetime.datetime.fromisoformat(dt_s).strftime("%d.%m %H:%M")
            else:
                t_label = datetime.date.fromisoformat(dt_s).strftime("%d.%m")
        except: t_label = dt_s
        lines.append(f"• {t_label} — {ev.get('summary','—')}")
    await send(msg.peer_id, "\n".join(lines))

@bot.on.message(text="➕ Добавить событие")
async def cal_add(msg: Message):
    uid = msg.from_id
    if not allowed(uid): return
    states[uid] = {"sec": "cal_add"}
    await send(msg.peer_id, "Опиши событие:", kb_cancel())

# ══════════════════════════════════════════════════════════════════
# ИНСТРУКЦИИ (Notion)
# ══════════════════════════════════════════════════════════════════

@bot.on.message(text="📘 Инструкции")
async def cmd_instr(msg: Message):
    uid = msg.from_id
    if not allowed(uid): return
    clear(uid)
    await send(msg.peer_id, "Инструкции (Notion).",
               kb(["➕ Новая инструкция"], ["◀ База знаний"]))

@bot.on.message(text="➕ Новая инструкция")
async def instr_new(msg: Message):
    uid = msg.from_id
    if not allowed(uid): return
    states[uid] = {"sec": "instr", "step": "name"}
    await send(msg.peer_id, "Название инструкции:", kb_cancel())

# ══════════════════════════════════════════════════════════════════
# ПАРОЛИ
# ══════════════════════════════════════════════════════════════════

@bot.on.message(text="🔐 Пароли")
async def cmd_passes(msg: Message):
    uid = msg.from_id
    if not allowed(uid): return
    last = auth_time.get(uid, 0)
    if time.time() - last < 1800:
        states[uid] = {"sec": "passes", "step": "menu"}
        await send(msg.peer_id, "Менеджер паролей.", kb_passes())
        return
    states[uid] = {"sec": "passes", "step": "auth"}
    await send(msg.peer_id, "Введи мастер-пароль:", kb_cancel())

# ══════════════════════════════════════════════════════════════════
# ГЛАВНЫЙ ОБРАБОТЧИК
# ══════════════════════════════════════════════════════════════════

@bot.on.message()
async def handle_all(msg: Message):
    uid = msg.from_id
    if not allowed(uid):
        await msg.answer("Нет доступа.")
        return

    text = (msg.text or "").strip()
    atts = await get_atts(msg)

    if text in ("❌ Отмена", "Отмена"):
        clear(uid)
        await send(msg.peer_id, "Отменено.", kb_main())
        return

    st  = states.get(uid, {})
    sec = st.get("sec")
    step = st.get("step")

    # ── ФИНАНСЫ: накопление ──────────────────────────────────────
    if sec is None and states.get(uid, {}).get("tbl"):
        tbl = states[uid]["tbl"]
        docs = batch.get(uid, [])
        if atts:
            docs += [a for a in atts if a["type"] in ("image","document")]
            batch[uid] = docs
            await send(msg.peer_id, f"Принято ({len(docs)} шт.). Ещё или ✅ Обработать.", kb_collect())
        elif text:
            docs.append({"type": "text", "text": text})
            batch[uid] = docs
            await send(msg.peer_id, f"Текст принят ({len(docs)} шт.).", kb_collect())
        return

    if st.get("tbl") and sec is None:
        docs = batch.get(uid, [])
        if atts:
            docs += [a for a in atts if a["type"] in ("image","document")]
            batch[uid] = docs
            await send(msg.peer_id, f"Принято ({len(docs)}). Ещё или ✅ Обработать.", kb_collect())
        elif text:
            docs.append({"type": "text", "text": text})
            batch[uid] = docs
            await send(msg.peer_id, f"Принято.", kb_collect())
        return

    # ── ПОДПИСКИ ─────────────────────────────────────────────────
    if sec == "subs":
        docs_in = [a for a in atts if a["type"] in ("image","document")]
        if text: docs_in.append({"type": "text", "text": text})
        if not docs_in: return
        await send(msg.peer_id, "⏳ Определяю...")
        try:
            raw = await ask_claude(SYS_SUBS, build_content(docs_in))
            d = parse_json(raw)
            sp = sheets_retry(gc.open_by_key, SP_SUBS)
            sheets_retry(sp.sheet1.append_row, [
                d.get("name",""), d.get("category",""),
                fmt(d.get("amount")), str(d.get("period_months","")),
                fmt(d.get("monthly_cost"))
            ])
            await send(msg.peer_id, f"✅ {d.get('name')} — {fmt(d.get('amount'))} ₽/{d.get('period_months')} мес.\nВ месяц: {fmt(d.get('monthly_cost'))} ₽")
        except Exception as e:
            await send(msg.peer_id, f"Ошибка: {e}")
        return

    # ── U-ON: поиск заявки ───────────────────────────────────────
    if sec == "uon" and step == "uon_query":
        if not text: return
        if len(text) < 2:
            await send(msg.peer_id, "Минимум 2 символа. Попробуй ещё раз:")
            return
        await send(msg.peer_id, f"⏳ Ищу «{text}» в U-ON…")
        try:
            leads = await uon_search(text)
        except Exception as e:
            log.error(f"uon_search: {e}")
            await send(msg.peer_id, f"Ошибка U-ON: {e}", kb_uon())
            states.pop(uid, None)
            return
        states.pop(uid, None)
        if not leads:
            await send(msg.peer_id, "Ничего не нашлось.", kb_uon())
            return
        if len(leads) > 5:
            await send(msg.peer_id, f"Найдено {len(leads)}, показываю первые 5:")
        for ld in leads[:5]:
            await send(msg.peer_id, fmt_lead_card_vk(ld))
        if len(leads) > 5:
            await send(msg.peer_id,
                       f"…и ещё {len(leads) - 5}. Уточни запрос если нужно.",
                       kb_uon())
        else:
            await send(msg.peer_id, "—", kb_uon())
        return

    # ── ГИДЫ: поиск ──────────────────────────────────────────────
    if sec == "guide_search":
        if not text: return
        await send(msg.peer_id, f"🔍 Ищу: {text}...")
        found = []
        for sp_id in [SP_GUIDES, SP_GUIDES_ALL, SP_GUIDES_CO]:
            if not sp_id: continue
            try:
                rows = sheets_retry(gc.open_by_key, sp_id).sheet1.get_all_values()
                for r in rows[1:]:
                    if fuzzy(text, " ".join(r)):
                        found.append(r)
            except: pass
        if not found:
            await send(msg.peer_id, "Не найдено.", kb_know())
        else:
            for r in found[:5]:
                await send(msg.peer_id, " | ".join(x for x in r[:5] if x))
        states.pop(uid, None)
        return

    # ── ГИДЫ: добавление ─────────────────────────────────────────
    if sec == "guide_add":
        buf = guide_buf.get(uid, [])
        if atts: buf += [a for a in atts if a["type"] in ("image","document")]
        if text and text not in ("✅ Готово","Готово"): buf.append({"type":"text","text":text})
        guide_buf[uid] = buf
        if text in ("✅ Готово","Готово"):
            if not buf: await send(msg.peer_id, "Пришли данные гида."); return
            await send(msg.peer_id, "⏳ Обрабатываю...")
            try:
                raw = await ask_claude(SYS_GUIDE, build_content(buf))
                items = parse_json(raw)
                if not isinstance(items, list): items = [items]
                sp = sheets_retry(gc.open_by_key, SP_GUIDES)
                for item in items:
                    sheets_retry(sp.sheet1.append_row, [
                        item.get("country",""), item.get("city",""),
                        item.get("guide_name",""), item.get("contacts",""),
                        item.get("description",""), _today()])
                await send(msg.peer_id, f"✅ Добавлено гидов: {len(items)}", kb_know())
            except Exception as e:
                await send(msg.peer_id, f"Ошибка: {e}", kb_know())
            guide_buf.pop(uid, None); states.pop(uid, None)
        else:
            await send(msg.peer_id, f"Принято. Ещё или ✅ Готово.", kb_ok())
        return

    # ── ТУРИСТЫ: добавление ──────────────────────────────────────
    if sec == "tourist_add":
        buf = tourist_buf.get(uid, [])
        if atts: buf += [a for a in atts if a["type"] in ("image","document")]
        if text and text not in ("✅ Готово","Готово"): buf.append({"type":"text","text":text})
        tourist_buf[uid] = buf
        if text in ("✅ Готово","Готово"):
            if not buf: await send(msg.peer_id, "Пришли переписку."); return
            await send(msg.peer_id, "⏳ Извлекаю данные...")
            try:
                raw = await ask_claude(SYS_TOURIST, build_content(buf))
                d = parse_json(raw)
                sp = sheets_retry(gc.open_by_key, SP_TOURISTS)
                sheets_retry(sp.sheet1.append_row, [
                    d.get("name",""), safe_phone(d.get("phone","")),
                    d.get("destination",""), d.get("dates",""),
                    d.get("budget",""), d.get("group",""),
                    d.get("wishes",""), d.get("source",""),
                    d.get("comments",""), _today()])
                card = f"👤 {d.get('name','—')}\n📞 {d.get('phone','—')}\n✈️ {d.get('destination','—')}\n📅 {d.get('dates','—')}"
                states[uid] = {"sec": "tourist_actions", "data": json.dumps(d, ensure_ascii=False)}
                await send(msg.peer_id, f"✅ Добавлено!\n\n{card}", kb_tourist())
            except Exception as e:
                await send(msg.peer_id, f"Ошибка: {e}", kb_orders())
            tourist_buf.pop(uid, None)
        else:
            await send(msg.peer_id, "Принято. Ещё или ✅ Готово.", kb_ok())
        return

    # ── ТУРИСТЫ: действия ────────────────────────────────────────
    if sec == "tourist_actions":
        d = json.loads(st.get("data", "{}"))
        profile = "\n".join(f"{k}: {v}" for k, v in d.items())
        if text == "👤 Портрет клиента":
            await send(msg.peer_id, "⏳...")
            ans = await ask_claude(SYS_PORTRAIT, [{"type":"text","text":profile}], max_tokens=1000)
            await send(msg.peer_id, ans)
        elif text == "📝 Скрипт продаж":
            await send(msg.peer_id, "⏳...")
            ans = await ask_claude(SYS_SCRIPT, [{"type":"text","text":profile}], max_tokens=1500)
            await send(msg.peer_id, ans)
        elif text == "◀ Главная":
            clear(uid); await send(msg.peer_id, "Главное меню:", kb_main())
        return

    # ── ТУРИСТЫ: поиск ───────────────────────────────────────────
    if sec == "tourist_find":
        if not text: return
        try:
            rows = sheets_retry(gc.open_by_key, SP_TOURISTS).sheet1.get_all_values()
            found = [r for r in rows[1:] if fuzzy(text, " ".join(r))]
        except Exception as e:
            await send(msg.peer_id, f"Ошибка: {e}"); states.pop(uid,None); return
        if not found: await send(msg.peer_id, f"'{text}' не найдено.")
        else:
            for r in found[:5]:
                await send(msg.peer_id, f"👤 {r[0]} | 📞 {r[1] if len(r)>1 else ''} | ✈️ {r[2] if len(r)>2 else ''}")
        states.pop(uid, None)
        return

    # ── ЗАДАЧИ: новая ────────────────────────────────────────────
    if sec == "task_new":
        if not text: return
        who = ""
        m = re.search(r"для\s+(\w+)", text, re.I)
        if m: who = m.group(1)
        try:
            sheets_retry(gc.open_by_key, SP_TASKS).sheet1.append_row([_today(), text, who, ""])
            await send(msg.peer_id, f"✅ Задача: {text}", kb_tasks())
        except Exception as e:
            await send(msg.peer_id, f"Ошибка: {e}", kb_tasks())
        states.pop(uid, None); return

    # ── ЗАДАЧИ: по людям ─────────────────────────────────────────
    if sec == "task_person":
        if not text: return
        try:
            rows = sheets_retry(gc.open_by_key, SP_TASKS).sheet1.get_all_values()
            found = [r for r in rows[1:] if fuzzy(text, r[2] if len(r)>2 else "")]
        except Exception as e:
            await send(msg.peer_id, f"Ошибка: {e}"); states.pop(uid,None); return
        if not found: await send(msg.peer_id, f"Задач для '{text}' нет.")
        else:
            lines = [f"Задачи для '{text}':", ""]
            for r in found:
                done = "✅" if str(r[3] if len(r)>3 else "").strip()=="✅" else "⬜"
                lines.append(f"{done} [{r[0]}] {r[1]}")
            await send(msg.peer_id, "\n".join(lines))
        states.pop(uid, None); return

    # ── ЗАДАЧИ: выполнить ────────────────────────────────────────
    if sec == "task_done":
        if not text: return
        try:
            ws = sheets_retry(gc.open_by_key, SP_TASKS).sheet1
            rows = ws.get_all_values()
            for i, r in enumerate(rows[1:], start=2):
                if fuzzy(text, r[1] if len(r)>1 else ""):
                    ws.update_cell(i, 4, "✅")
                    await send(msg.peer_id, f"✅ Выполнено: {r[1]}", kb_tasks())
                    states.pop(uid, None); return
            await send(msg.peer_id, "Не найдено.")
        except Exception as e:
            await send(msg.peer_id, f"Ошибка: {e}")
        states.pop(uid, None); return

    # ── ПРОГРЕВ: состояния ───────────────────────────────────────
    ws_st = warmup_st.get(uid, {})
    if ws_st:
        await handle_warmup(msg, uid, text, atts, ws_st)
        return

    # ── ПИТАНИЕ: добавить ────────────────────────────────────────
    if sec == "nutr":
        if NUTR_OWNER and uid != NUTR_OWNER: return
        docs_n = [a for a in atts if a["type"] == "image"]
        if text and text not in ("📊 Сегодня","📅 Неделя","📈 Месяц"):
            docs_n.append({"type":"text","text":text})
        if not docs_n: return
        await send(msg.peer_id, "⏳ Считаю КБЖУ...")
        try:
            raw = await ask_claude(SYS_NUTR, build_content(docs_n), max_tokens=300)
            d = parse_json(raw)
            sp = sheets_retry(gc.open_by_key, SP_NUTR)
            sheets_retry(sp.sheet1.append_row, [
                str(int(time.time())), str(uid), _now().isoformat(),
                d.get("meal_name",""), "",
                fmt(d.get("kcal")), fmt(d.get("protein")),
                fmt(d.get("fat")), fmt(d.get("carbs"))])
            await send(msg.peer_id,
                       f"✅ {d.get('meal_name','Блюдо')}\n"
                       f"🔥 {d.get('kcal')} ккал | Б:{d.get('protein')} Ж:{d.get('fat')} У:{d.get('carbs')}")
        except Exception as e:
            await send(msg.peer_id, f"Ошибка: {e}")
        return

    # ── КАЛЕНДАРЬ: добавить ──────────────────────────────────────
    if sec == "cal_add":
        docs_c = [a for a in atts if a["type"] in ("image","document")]
        if text: docs_c.append({"type":"text","text":text})
        if not docs_c: return
        today = _now().strftime("%Y-%m-%d")
        sys_c = SYS_CAL.format(today=today)
        try:
            raw = await ask_claude(sys_c, build_content(docs_c), max_tokens=400)
            d = parse_json(raw)
            cal_map = {"work": CAL_WORK, "family": CAL_FAM, "personal": CAL_PER}
            cal_id = cal_map.get(d.get("calendar","personal"), CAL_PER) or CAL_PER
            if not cal_id:
                await send(msg.peer_id, "Календарь не настроен."); states.pop(uid,None); return
            date_ = d.get("date", today)
            time_ = d.get("time")
            if time_:
                start = end = {"dateTime": f"{date_}T{time_}:00+03:00", "timeZone": TIMEZONE}
            else:
                start = end = {"date": date_}
            event = {"summary": d.get("title","Событие"), "start": start, "end": end}
            await asyncio.to_thread(
                cal_svc.events().insert(calendarId=cal_id, body=event).execute)
            await send(msg.peer_id,
                       f"✅ {d.get('title')}\n📅 {date_}" + (f" в {time_}" if time_ else ""),
                       kb_cal())
        except Exception as e:
            await send(msg.peer_id, f"Ошибка: {e}")
        states.pop(uid, None); return

    # ── ИНСТРУКЦИИ ───────────────────────────────────────────────
    if sec == "instr":
        if step == "name":
            if not text: return
            states[uid] = {**st, "step": "steps", "title": text, "blocks": [], "n": 1}
            await send(msg.peer_id, f"Название: {text}\nПиши шаги по одному. Когда всё — ✅ Готово.", kb_ok())
        elif step == "steps":
            blocks = list(st.get("blocks",[]))
            n = st.get("n", 1)
            if text in ("✅ Готово","Готово"):
                if not blocks: await send(msg.peer_id, "Добавь хотя бы один шаг."); return
                title = st.get("title","Инструкция")
                await send(msg.peer_id, "⏳ Создаю в Notion...")
                url = await create_notion_page(title, blocks)
                states.pop(uid, None)
                if url: await send(msg.peer_id, f"✅ {title}\n🔗 {url}", kb_know())
                else: await send(msg.peer_id, "Notion не настроен (добавь NOTION_TOKEN).", kb_know())
            else:
                if text:
                    blocks.append({"object":"block","type":"paragraph",
                                   "paragraph":{"rich_text":[{"type":"text","text":{"content":f"Шаг {n}: {text}"}}]}})
                    states[uid] = {**st, "blocks": blocks, "n": n+1}
                    await send(msg.peer_id, f"Шаг {n} добавлен.", kb_ok())
        return

    # ── ПАРОЛИ ───────────────────────────────────────────────────
    if sec == "passes":
        await handle_passes(msg, uid, text, st)
        return

    # ── Неизвестная команда ──────────────────────────────────────
    if text:
        await send(msg.peer_id, "Не понял. Используй меню.", kb_main())


# ══════════════════════════════════════════════════════════════════
# ПРОГРЕВ — обработчик
# ══════════════════════════════════════════════════════════════════

async def handle_warmup(msg: Message, uid, text, atts, ws):
    step = ws.get("step")
    peer = msg.peer_id

    if step == "name":
        if not text: return
        warmup_st[uid] = {**ws, "step": "plan_sum", "name": text}
        await send(peer, f"Название: {text}\nПлан продаж (руб):", kb_cancel())

    elif step == "plan_sum":
        try:
            v = float(text.replace(" ","").replace(",","."))
            warmup_st[uid] = {**ws, "step": "plan_count", "plan_sum": v}
            await send(peer, f"План: {fmt(v)} ₽\nПлан продаж (штук):")
        except: await send(peer, "Введи число:")

    elif step == "plan_count":
        try:
            v = int(text)
            warmup_st[uid] = {**ws, "step": "subs", "plan_count": v}
            await send(peer, f"Штук: {v}\nКол-во подписчиков канала:")
        except: await send(peer, "Введи целое число:")

    elif step == "subs":
        try:
            subs = int(text)
            wid = str(int(time.time()))
            name = ws.get("name","")
            sp = sheets_retry(gc.open_by_key, SP1)
            try: ww = sp.worksheet("Прогревы")
            except: ww = sp.add_worksheet("Прогревы", 1000, 20)
            sheets_retry(ww.append_row, [wid, name, fmt(ws.get("plan_sum",0)),
                                         str(ws.get("plan_count",0)), "active", str(subs)])
            warmup_st.pop(uid, None)
            await send(peer, f"✅ Прогрев '{name}' создан! Подписчиков: {subs}", kb_warmup())
        except Exception as e:
            await send(peer, f"Ошибка: {e}")

    elif step == "photos":
        photos = ws.get("photos", [])
        for a in atts:
            if a["type"] == "image":
                photos.append(base64.b64decode(a["data"]))
        warmup_st[uid] = {**ws, "photos": photos}
        if text in ("✅ Готово (прогрев)", "Готово", "готово") and photos:
            await send(peer, f"⏳ Анализирую {len(photos)} скрин(ов) через Claude...")
            content = [{"type":"image","source":{"type":"base64","media_type":"image/jpeg",
                        "data":base64.b64encode(p).decode()}} for p in photos]
            try:
                raw = await ask_claude(SYS_WARMUP_OCR, content, model=CLAUDE_VISION, max_tokens=3000)
                data = parse_json(raw)
                by_date = data.get("по_датам", {})
            except Exception as e:
                await send(peer, f"Ошибка OCR: {e}"); return
            dates = list(by_date.keys())
            if not dates: await send(peer, "Не удалось распознать."); return
            warmup_st[uid] = {**ws, "step":"sales", "by_date":by_date,
                              "queue": dates, "done": []}
            d0 = dates[0]; dd = by_date[d0]
            await send(peer,
                       f"📅 {d0}\n📝 Постов: {dd.get('постов',0)} | 👁 {dd.get('сумма_просмотров',0)} | ❤️ {dd.get('лайки',0)}\n\nПродажи — кол-во:",
                       kb_cancel())
        elif photos:
            await send(peer, f"{len(photos)} фото. Ещё или ✅ Готово (прогрев).",
                       kb(["✅ Готово (прогрев)","❌ Отмена"]))
        else:
            await send(peer, "Пришли скрины статистики.", kb(["✅ Готово (прогрев)","❌ Отмена"]))

    elif step == "sales":
        try: cnt = int(text)
        except: await send(peer, "Введи число продаж:"); return
        warmup_st[uid] = {**ws, "step":"sum", "sales_cnt": cnt}
        await send(peer, "Сумма продаж (руб):")

    elif step == "sum":
        try: total = float(text.replace(" ","").replace(",","."))
        except: await send(peer, "Введи сумму:"); return
        warmup_st[uid] = {**ws, "step":"commission", "sales_sum": total}
        await send(peer, "Комиссия (руб):")

    elif step == "commission":
        try: comm = float(text.replace(" ","").replace(",","."))
        except: await send(peer, "Введи комиссию:"); return

        queue = list(ws.get("queue",[]))
        by_date = ws.get("by_date",{})
        done = ws.get("done",[])
        cur = queue.pop(0)
        dd = by_date.get(cur, {})
        subs = ws.get("subs",1) or 1
        posts = dd.get("постов",1) or 1
        views = dd.get("сумма_просмотров",0)
        likes = dd.get("лайки",0)
        prev = sum(d.get("sales_sum",0) for d in done)
        cum = prev + ws.get("sales_sum",0)
        plan = ws.get("plan_sum",1) or 1
        pct = round(cum / plan * 100, 1)
        done.append({"date":cur,"sales_cnt":ws.get("sales_cnt",0),
                     "sales_sum":ws.get("sales_sum",0),"commission":comm,"pct":pct})
        try:
            sp = sheets_retry(gc.open_by_key, SP1)
            try: wd = sp.worksheet("Прогрев_дни")
            except: wd = sp.add_worksheet("Прогрев_дни", 10000, 20)
            sheets_retry(wd.append_row, [ws.get("wid",""), ws.get("wname",""),
                                         cur, str(posts), str(views), "",
                                         str(likes), "", "",
                                         str(ws.get("sales_cnt",0)), fmt(ws.get("sales_sum",0)),
                                         fmt(comm), str(pct)])
        except Exception as e:
            log.warning(f"warmup save: {e}")

        await send(peer, f"✅ День {cur} сохранён. % плана: {pct}%")
        if queue:
            d_next = queue[0]; dd_next = by_date.get(d_next,{})
            warmup_st[uid] = {**ws, "step":"sales", "queue":queue, "done":done}
            await send(peer,
                       f"📅 {d_next}\n📝 {dd_next.get('постов',0)} постов | 👁 {dd_next.get('сумма_просмотров',0)} | ❤️ {dd_next.get('лайки',0)}\n\nПродажи — кол-во:",
                       kb_cancel())
        else:
            warmup_st.pop(uid, None)
            await send(peer, f"✅ Готово! Дней обработано: {len(done)}. % плана: {pct}%", kb_warmup())


# ══════════════════════════════════════════════════════════════════
# ПАРОЛИ — обработчик
# ══════════════════════════════════════════════════════════════════

async def handle_passes(msg: Message, uid, text, st):
    step = st.get("step")
    peer = msg.peer_id

    if step == "auth":
        if not text: return
        h = hashlib.sha256(text.encode()).hexdigest()
        try:
            sp = sheets_retry(gc.open_by_key, SP_PASSES)
            rows = sp.sheet1.get_all_values()
            if any(str(r[0])==str(uid) and r[1]==h for r in rows):
                auth_time[uid] = time.time()
                states[uid] = {"sec":"passes","step":"menu"}
                await send(peer, "Менеджер паролей.", kb_passes())
            elif not any(str(r[0])==str(uid) for r in rows):
                sheets_retry(sp.sheet1.append_row, [str(uid), h, _today()])
                auth_time[uid] = time.time()
                states[uid] = {"sec":"passes","step":"menu"}
                await send(peer, "✅ Мастер-пароль установлен!", kb_passes())
            else:
                await send(peer, "Неверный пароль.", kb_cancel())
        except Exception as e:
            await send(peer, f"Ошибка: {e}")

    elif step == "menu":
        if text == "🔍 Найти пароль":
            states[uid] = {**st, "step":"find"}
            await send(peer, "Название сервиса:", kb_cancel())
        elif text == "➕ Добавить пароль":
            states[uid] = {**st, "step":"add_name"}
            await send(peer, "Название сервиса:", kb_cancel())
        elif text == "📋 Все пароли":
            try:
                sp = sheets_retry(gc.open_by_key, SP_PASSES)
                try: ws = sp.worksheet("Пароли")
                except: await send(peer, "Паролей нет."); return
                rows = [r for r in ws.get_all_values()[1:] if str(r[0])==str(uid)]
                if not rows: await send(peer, "Паролей нет."); return
                await send(peer, "🔐 Сервисы:\n" + "\n".join(f"• {r[1]}" for r in rows if len(r)>1))
            except Exception as e:
                await send(peer, f"Ошибка: {e}")

    elif step == "find":
        if not text: return
        try:
            sp = sheets_retry(gc.open_by_key, SP_PASSES)
            ws = sp.worksheet("Пароли")
            found = [r for r in ws.get_all_values()[1:] if str(r[0])==str(uid) and fuzzy(text, r[1] if len(r)>1 else "")]
            if not found: await send(peer, f"'{text}' не найдено.")
            else:
                for r in found:
                    await send(peer, f"🔐 {r[1]}\n👤 {r[2] if len(r)>2 else '—'}\n🔑 {r[3] if len(r)>3 else '—'}")
        except Exception as e:
            await send(peer, f"Ошибка: {e}")
        states[uid] = {"sec":"passes","step":"menu"}
        await send(peer, "Менеджер паролей.", kb_passes())

    elif step == "add_name":
        if not text: return
        states[uid] = {**st, "step":"add_login", "p_name": text}
        await send(peer, f"Сервис: {text}\nЛогин:")

    elif step == "add_login":
        states[uid] = {**st, "step":"add_pwd", "p_login": text}
        await send(peer, "Пароль:")

    elif step == "add_pwd":
        try:
            sp = sheets_retry(gc.open_by_key, SP_PASSES)
            try: ws = sp.worksheet("Пароли")
            except: ws = sp.add_worksheet("Пароли", 1000, 10)
            sheets_retry(ws.append_row, [str(uid), st.get("p_name",""), st.get("p_login",""), text, "", _today()])
            await send(peer, f"✅ Пароль для {st.get('p_name','')} сохранён.")
        except Exception as e:
            await send(peer, f"Ошибка: {e}")
        states[uid] = {"sec":"passes","step":"menu"}
        await send(peer, "Менеджер паролей.", kb_passes())


# ══════════════════════════════════════════════════════════════════
# U-ON TRAVEL CRM (read-only port из finance-bot, один аккаунт)
# ══════════════════════════════════════════════════════════════════

UON_BASE = "https://api.u-on.ru"
UON_SCAN_PAGES = 60
UON_SCAN_PAGES_DEEP = 200
uon_leads_cache_vk: dict[int, dict[str, dict]] = {}

def uon_configured() -> bool:
    return bool(UON_API_KEY and UON_ACCOUNT_ID)

def uon_lead_url(lead_id) -> str:
    if not UON_ACCOUNT_ID or not lead_id: return ""
    return f"https://id{UON_ACCOUNT_ID}.u-on.ru/request_edit.php?r_id={lead_id}"

async def _uon_get(s, path):
    """GET к U-ON. На 'нет данных' API возвращает 404 с валидным JSON-телом."""
    url = f"{UON_BASE}/{UON_API_KEY}/{path}"
    try:
        async with s.get(url, timeout=aiohttp.ClientTimeout(total=15)) as r:
            txt = await r.text()
            try: data = json.loads(txt)
            except Exception: data = None
            if r.status == 200 and data is not None: return data
            if r.status == 404 and isinstance(data, dict): return data
            return None
    except Exception as e:
        log.debug(f"_uon_get {path}: {e}")
        return None

async def _uon_post(s, path, payload):
    url = f"{UON_BASE}/{UON_API_KEY}/{path}"
    try:
        async with s.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=15)) as r:
            txt = await r.text()
            try: data = json.loads(txt)
            except Exception: data = None
            if r.status in (200, 404) and isinstance(data, dict): return data
            return None
    except Exception as e:
        log.debug(f"_uon_post {path}: {e}")
        return None

def _u_as_list(v):
    if v is None: return []
    if isinstance(v, list): return v
    if isinstance(v, dict): return [v]
    return []

def _u_pick(d, *keys):
    if not isinstance(d, dict): return ""
    for k in keys:
        v = d.get(k)
        if v not in (None, "", 0, "0", "0.00"): return v
    return ""

def _u_digits(s):
    return "".join(ch for ch in str(s or "") if ch.isdigit())

def _u_normalize_phone(p):
    d = _u_digits(p)
    if len(d) == 11 and d[0] in ("7", "8"): d = d[1:]
    return d

def _u_classify(phrase):
    raw = (phrase or "").strip()
    digits = _u_digits(raw)
    has_alpha = any(ch.isalpha() for ch in raw)
    if has_alpha and digits and " " not in raw.strip(): return "operator_num"
    if has_alpha: return "name"
    if len(digits) >= 10: return "phone"
    if 1 <= len(digits) <= 9: return "id"
    return "name"

def _u_person_matches(p, phrase_lower, qtype="name"):
    if not isinstance(p, dict): return False
    if qtype == "phone":
        target = _u_normalize_phone(phrase_lower)
        if not target: return False
        for k, v in p.items():
            if isinstance(k, str) and k.startswith("_"): continue
            if v in (None, "", 0, "0"): continue
            if not isinstance(v, (str, int)): continue
            digits = _u_normalize_phone(v)
            if len(digits) < 7: continue
            if target in digits: return True
        return False
    fields = (
        _u_pick(p, "surname", "client_surname", "u_surname", "last_name", "fam",
                "t_surname", "tourist_surname", "familiya"),
        _u_pick(p, "name", "client_name", "u_name", "first_name", "imya",
                "t_name", "tourist_name"),
        _u_pick(p, "middle_name", "client_middle_name", "u_middle_name", "patronymic", "otch"),
        _u_pick(p, "email", "client_email", "u_email", "t_email"),
    )
    for f in fields:
        if f and phrase_lower in str(f).lower(): return True
    return False

_U_NOT_LEAD_ID_KEYS = frozenset({
    "manager_id", "user_id", "u_id", "client_id", "id_client", "id_user",
    "id_manager", "id_status", "status_id", "id_country", "country_id",
    "id_region", "region_id", "id_city", "city_id", "id_currency", "currency_id",
    "tour_operator_id", "id_tour_operator", "operator_id", "id_operator",
    "id_office", "office_id", "id_source", "source_id",
})

_U_OPERATOR_NUM_KEYS = (
    "reservation_number", "tour_operator_number", "touroperator_number",
    "tour_number", "number_tour", "tour_op_number", "nomer_zayavki",
    "zayavka_to", "to_number", "n_zayavka", "r_num_tur", "num_tur",
    "operator_number", "booking_number", "bron_number", "nomer_broni",
    "code_tour", "tour_code", "r_code",
)

def _u_operator_num_matches(lead, query):
    if not isinstance(lead, dict) or not query: return False
    q = query.strip().lower()
    for k in _U_OPERATOR_NUM_KEYS:
        v = lead.get(k)
        if v and q == str(v).strip().lower(): return True
    for k, v in lead.items():
        if not isinstance(v, str) or not v: continue
        if q == v.strip().lower(): return True
    return False

def _u_lead_id_matches(lead, query_digits):
    if not isinstance(lead, dict) or not query_digits: return False
    for k in ("id", "lead_id", "r_id", "request_id", "lead_number", "number"):
        v = lead.get(k)
        if v and str(v).strip() == query_digits: return True
    for k, v in lead.items():
        if not isinstance(k, str): continue
        kl = k.lower()
        if kl in _U_NOT_LEAD_ID_KEYS: continue
        if kl in ("id", "lead_id", "r_id", "request_id", "lead_number", "number") \
           or (kl.endswith("_id") and kl not in _U_NOT_LEAD_ID_KEYS):
            if v and str(v).strip() == query_digits: return True
    contract = _u_pick(lead, "contract_number", "number_contract", "contract",
                       "nomer_dogovora", "dogovor", "n_dogovor")
    if contract and query_digits in str(contract): return True
    return False

def _u_lead_tourists(lead):
    for k in ("tourists", "tourist", "u_tourists", "u_tourist",
              "travellers", "traveler", "passengers", "members", "participants"):
        v = lead.get(k)
        if v: return _u_as_list(v)
    return []

def _u_parse_date(s):
    if not s: return None
    s = str(s).strip()[:10]
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y"):
        try: return datetime.datetime.strptime(s, fmt).date()
        except ValueError: pass
    return None

def _u_lead_fio(lead):
    surname = _u_pick(lead, "client_surname", "surname", "u_surname")
    name = _u_pick(lead, "client_name", "name", "u_name")
    initial = (str(name)[:1] + ".") if name else ""
    return f"{surname} {initial}".strip()

def _u_fmt_money(v):
    if v in (None, "", 0, "0", "0.00"): return ""
    try:
        f = float(str(v).replace(",", ".").replace(" ", ""))
        if f == int(f): return f"{int(f):,}".replace(",", " ")
        return f"{f:,.2f}".replace(",", " ").replace(".", ",")
    except Exception:
        return str(v)

async def _u_list_clients_matching(s, phrase_lower, qtype="name"):
    found = []
    last_size = None
    for page in range(1, UON_SCAN_PAGES + 1):
        data = await _uon_get(s, f"users/{page}.json")
        if data is None: break
        items = _u_as_list(data.get("users") or data.get("clients") or data.get("user"))
        if not items: break
        for u in items:
            if _u_person_matches(u, phrase_lower, qtype=qtype): found.append(u)
        if last_size is not None and len(items) < last_size: break
        last_size = len(items)
    return found

async def _u_paged_leads(s, base_path, max_pages=None):
    leads = []
    last_size = None
    limit = max_pages or UON_SCAN_PAGES
    for page in range(1, limit + 1):
        data = await _uon_get(s, f"{base_path}/{page}.json")
        if data is None: break
        page_leads = _u_as_list(data.get("leads") or data.get("lead") or
                                data.get("requests") or data.get("request"))
        if not page_leads: break
        leads.extend(page_leads)
        if last_size is not None and len(page_leads) < last_size: break
        last_size = len(page_leads)
    return leads

async def _u_scan_for_phrase(s, phrase_lower, qtype="name"):
    query_digits = _u_digits(phrase_lower) if qtype == "id" else ""
    max_pages = UON_SCAN_PAGES_DEEP if qtype in ("id", "phone", "operator_num") else UON_SCAN_PAGES
    found = []
    base_path = None
    for candidate in ("lead", "request"):
        test = await _uon_get(s, f"{candidate}/1.json")
        if test is not None:
            items = _u_as_list(test.get("leads") or test.get("lead")
                               or test.get("requests") or test.get("request"))
            if items: base_path = candidate; break
    if base_path is None: return found
    last_size = None
    for page in range(1, max_pages + 1):
        data = await _uon_get(s, f"{base_path}/{page}.json")
        if data is None: break
        leads = _u_as_list(data.get("leads") or data.get("lead") or
                           data.get("requests") or data.get("request"))
        if not leads: break
        for ld in leads:
            matched_tourist = None
            if qtype == "id":
                if not _u_lead_id_matches(ld, query_digits): continue
            elif qtype == "operator_num":
                if not _u_operator_num_matches(ld, phrase_lower): continue
            elif _u_person_matches(ld, phrase_lower, qtype=qtype):
                pass
            else:
                tourists = _u_lead_tourists(ld)
                for t in tourists:
                    if _u_person_matches(t, phrase_lower, qtype=qtype):
                        matched_tourist = t; break
                if matched_tourist is None: continue
            if matched_tourist is not None:
                ld["_matched_tourist"] = matched_tourist
            found.append(ld)
        if last_size is not None and len(leads) < last_size: break
        last_size = len(leads)
    return found

async def _u_get_lead(lead_id):
    async with aiohttp.ClientSession() as s:
        for path in (f"lead/{lead_id}.json", f"request/{lead_id}.json"):
            data = await _uon_get(s, path)
            if not data: continue
            lead = data.get("lead") or data.get("request") or data.get("requests")
            if isinstance(lead, list): return lead[0] if lead else None
            if isinstance(lead, dict): return lead
    return None

def fmt_lead_card_vk(lead: dict) -> str:
    """Карточка заявки в plain text для VK (без HTML, с URL прямой строкой)."""
    lid = _u_pick(lead, "id", "lead_id", "r_id")
    fio = " ".join(filter(None, [
        _u_pick(lead, "client_surname", "surname", "u_surname", "fam"),
        _u_pick(lead, "client_name", "name", "u_name", "imya"),
        _u_pick(lead, "client_middle_name", "middle_name", "u_middle_name", "otch"),
    ])).strip()
    contract_num = _u_pick(lead, "contract_number", "number_contract", "contract",
                           "nomer_dogovora", "dogovor", "n_dogovor")
    contract_date = _u_pick(lead, "contract_date", "date_contract", "data_dogovora")
    operator = _u_pick(lead, "tour_operator", "operator", "touroperator",
                       "tour_operator_name", "operator_name", "to_name")
    operator_num = _u_pick(lead, "reservation_number", "tour_operator_number",
                           "touroperator_number", "tour_number", "number_tour",
                           "tour_op_number", "nomer_zayavki", "zayavka_to",
                           "to_number", "n_zayavka")
    country = _u_pick(lead, "country", "country_name", "name_country", "strana")
    region = _u_pick(lead, "region", "region_name", "kurort", "city")
    date_from = _u_pick(lead, "from_date", "date_from", "departure", "dat_begin",
                        "data_zaezda", "datebegin", "date_begin")
    date_to = _u_pick(lead, "to_date", "date_to", "return", "dat_end",
                      "data_viezda", "dateend", "date_end")
    nights = _u_pick(lead, "nights", "nochej", "nochey", "n_nights")
    cost = _u_pick(lead, "cost", "price", "sum", "summa", "total", "total_cost",
                   "tour_cost", "cost_total")
    paid = _u_pick(lead, "pay", "paid", "payed", "clean_pay",
                   "summa_oplacheno", "oplacheno", "summa_pay")
    remainder = _u_pick(lead, "remainder", "debt", "balance", "ostatok",
                        "summa_dolg", "dolg", "doplata", "to_pay")
    if not remainder and cost and paid:
        try: remainder = float(str(cost).replace(",", ".")) - float(str(paid).replace(",", "."))
        except Exception: pass
    status = _u_pick(lead, "status_name", "status", "name_status", "status_text")

    def _fmt_d(v):
        d = _u_parse_date(v)
        return d.strftime("%d.%m.%Y") if d else str(v or "")

    mt = lead.get("_matched_tourist") if isinstance(lead, dict) else None
    matched_tourist_fio = ""
    if isinstance(mt, dict):
        matched_tourist_fio = " ".join(filter(None, [
            _u_pick(mt, "u_surname", "surname", "last_name"),
            _u_pick(mt, "u_name", "name", "first_name"),
        ])).strip()

    lines = [f"Заявка #{lid}" if lid else "Заявка"]
    if fio: lines.append(f"👤 {fio}")
    if matched_tourist_fio and matched_tourist_fio.lower() != fio.lower():
        lines.append(f"🧳 Турист: {matched_tourist_fio}")
    if contract_num:
        s_line = f"📄 Договор {contract_num}"
        if contract_date: s_line += f" от {contract_date}"
        lines.append(s_line)
    tour_parts = [str(p) for p in (country, region) if p]
    tour_str = ", ".join(tour_parts)
    if date_from or date_to:
        tour_str += f" • {_fmt_d(date_from)}"
        if date_to: tour_str += f" — {_fmt_d(date_to)}"
    if nights: tour_str += f" ({nights} н)"
    tour_str = tour_str.strip(", •")
    if tour_str: lines.append(f"✈️ {tour_str}")
    if operator:
        s_line = f"🏢 {operator}"
        if operator_num: s_line += f" / бронь {operator_num}"
        lines.append(s_line)
    if cost: lines.append(f"💰 Сумма: {_u_fmt_money(cost)} руб")
    if paid: lines.append(f"✅ Оплачено: {_u_fmt_money(paid)} руб")
    if remainder and _u_fmt_money(remainder):
        lines.append(f"❗ Остаток: {_u_fmt_money(remainder)} руб")
    if status: lines.append(f"📌 Статус: {status}")
    url = uon_lead_url(lid)
    if url: lines.append(f"🔗 {url}")
    return "\n".join(lines)

async def uon_search(query: str) -> list:
    """Универсальный поиск — авто-классификация запроса. Возвращает список заявок."""
    qtype = _u_classify(query)
    phrase_lower = query.lower().strip()
    all_leads = []
    seen = set()

    async with aiohttp.ClientSession() as s:
        if qtype == "operator_num":
            data = await _uon_post(s, "request/search.json", {"reservation_number": query})
            if data:
                items = _u_as_list(data.get("requests") or data.get("request")
                                   or data.get("leads") or data.get("lead"))
                for ld in items:
                    lid = str(_u_pick(ld, "id", "lead_id", "r_id") or "")
                    if lid and lid not in seen:
                        seen.add(lid); all_leads.append(ld)

        if qtype == "id":
            qd = _u_digits(query)
            for param in ("r_id", "id", "number", "lead_number"):
                data = await _uon_post(s, "request/search.json", {param: qd})
                if data:
                    items = _u_as_list(data.get("requests") or data.get("request")
                                       or data.get("leads") or data.get("lead"))
                    for ld in items:
                        lid = str(_u_pick(ld, "id", "lead_id", "r_id") or "")
                        if lid != qd: continue
                        if lid not in seen:
                            seen.add(lid); all_leads.append(ld)
                    if all_leads: break
            if not all_leads:
                direct = await _u_get_lead(qd)
                if direct:
                    lid = str(_u_pick(direct, "id", "lead_id", "r_id") or "")
                    if lid == qd or not lid:
                        all_leads.append(direct)
                        if lid: seen.add(lid)
            if not all_leads:
                scanned = await _u_scan_for_phrase(s, qd, qtype="id")
                for ld in scanned:
                    lid = str(_u_pick(ld, "id", "lead_id", "r_id") or "")
                    if lid == qd and lid not in seen:
                        seen.add(lid); all_leads.append(ld)

        if qtype == "name":
            clients = await _u_list_clients_matching(s, phrase_lower, qtype="name")
            for u in clients[:10]:
                c_id = _u_pick(u, "id", "u_id", "user_id")
                if not c_id: continue
                leads = await _u_paged_leads(s, f"request-by-client/{c_id}")
                for ld in leads:
                    lid = str(_u_pick(ld, "id", "lead_id") or "")
                    if lid and lid in seen: continue
                    if lid: seen.add(lid)
                    ld["_client"] = u
                    all_leads.append(ld)
            # Поиск туристов по фамилии
            parts = query.strip().split()
            surname = parts[0] if parts else query
            payload = {"u_surname": surname}
            if len(parts) >= 2: payload["u_name"] = parts[1]
            data = await _uon_post(s, "user/search.json", payload)
            tourists_found = _u_as_list(data.get("users") if data else None)
            for u in tourists_found[:10]:
                c_id = _u_pick(u, "id", "u_id", "user_id")
                if not c_id: continue
                leads = await _u_paged_leads(s, f"request-by-client/{c_id}")
                for ld in leads:
                    lid = str(_u_pick(ld, "id", "lead_id") or "")
                    if lid and lid in seen: continue
                    if lid: seen.add(lid)
                    ld["_matched_tourist"] = u
                    all_leads.append(ld)

        if qtype == "phone":
            norm = _u_normalize_phone(query)
            data = await _uon_get(s, f"user/phone/{norm}.json")
            users = _u_as_list(data.get("users") if data else None)
            data2 = await _uon_post(s, "request/search.json", {"client_phone": query})
            if data2:
                items = _u_as_list(data2.get("requests") or data2.get("request")
                                   or data2.get("leads") or data2.get("lead"))
                for ld in items:
                    if not _u_person_matches(ld, phrase_lower, qtype="phone"):
                        tourists = _u_lead_tourists(ld)
                        if not any(_u_person_matches(t, phrase_lower, qtype="phone") for t in tourists):
                            continue
                    lid = str(_u_pick(ld, "id", "lead_id", "r_id") or "")
                    if lid and lid not in seen:
                        seen.add(lid); all_leads.append(ld)
            for u in users[:10]:
                c_id = _u_pick(u, "id", "u_id", "user_id")
                if not c_id: continue
                leads = await _u_paged_leads(s, f"request-by-client/{c_id}")
                for ld in leads:
                    lid = str(_u_pick(ld, "id", "lead_id") or "")
                    if lid and lid in seen: continue
                    if lid: seen.add(lid)
                    all_leads.append(ld)

    return all_leads

async def _u_fetch_all_leads(max_pages=60):
    leads = []
    async with aiohttp.ClientSession() as s:
        base_path = None
        for candidate in ("lead", "request"):
            data = await _uon_get(s, f"{candidate}/1.json")
            if data:
                items = _u_as_list(data.get("leads") or data.get("lead") or
                                   data.get("requests") or data.get("request"))
                if items: base_path = candidate; break
        if not base_path: return leads
        last_size = None
        for page in range(1, max_pages + 1):
            data = await _uon_get(s, f"{base_path}/{page}.json")
            if not data: break
            page_leads = _u_as_list(data.get("leads") or data.get("lead") or
                                    data.get("requests") or data.get("request"))
            if not page_leads: break
            leads.extend(page_leads)
            if last_size is not None and len(page_leads) < last_size: break
            last_size = len(page_leads)
    return leads

def _u_days_label(n):
    if n == 0: return "сегодня"
    if n == 1: return "завтра"
    w = ("день" if n % 10 == 1 and n % 100 != 11 else
         "дня" if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else "дней")
    return f"через {n} {w}"

async def uon_digest_departures():
    if not uon_configured(): return "U-ON не настроен."
    today = datetime.date.today()
    tomorrow = today + datetime.timedelta(days=1)
    buckets = {today: [], tomorrow: []}
    leads = await _u_fetch_all_leads()
    for ld in leads:
        dep = _u_parse_date(_u_pick(ld, "date_begin", "from_date", "date_from",
                                    "departure", "dat_begin", "datebegin"))
        if dep not in buckets: continue
        fio = _u_lead_fio(ld)
        country = _u_pick(ld, "country", "country_name", "name_country") or ""
        lid = _u_pick(ld, "id", "lead_id", "r_id")
        ref = f"#{lid}" if lid else ""
        buckets[dep].append(f"• {ref} {fio} — {country}".rstrip(" —").strip())
    if not any(buckets.values()):
        return "✈️ Вылетов сегодня и завтра не найдено."
    parts = []
    for d, lines in buckets.items():
        if lines:
            label = "сегодня" if d == today else "завтра"
            parts.append(f"✈️ Вылеты {label} ({d.strftime('%d.%m.%Y')})\n" + "\n".join(lines))
    return "\n\n".join(parts)

async def uon_digest_returns():
    if not uon_configured(): return "U-ON не настроен."
    today = datetime.date.today()
    tomorrow = today + datetime.timedelta(days=1)
    buckets = {today: [], tomorrow: []}
    leads = await _u_fetch_all_leads()
    for ld in leads:
        ret = _u_parse_date(_u_pick(ld, "to_date", "date_to", "return", "dat_end",
                                    "data_viezda", "dateend", "date_end"))
        if ret not in buckets: continue
        fio = _u_lead_fio(ld)
        country = _u_pick(ld, "country", "country_name") or ""
        lid = _u_pick(ld, "id", "lead_id", "r_id")
        ref = f"#{lid}" if lid else ""
        buckets[ret].append(f"• {ref} {fio} — {country}".rstrip(" —").strip())
    if not any(buckets.values()):
        return "🛬 Возвращений сегодня и завтра не найдено."
    parts = []
    for d, lines in buckets.items():
        if lines:
            label = "сегодня" if d == today else "завтра"
            parts.append(f"🛬 Возвращения {label} ({d.strftime('%d.%m.%Y')})\n" + "\n".join(lines))
    return "\n\n".join(parts)

async def uon_digest_deadlines(days_ahead=14):
    if not uon_configured(): return "U-ON не настроен."
    today = datetime.date.today()
    horizon = today + datetime.timedelta(days=days_ahead)
    deadlines = []
    leads = await _u_fetch_all_leads()
    for ld in leads:
        pay_date = _u_parse_date(_u_pick(ld,
            "payment_deadline_partner", "payment_deadline_to",
            "to_pay_date", "operator_pay_date", "date_pay_to",
            "pay_date_to", "deadline_to", "r_pay_date_to", "pay_to_deadline"))
        if pay_date and today <= pay_date <= horizon:
            deadlines.append((pay_date, ld))
    if not deadlines:
        return f"📊 Дедлайнов оплаты ТО на ближайшие {days_ahead} дней не найдено."
    deadlines.sort(key=lambda x: x[0])
    lines = [f"📊 Дедлайны оплаты ТО (ближайшие {days_ahead} дней)"]
    for pay_date, ld in deadlines:
        fio = _u_lead_fio(ld)
        operator = _u_pick(ld, "tour_operator", "operator", "touroperator") or ""
        delta = (pay_date - today).days
        urgency = "❗ " if delta <= 3 else ("⚠️ " if delta <= 7 else "• ")
        lid = _u_pick(ld, "id", "lead_id", "r_id")
        ref = f"#{lid}" if lid else ""
        lines.append(
            f"{urgency}{ref} {fio} — до {pay_date.strftime('%d.%m')} "
            f"({_u_days_label(delta)}) {operator}".strip()
        )
    return "\n".join(lines)

async def uon_digest_bookings(days=1):
    if not uon_configured(): return "U-ON не настроен."
    today = datetime.date.today()
    since = today - datetime.timedelta(days=days - 1)
    found = []
    leads = await _u_fetch_all_leads(max_pages=10)
    for ld in leads:
        created = _u_parse_date(_u_pick(ld, "created", "date_create", "create_date",
                                        "r_dat_create", "dat_create"))
        if created and created >= since:
            found.append((created, ld))
    label = "сегодня" if days == 1 else f"за {days} дн."
    if not found:
        return f"📋 Новых заявок {label} не найдено."
    found.sort(key=lambda x: x[0], reverse=True)
    lines = [f"📋 Заявки {label} ({len(found)} шт.)"]
    for created, ld in found[:30]:
        fio = _u_lead_fio(ld)
        country = _u_pick(ld, "country", "country_name") or ""
        lid = _u_pick(ld, "id", "lead_id", "r_id")
        ref = f"#{lid}" if lid else ""
        lines.append(f"• {ref} {fio} {country} — {created.strftime('%d.%m')}".rstrip(" —").strip())
    return "\n".join(lines)


# ─── VK-хендлеры U-ON ──────────────────────────────────────────────

@bot.on.message(text="🔎 U-ON")
async def go_uon(msg: Message):
    if not allowed(msg.from_id): return
    clear(msg.from_id)
    if not uon_configured():
        await send(msg.peer_id,
                   "U-ON не настроен. Добавь в Railway → Variables: "
                   "UON_API_KEY и UON_ACCOUNT_ID.",
                   kb_orders())
        return
    await send(msg.peer_id, "U-ON: что показать?", kb_uon())

@bot.on.message(text="◀ Заказы")
async def back_orders(msg: Message):
    if not allowed(msg.from_id): return
    clear(msg.from_id)
    await send(msg.peer_id, "Туристы:", kb_orders())

@bot.on.message(text="🔎 Поиск заявки")
async def uon_start_search(msg: Message):
    uid = msg.from_id
    if not allowed(uid): return
    if not uon_configured():
        await send(msg.peer_id, "U-ON не настроен.", kb_orders()); return
    clear(uid)
    states[uid] = {"sec": "uon", "step": "uon_query"}
    await send(msg.peer_id,
               "Введи ФИО, телефон, номер заявки или номер брони ТО — "
               "сама пойму что это:",
               kb_cancel())

@bot.on.message(text="📋 Брони сегодня")
async def uon_bookings_today(msg: Message):
    if not allowed(msg.from_id): return
    await send(msg.peer_id, "⏳ Считаю заявки за сегодня…")
    try:
        text = await uon_digest_bookings(days=1)
    except Exception as e:
        text = f"Ошибка: {e}"
    await send(msg.peer_id, text, kb_uon())

@bot.on.message(text="✈️ Вылеты")
async def uon_departures(msg: Message):
    if not allowed(msg.from_id): return
    await send(msg.peer_id, "⏳ Собираю вылеты…")
    try:
        text = await uon_digest_departures()
    except Exception as e:
        text = f"Ошибка: {e}"
    await send(msg.peer_id, text, kb_uon())

@bot.on.message(text="🛬 Возвращения")
async def uon_returns(msg: Message):
    if not allowed(msg.from_id): return
    await send(msg.peer_id, "⏳ Собираю возвращения…")
    try:
        text = await uon_digest_returns()
    except Exception as e:
        text = f"Ошибка: {e}"
    await send(msg.peer_id, text, kb_uon())

@bot.on.message(text="📊 Дедлайны ТО")
async def uon_deadlines(msg: Message):
    if not allowed(msg.from_id): return
    await send(msg.peer_id, "⏳ Собираю дедлайны ТО на 14 дней…")
    try:
        text = await uon_digest_deadlines(days_ahead=14)
    except Exception as e:
        text = f"Ошибка: {e}"
    await send(msg.peer_id, text, kb_uon())


# ══════════════════════════════════════════════════════════════════
# КУРС ВАЛЮТ (ЦБ РФ)
# ══════════════════════════════════════════════════════════════════

# Курсы туроператоров и ЦБ — все из currency_module (один в один как в TG-боте).
import currency_module as _curr

async def fetch_cbr_rates():
    """Курсы ЦБ РФ на сегодня. Источник — публичный JSON cbr-xml-daily.ru."""
    url = "https://www.cbr-xml-daily.ru/daily_json.js"
    async with aiohttp.ClientSession() as s:
        async with s.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
            return await r.json(content_type=None)

@bot.on.message(text="💱 Курс валют")
async def go_currency_menu(msg: Message):
    """Меню источников курса валют."""
    if not allowed(msg.from_id): return
    await send(msg.peer_id, "💱 Курс какого ТО?", kb_currency())

@bot.on.message(text="📊 Tour-kassa (сегодня)")
async def go_currency_tk_today(msg: Message):
    if not allowed(msg.from_id): return
    await send(msg.peer_id, "⏳ Тяну сводную таблицу tour-kassa…")
    try:
        text = await _curr.fetch_tour_kassa_rates(tomorrow=False)
    except Exception as e:
        text = f"Ошибка: {e}"
    await send(msg.peer_id, text, kb_currency())

@bot.on.message(text="📊 Tour-kassa (завтра)")
async def go_currency_tk_tomorrow(msg: Message):
    if not allowed(msg.from_id): return
    await send(msg.peer_id, "⏳ Тяну сводную таблицу tour-kassa…")
    try:
        text = await _curr.fetch_tour_kassa_rates(tomorrow=True)
    except Exception as e:
        text = f"Ошибка: {e}"
    await send(msg.peer_id, text, kb_currency())

@bot.on.message(text="🛳 CruClub")
async def go_currency_cruclub(msg: Message):
    if not allowed(msg.from_id): return
    await send(msg.peer_id, "⏳ Тяну курс CruClub…")
    try:
        text = await _curr.fetch_cruclub_rates()
    except Exception as e:
        text = f"Ошибка: {e}"
    await send(msg.peer_id, text, kb_currency())

@bot.on.message(text="✈️ PAC Group")
async def go_currency_pac(msg: Message):
    if not allowed(msg.from_id): return
    await send(msg.peer_id, "⏳ Тяну курс PAC Group…")
    try:
        text = await _curr.fetch_pac_rates()
    except Exception as e:
        text = f"Ошибка: {e}"
    await send(msg.peer_id, text, kb_currency())

@bot.on.message(text="🚢 Ла Вояж")
async def go_currency_lavoyage(msg: Message):
    if not allowed(msg.from_id): return
    await send(msg.peer_id, "⏳ Тяну курс Ла Вояж…")
    try:
        text = await _curr.fetch_lavoyage_rates()
    except Exception as e:
        text = f"Ошибка: {e}"
    await send(msg.peer_id, text, kb_currency())


# ══════════════════════════════════════════════════════════════════
# NOTION
# ══════════════════════════════════════════════════════════════════

async def create_notion_page(title, blocks):
    if not NOTION_TOKEN or not NOTION_PARENT:
        return None
    headers = {"Authorization": f"Bearer {NOTION_TOKEN}",
               "Notion-Version": "2022-06-28", "Content-Type": "application/json"}
    body = {"parent": {"page_id": NOTION_PARENT},
            "properties": {"title": {"title": [{"text": {"content": title}}]}},
            "children": blocks}
    async with aiohttp.ClientSession() as s:
        async with s.post("https://api.notion.com/v1/pages", json=body, headers=headers) as r:
            data = await r.json()
    return data.get("url")


# ══════════════════════════════════════════════════════════════════
# ЗАПУСК
# ══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    log.info("VK-бот запускается...")
    bot.run_forever()

