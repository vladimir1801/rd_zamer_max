import logging
import io
import os
import json
import re
import asyncio
import tempfile
import aiohttp

from PIL import Image, ImageDraw, ImageFont

from maxapi import Bot, Dispatcher, F
from maxapi.context import MemoryContext, State, StatesGroup
from maxapi.types import (
    MessageCreated,
    MessageCallback,
    BotStarted,
    BotAdded,
    Command,
    CommandStart,
    RequestContactButton,
    CallbackButton,
    InputMedia,
)
from maxapi.utils.inline_keyboard import InlineKeyboardBuilder

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

TOKEN = os.environ.get("TOKEN")
TARGET_CHAT_ID = int(os.environ.get("TARGET_CHAT_ID", "0"))  # ID рабочего чата в МАКС

ALLOWED_NUMBERS_FILE = "allowed_numbers.json"
try:
    with open(ALLOWED_NUMBERS_FILE, "r", encoding="utf-8") as f:
        ALLOWED_NUMBERS = json.load(f)
except Exception as e:
    logging.error("Ошибка загрузки базы номеров: %s", e)
    ALLOWED_NUMBERS = {}

FONT_PATH = "Montserrat-Regular.ttf"

bot = Bot(TOKEN)
dp = Dispatcher()


# ─── STATES ───────────────────────────────────────────────────────────────────

class AuthStates(StatesGroup):
    menu = State()
    waiting_contact = State()


class MeasureStates(StatesGroup):
    menu = State()
    get_name = State()
    get_phone = State()
    get_address = State()
    enter_room = State()
    enter_door_type = State()
    enter_door_type_custom = State()
    enter_dimensions = State()
    enter_canvas = State()
    enter_canvas_custom = State()
    enter_dobor = State()
    enter_dobor_custom = State()
    enter_dobor_count = State()
    enter_dobor_count_custom = State()
    enter_nalichniki = State()
    enter_nalichniki_custom = State()
    enter_threshold = State()
    enter_demontage = State()
    enter_opening = State()
    enter_opening_custom = State()
    enter_comment = State()
    enter_photos = State()
    opening_menu = State()
    check_measure = State()
    edit_choice = State()
    edit_field = State()
    edit_value = State()
    delete_choice = State()
    delete_confirm = State()


# ─── KEYBOARD HELPER ──────────────────────────────────────────────────────────

def kb(*rows, cancel: bool = True):
    """Build InlineKeyboardBuilder from rows of (text, payload) tuples."""
    builder = InlineKeyboardBuilder()
    for row in rows:
        builder.row(*[CallbackButton(text=t, payload=p) for t, p in row])
    if cancel:
        builder.row(CallbackButton(text="Отключить бот", payload="cancel"))
    return builder.as_markup()


# ─── IMAGE GENERATION ─────────────────────────────────────────────────────────

def generate_measurement_image(client_data: dict) -> str:
    col_widths = [50, 150, 200, 200, 100, 100, 110, 110, 80, 100, 120, 200]
    headers = [
        "№", "Комната", "Тип двери", "Размеры", "Полотно",
        "Добор", "Кол-во доборов", "Наличники",
        "Порог", "Демонтаж", "Открывание", "Комментарий"
    ]
    openings = client_data.get("openings", [])
    rows = [headers]
    for i, op in enumerate(openings, start=1):
        rows.append([
            str(i), op["room"], op["door_type"], op["dimensions"],
            op["canvas"], op["dobor"], op["dobor_count"], op["nalichniki"],
            op["threshold"], op["demontage"], op["opening"], op["comment"]
        ])
    client_info = (
        f"Имя: {client_data.get('client_name', '')}\n"
        f"Телефон: {client_data.get('client_phone', '')}\n"
        f"Адрес: {client_data.get('client_address', '')}\n"
    )
    try:
        font = ImageFont.truetype(FONT_PATH, 16)
    except Exception:
        font = ImageFont.load_default()

    tmp_img = Image.new("RGB", (10, 10))
    draw_tmp = ImageDraw.Draw(tmp_img)

    def get_text_size(text, fnt):
        l, t, r, b = draw_tmp.textbbox((0, 0), text, font=fnt)
        return r - l, b - t

    def wrap_text(text, max_width):
        words = text.split()
        if not words:
            return [""]
        lines, cur = [], words[0]
        for w in words[1:]:
            test = cur + " " + w
            if get_text_size(test, font)[0] <= max_width:
                cur = test
            else:
                lines.append(cur)
                cur = w
        lines.append(cur)
        return lines

    cell_pad, line_sp = 10, 5
    row_lines, row_heights = [], []
    for row_data in rows:
        max_h = 0
        row_lines.append([])
        for col_idx, cell_text in enumerate(row_data):
            lines = wrap_text(str(cell_text), col_widths[col_idx] - 2 * cell_pad)
            _, lh = get_text_size("A", font)
            cell_h = len(lines) * (lh + line_sp) + 3 * cell_pad
            max_h = max(max_h, cell_h)
            row_lines[-1].append(lines)
        row_heights.append(max_h)

    margin = 50
    table_w = sum(col_widths) + margin * 2
    _, lh = get_text_size("A", font)
    info_h = lh * len(client_info.strip().split("\n")) + 40

    logo = None
    logo_w, logo_h = 0, 0
    try:
        logo = Image.open("Logo_rusdver.png").convert("RGBA")
        logo.thumbnail((150, 9999))
        logo_w, logo_h = logo.size
    except Exception:
        pass

    top_h = max(info_h, logo_h) + 20
    total_h = top_h + sum(row_heights) + margin * 2

    img = Image.new("RGB", (table_w, total_h), color="white")
    draw = ImageDraw.Draw(img)
    draw.text((margin, 20), client_info, font=font, fill="black")
    if logo:
        img.paste(logo, (table_w - margin - logo_w, 20), logo)

    y = top_h
    for ridx, row_data in enumerate(rows):
        rh = row_heights[ridx]
        x = margin
        for cidx in range(len(row_data)):
            cw = col_widths[cidx]
            draw.rectangle([x, y, x + cw, y + rh], outline="black", width=1)
            ty = y + cell_pad
            for line in row_lines[ridx][cidx]:
                draw.text((x + cell_pad, ty), line, font=font, fill="black")
                ty += get_text_size("A", font)[1] + line_sp
            x += cw
        y += rh

    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    img.save(tmp.name, "PNG")
    tmp.close()
    return tmp.name


# ─── PHOTO HELPERS ────────────────────────────────────────────────────────────

def get_photo_url(attachments) -> str | None:
    """Extract photo download URL from MAX message attachments.
    NOTE: field names (att.type, att.payload.url) may need adjustment
    after testing against the real MAX API response structure."""
    if not attachments:
        return None
    for att in attachments:
        att_type = getattr(att, 'type', None)
        if att_type in ('image', 'photo'):
            payload = getattr(att, 'payload', None)
            if payload:
                return getattr(payload, 'url', None) or getattr(payload, 'token', None)
    return None


def get_contact_phone(attachments) -> str | None:
    """Extract phone number from MAX contact attachment."""
    if not attachments:
        return None
    for att in attachments:
        att_type = getattr(att, 'type', None)
        logging.info("CONTACT_DEBUG attachment type=%s | repr=%s", att_type, repr(att))
        # MAX API может вернуть тип 'contact' или 'share' — пробуем оба
        if att_type in ('contact', 'share', None):
            payload = getattr(att, 'payload', None)
            if payload:
                logging.info("CONTACT_DEBUG payload attrs=%s", [a for a in dir(payload) if not a.startswith('_')])
                # Прямое поле phone
                phone = getattr(payload, 'phone', None)
                if phone:
                    return phone
                # Поле vcf_info — полная VCF-карточка, нужно вытащить TEL
                vcf = getattr(payload, 'vcf_info', None)
                if vcf:
                    logging.info("CONTACT_DEBUG vcf_info=%s", vcf)
                    m = re.search(r'TEL[^:]*:([+\d\s\-()]+)', vcf)
                    if m:
                        return m.group(1).strip()
                # Другие возможные поля
                for field in ('contact_id', 'user_id', 'phone_number'):
                    val = getattr(payload, field, None)
                    if val:
                        return str(val)
    return None


async def download_to_temp(url: str, suffix: str = ".jpg") -> str:
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            data = await resp.read()
    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    tmp.write(data)
    tmp.close()
    return tmp.name


async def overlay_text_on_photo(photo_url: str, text: str) -> str:
    path = await download_to_temp(photo_url)
    img = Image.open(path).convert("RGBA")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype(FONT_PATH, 24)
    except Exception:
        font = ImageFont.load_default()
    tx, ty = 20, img.height - 60
    bbox = draw.textbbox((tx, ty), text, font=font)
    draw.rectangle([bbox[0]-10, bbox[1]-10, bbox[2]+10, bbox[3]+10], fill=(0, 0, 0, 128))
    draw.text((tx, ty), text, fill=(255, 255, 255, 255), font=font)
    out = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    img.save(out.name, "PNG")
    out.close()
    os.remove(path)
    return out.name


# ─── CANCEL (global, registered first — catches any state) ────────────────────

@dp.message_callback(F.callback.payload == 'cancel')
async def on_cancel(event: MessageCallback, context: MemoryContext):
    await context.clear()
    await event.message.answer("Диалог отменён. Напишите /start для повторного запуска.")


# ─── ENTRY POINTS ─────────────────────────────────────────────────────────────

@dp.bot_added()
async def bot_added(event: BotAdded):
    await bot.send_message(
        chat_id=event.chat_id,
        text=f"Бот добавлен в чат!\nChat ID этого чата: {event.chat_id}\n\nСкопируйте это число и передайте разработчику для настройки бота."
    )


@dp.bot_started()
async def bot_started(event: BotStarted):
    await bot.send_message(
        chat_id=event.chat_id,
        text="Добро пожаловать! Для использования бота нажмите «Запустить».",
        attachments=[kb([("Запустить", "launch")], cancel=False)]
    )


@dp.message_created(CommandStart())
async def cmd_start(event: MessageCreated, context: MemoryContext):
    await context.clear()
    await event.message.answer(
        text="Добро пожаловать! Для использования бота нажмите «Запустить».",
        attachments=[kb([("Запустить", "launch")], cancel=False)]
    )


# ─── AUTH ─────────────────────────────────────────────────────────────────────

@dp.message_callback(F.callback.payload == 'launch')
async def on_launch(event: MessageCallback, context: MemoryContext):
    await context.set_state(AuthStates.waiting_contact)
    builder = InlineKeyboardBuilder()
    builder.row(RequestContactButton(text="Поделиться контактом"))
    builder.row(CallbackButton(text="Отключить бот", payload="cancel"))
    await event.message.answer(
        text="Для авторизации поделитесь своим контактом.",
        attachments=[builder.as_markup()]
    )


@dp.message_created(AuthStates.waiting_contact)
async def handle_contact(event: MessageCreated, context: MemoryContext):
    attachments = getattr(event.message.body, 'attachments', None) or []
    logging.info("CONTACT_DEBUG total attachments=%d | body attrs=%s",
                 len(attachments),
                 [a for a in dir(event.message.body) if not a.startswith('_')])
    phone_raw = get_contact_phone(attachments)

    if not phone_raw:
        await event.message.answer("Пожалуйста, нажмите кнопку «Поделиться контактом».")
        return

    # Оставляем только цифры и нормализуем: 8XXXXXXXXXX → 7XXXXXXXXXX
    phone = re.sub(r"\D", "", phone_raw)
    if phone.startswith("8") and len(phone) == 11:
        phone = "7" + phone[1:]
    logging.info("CONTACT_DEBUG phone_raw=%s | phone_normalized=%s | in_db=%s",
                 phone_raw, phone, phone in ALLOWED_NUMBERS)
    if phone in ALLOWED_NUMBERS:
        name = ALLOWED_NUMBERS[phone]
        await context.clear()
        await context.update_data(authorized_name=name)
        await context.set_state(MeasureStates.menu)
        await event.message.answer(
            text=f"Здравствуйте, {name}! Нажмите «Новый замер» для начала.",
            attachments=[kb([("Новый замер", "new_measure")])]
        )
    else:
        await context.clear()
        await event.message.answer("Извините, ваш номер не найден в базе. Доступ закрыт.")


# ─── MENU ─────────────────────────────────────────────────────────────────────

@dp.message_callback(F.callback.payload == 'new_measure', MeasureStates.menu)
async def on_new_measure(event: MessageCallback, context: MemoryContext):
    data = await context.get_data()
    await context.update_data(
        openings=[], client_name=None, client_phone=None, client_address=None
    )
    await context.set_state(MeasureStates.get_name)
    await event.message.answer("Введите имя клиента:")


# ─── CLIENT DATA ──────────────────────────────────────────────────────────────

@dp.message_created(F.message.body.text, MeasureStates.get_name)
async def get_name(event: MessageCreated, context: MemoryContext):
    await context.update_data(client_name=event.message.body.text)
    await context.set_state(MeasureStates.get_phone)
    await event.message.answer("Введите телефон клиента:")


@dp.message_created(F.message.body.text, MeasureStates.get_phone)
async def get_phone(event: MessageCreated, context: MemoryContext):
    await context.update_data(client_phone=event.message.body.text)
    await context.set_state(MeasureStates.get_address)
    await event.message.answer("Введите адрес клиента:")


@dp.message_created(F.message.body.text, MeasureStates.get_address)
async def get_address(event: MessageCreated, context: MemoryContext):
    await context.update_data(client_address=event.message.body.text)
    await start_opening_flow(event, context)


# ─── OPENING FLOW ─────────────────────────────────────────────────────────────

async def start_opening_flow(event, context: MemoryContext):
    current = {
        "room": "", "door_type": "", "dimensions": "",
        "canvas": "---", "dobor": "---", "dobor_count": "---",
        "nalichniki": "---", "threshold": "", "demontage": "",
        "opening": "---", "comment": "", "photos": []
    }
    await context.update_data(current_opening=current)
    await context.set_state(MeasureStates.enter_room)
    await event.message.answer("Введите название комнаты (например, «Кухня»):")


DOOR_TYPE_MAP = {
    "dt_inter":  "Межкомнатная дверь",
    "dt_hidden": "Скрытая дверь",
    "dt_entry":  "Входная дверь",
    "dt_oblag":  "Облагораживание проема",
    "dt_fold":   "Складная дверь (книжка)",
    "dt_slide1": "Раздвижная дверь (одностворчатая)",
    "dt_slide2": "Раздвижная дверь (двустворчатая)",
    "dt_double": "Двустворчатая дверь (распашная)",
}

NO_CANVAS_TYPES   = {"Облагораживание проема"}
NO_NALICHNIKI_TYPES = {"Скрытая дверь", "Входная дверь"}
NO_THRESHOLD_TYPES  = {"Облагораживание проема"}
NO_OPENING_TYPES    = {
    "Облагораживание проема", "Складная дверь (книжка)",
    "Раздвижная дверь (одностворчатая)", "Раздвижная дверь (двустворчатая)",
    "Двустворчатая дверь (распашная)"
}


@dp.message_created(F.message.body.text, MeasureStates.enter_room)
async def enter_room(event: MessageCreated, context: MemoryContext):
    data = await context.get_data()
    current = data["current_opening"]
    current["room"] = event.message.body.text
    await context.update_data(current_opening=current)
    await context.set_state(MeasureStates.enter_door_type)
    await event.message.answer(
        text="Выберите тип двери:",
        attachments=[kb(
            [("Межкомнатная дверь",               "dt_inter")],
            [("Скрытая дверь",                    "dt_hidden")],
            [("Входная дверь",                    "dt_entry")],
            [("Облагораживание проема",            "dt_oblag")],
            [("Складная дверь (книжка)",           "dt_fold")],
            [("Раздвижная дверь (одностворчатая)", "dt_slide1")],
            [("Раздвижная дверь (двустворчатая)",  "dt_slide2")],
            [("Двустворчатая дверь (распашная)",   "dt_double")],
            [("Иное",                             "dt_custom")],
        )]
    )


@dp.message_callback(MeasureStates.enter_door_type)
async def cb_door_type(event: MessageCallback, context: MemoryContext):
    p = event.callback.payload
    if p == "dt_custom":
        await context.set_state(MeasureStates.enter_door_type_custom)
        await event.message.answer("Введите ваш вариант типа двери:")
        return
    data = await context.get_data()
    current = data["current_opening"]
    current["door_type"] = DOOR_TYPE_MAP.get(p, p)
    await context.update_data(current_opening=current)
    await ask_dimensions(event, context)


@dp.message_created(F.message.body.text, MeasureStates.enter_door_type_custom)
async def enter_door_type_custom(event: MessageCreated, context: MemoryContext):
    data = await context.get_data()
    current = data["current_opening"]
    current["door_type"] = event.message.body.text
    await context.update_data(current_opening=current)
    await ask_dimensions(event, context)


async def ask_dimensions(event, context: MemoryContext):
    await context.set_state(MeasureStates.enter_dimensions)
    await event.message.answer("Введите размеры проёма (высота, ширина, толщина стены):")


@dp.message_created(F.message.body.text, MeasureStates.enter_dimensions)
async def enter_dimensions(event: MessageCreated, context: MemoryContext):
    data = await context.get_data()
    current = data["current_opening"]
    current["dimensions"] = event.message.body.text
    await context.update_data(current_opening=current)
    if current["door_type"] in NO_CANVAS_TYPES:
        current["canvas"] = "---"
        await context.update_data(current_opening=current)
        await ask_dobor(event, context)
    else:
        await context.set_state(MeasureStates.enter_canvas)
        await event.message.answer(
            text="Введите рекомендуемое полотно:",
            attachments=[kb(
                [("600", "cv_600"), ("700", "cv_700"), ("800", "cv_800")],
                [("Иное", "cv_custom")],
            )]
        )


@dp.message_callback(MeasureStates.enter_canvas)
async def cb_canvas(event: MessageCallback, context: MemoryContext):
    p = event.callback.payload
    if p == "cv_custom":
        await context.set_state(MeasureStates.enter_canvas_custom)
        await event.message.answer("Введите ваш вариант полотна:")
        return
    canvas_map = {"cv_600": "600", "cv_700": "700", "cv_800": "800"}
    data = await context.get_data()
    current = data["current_opening"]
    current["canvas"] = canvas_map.get(p, p)
    await context.update_data(current_opening=current)
    await ask_dobor(event, context)


@dp.message_created(F.message.body.text, MeasureStates.enter_canvas_custom)
async def enter_canvas_custom(event: MessageCreated, context: MemoryContext):
    data = await context.get_data()
    current = data["current_opening"]
    current["canvas"] = event.message.body.text
    await context.update_data(current_opening=current)
    await ask_dobor(event, context)


async def ask_dobor(event, context: MemoryContext):
    await context.set_state(MeasureStates.enter_dobor)
    await event.message.answer(
        text="Введите ширину добора:",
        attachments=[kb(
            [("100 мм", "db_100"), ("150 мм", "db_150")],
            [("200 мм", "db_200"), ("нет",    "db_no")],
            [("Иное",   "db_custom")],
        )]
    )


@dp.message_callback(MeasureStates.enter_dobor)
async def cb_dobor(event: MessageCallback, context: MemoryContext):
    p = event.callback.payload
    if p == "db_custom":
        await context.set_state(MeasureStates.enter_dobor_custom)
        await event.message.answer("Введите ваш вариант добора:")
        return
    dobor_map = {"db_100": "100 мм", "db_150": "150 мм", "db_200": "200 мм", "db_no": "нет"}
    data = await context.get_data()
    current = data["current_opening"]
    current["dobor"] = dobor_map.get(p, p)
    await context.update_data(current_opening=current)
    if p == "db_no":
        current["dobor_count"] = "---"
        await context.update_data(current_opening=current)
        await ask_nalichniki(event, context)
    else:
        await ask_dobor_count(event, context)


@dp.message_created(F.message.body.text, MeasureStates.enter_dobor_custom)
async def enter_dobor_custom(event: MessageCreated, context: MemoryContext):
    data = await context.get_data()
    current = data["current_opening"]
    current["dobor"] = event.message.body.text
    await context.update_data(current_opening=current)
    await ask_dobor_count(event, context)


async def ask_dobor_count(event, context: MemoryContext):
    await context.set_state(MeasureStates.enter_dobor_count)
    await event.message.answer(
        text="Введите кол-во доборов:",
        attachments=[kb(
            [("1,5", "dc_1.5"), ("2,5", "dc_2.5"), ("3", "dc_3"), ("нет", "dc_no")],
            [("Иное", "dc_custom")],
        )]
    )


@dp.message_callback(MeasureStates.enter_dobor_count)
async def cb_dobor_count(event: MessageCallback, context: MemoryContext):
    p = event.callback.payload
    if p == "dc_custom":
        await context.set_state(MeasureStates.enter_dobor_count_custom)
        await event.message.answer("Введите ваш вариант кол-ва доборов:")
        return
    count_map = {"dc_1.5": "1,5", "dc_2.5": "2,5", "dc_3": "3", "dc_no": "нет"}
    data = await context.get_data()
    current = data["current_opening"]
    current["dobor_count"] = count_map.get(p, p)
    await context.update_data(current_opening=current)
    await ask_nalichniki(event, context)


@dp.message_created(F.message.body.text, MeasureStates.enter_dobor_count_custom)
async def enter_dobor_count_custom(event: MessageCreated, context: MemoryContext):
    data = await context.get_data()
    current = data["current_opening"]
    current["dobor_count"] = event.message.body.text
    await context.update_data(current_opening=current)
    await ask_nalichniki(event, context)


async def ask_nalichniki(event, context: MemoryContext):
    data = await context.get_data()
    current = data["current_opening"]
    if current["door_type"] in NO_NALICHNIKI_TYPES:
        current["nalichniki"] = "---"
        await context.update_data(current_opening=current)
        await ask_threshold(event, context)
        return
    await context.set_state(MeasureStates.enter_nalichniki)
    await event.message.answer(
        text="Введите кол-во наличников:",
        attachments=[kb(
            [("2,5", "nl_2.5")],
            [("5",   "nl_5")],
            [("5,5", "nl_5.5")],
            [("6",   "nl_6")],
            [("нет", "nl_no")],
            [("Иное","nl_custom")],
        )]
    )


@dp.message_callback(MeasureStates.enter_nalichniki)
async def cb_nalichniki(event: MessageCallback, context: MemoryContext):
    p = event.callback.payload
    if p == "nl_custom":
        await context.set_state(MeasureStates.enter_nalichniki_custom)
        await event.message.answer("Введите ваш вариант кол-ва наличников:")
        return
    nl_map = {"nl_2.5": "2,5", "nl_5": "5", "nl_5.5": "5,5", "nl_6": "6", "nl_no": "нет"}
    data = await context.get_data()
    current = data["current_opening"]
    current["nalichniki"] = nl_map.get(p, p)
    await context.update_data(current_opening=current)
    await ask_threshold(event, context)


@dp.message_created(F.message.body.text, MeasureStates.enter_nalichniki_custom)
async def enter_nalichniki_custom(event: MessageCreated, context: MemoryContext):
    data = await context.get_data()
    current = data["current_opening"]
    current["nalichniki"] = event.message.body.text
    await context.update_data(current_opening=current)
    await ask_threshold(event, context)


async def ask_threshold(event, context: MemoryContext):
    data = await context.get_data()
    current = data["current_opening"]
    if current["door_type"] in NO_THRESHOLD_TYPES:
        current["threshold"] = "---"
        await context.update_data(current_opening=current)
        await ask_demontage(event, context)
        return
    await context.set_state(MeasureStates.enter_threshold)
    await event.message.answer(
        text="Наличие порога?",
        attachments=[kb([("да", "th_yes"), ("нет", "th_no")])]
    )


@dp.message_callback(MeasureStates.enter_threshold)
async def cb_threshold(event: MessageCallback, context: MemoryContext):
    data = await context.get_data()
    current = data["current_opening"]
    current["threshold"] = {"th_yes": "да", "th_no": "нет"}.get(event.callback.payload, "")
    await context.update_data(current_opening=current)
    await ask_demontage(event, context)


async def ask_demontage(event, context: MemoryContext):
    await context.set_state(MeasureStates.enter_demontage)
    await event.message.answer(
        text="Демонтаж старой двери?",
        attachments=[kb([("да", "dm_yes"), ("нет", "dm_no")])]
    )


@dp.message_callback(MeasureStates.enter_demontage)
async def cb_demontage(event: MessageCallback, context: MemoryContext):
    data = await context.get_data()
    current = data["current_opening"]
    current["demontage"] = {"dm_yes": "да", "dm_no": "нет"}.get(event.callback.payload, "")
    await context.update_data(current_opening=current)
    await ask_opening(event, context)


async def ask_opening(event, context: MemoryContext):
    data = await context.get_data()
    current = data["current_opening"]
    if current["door_type"] in NO_OPENING_TYPES:
        current["opening"] = "---"
        await context.update_data(current_opening=current)
        await ask_comment(event, context)
        return
    await context.set_state(MeasureStates.enter_opening)
    await event.message.answer(
        text="Введите открывание:",
        attachments=[kb(
            [("Левое",      "op_left"),     ("Правое",      "op_right")],
            [("Левое рев.", "op_left_rev"), ("Правое рев.", "op_right_rev")],
            [("Иное",       "op_custom")],
        )]
    )


@dp.message_callback(MeasureStates.enter_opening)
async def cb_opening(event: MessageCallback, context: MemoryContext):
    p = event.callback.payload
    if p == "op_custom":
        await context.set_state(MeasureStates.enter_opening_custom)
        await event.message.answer("Введите ваш вариант открывания:")
        return
    op_map = {
        "op_left": "Левое", "op_right": "Правое",
        "op_left_rev": "Левое рев.", "op_right_rev": "Правое рев."
    }
    data = await context.get_data()
    current = data["current_opening"]
    current["opening"] = op_map.get(p, p)
    await context.update_data(current_opening=current)
    await ask_comment(event, context)


@dp.message_created(F.message.body.text, MeasureStates.enter_opening_custom)
async def enter_opening_custom(event: MessageCreated, context: MemoryContext):
    data = await context.get_data()
    current = data["current_opening"]
    current["opening"] = event.message.body.text
    await context.update_data(current_opening=current)
    await ask_comment(event, context)


async def ask_comment(event, context: MemoryContext):
    await context.set_state(MeasureStates.enter_comment)
    await event.message.answer(
        text="Введите комментарий или нажмите «Пропустить»:",
        attachments=[kb([("Пропустить", "skip_comment")])]
    )


@dp.message_callback(F.callback.payload == 'skip_comment', MeasureStates.enter_comment)
async def skip_comment(event: MessageCallback, context: MemoryContext):
    data = await context.get_data()
    current = data["current_opening"]
    current["comment"] = ""
    await context.update_data(current_opening=current)
    await ask_photos(event, context)


@dp.message_created(F.message.body.text, MeasureStates.enter_comment)
async def enter_comment(event: MessageCreated, context: MemoryContext):
    data = await context.get_data()
    current = data["current_opening"]
    current["comment"] = event.message.body.text
    await context.update_data(current_opening=current)
    await ask_photos(event, context)


async def ask_photos(event, context: MemoryContext):
    await context.set_state(MeasureStates.enter_photos)
    await event.message.answer(
        text="Прикрепите фото (по одному). Когда закончите — «Готово». Нет фото — «Пропустить».",
        attachments=[kb([("Готово", "photos_done"), ("Пропустить", "photos_skip")])]
    )


@dp.message_callback(MeasureStates.enter_photos)
async def cb_photos_done(event: MessageCallback, context: MemoryContext):
    p = event.callback.payload
    if p in ("photos_done", "photos_skip"):
        await save_opening_and_show_menu(event, context)


@dp.message_created(MeasureStates.enter_photos)
async def receive_photo(event: MessageCreated, context: MemoryContext):
    attachments = getattr(event.message.body, 'attachments', None) or []
    photo_url = get_photo_url(attachments)
    if photo_url:
        data = await context.get_data()
        current = data["current_opening"]
        current["photos"].append(photo_url)
        await context.update_data(current_opening=current)
        n = len(current["photos"])
        await event.message.answer(
            text=f"Фото сохранено ({n} шт.). Отправьте ещё или нажмите «Готово».",
            attachments=[kb([("Готово", "photos_done"), ("Пропустить", "photos_skip")])]
        )
    else:
        await event.message.answer(
            text="Отправьте фото, либо нажмите «Готово» или «Пропустить».",
            attachments=[kb([("Готово", "photos_done"), ("Пропустить", "photos_skip")])]
        )


async def save_opening_and_show_menu(event, context: MemoryContext):
    data = await context.get_data()
    openings = data.get("openings", [])
    openings.append(data["current_opening"])
    await context.update_data(openings=openings, current_opening=None)
    await show_opening_menu(event, context)


async def show_opening_menu(event, context: MemoryContext):
    await context.set_state(MeasureStates.opening_menu)
    await event.message.answer(
        text="Проём сохранён. Что делаем дальше?",
        attachments=[kb(
            [("Следующий проём",       "next_opening")],
            [("Редактировать проём",   "edit_opening"),
             ("Удалить проём",         "delete_opening")],
            [("Проверить и завершить", "check_finish")],
        )]
    )


# ─── OPENING MENU ─────────────────────────────────────────────────────────────

@dp.message_callback(MeasureStates.opening_menu)
async def cb_opening_menu(event: MessageCallback, context: MemoryContext):
    p = event.callback.payload
    if p == "next_opening":
        await start_opening_flow(event, context)
    elif p == "edit_opening":
        await show_edit_choice(event, context)
    elif p == "delete_opening":
        await show_delete_choice(event, context)
    elif p == "check_finish":
        await do_check_measure(event, context)


# ─── CHECK / FINISH ───────────────────────────────────────────────────────────

async def do_check_measure(event, context: MemoryContext):
    data = await context.get_data()
    name    = data.get("client_name", "")
    phone   = data.get("client_phone", "")
    address = data.get("client_address", "")
    openings = data.get("openings", [])
    client_data = {
        "client_name": name, "client_phone": phone, "client_address": address,
        "openings": [{**op, "photo": "есть" if op["photos"] else "нет"} for op in openings]
    }
    img_path = generate_measurement_image(client_data)
    caption = f"Имя: {name}\nТелефон: {phone}\nАдрес: {address}"
    await bot.send_message(
        chat_id=event.chat_id,
        text=caption,
        attachments=[InputMedia(path=img_path)]
    )
    os.remove(img_path)
    await context.set_state(MeasureStates.check_measure)
    await bot.send_message(
        chat_id=event.chat_id,
        text="Проверьте замер. Если всё правильно — «Завершить замер», иначе — «Редактировать замер».",
        attachments=[kb(
            [("Редактировать замер", "edit_measure")],
            [("Завершить замер",     "finish_measure")],
        )]
    )


@dp.message_callback(MeasureStates.check_measure)
async def cb_check_measure(event: MessageCallback, context: MemoryContext):
    p = event.callback.payload
    if p == "edit_measure":
        await show_edit_choice(event, context)
    elif p == "finish_measure":
        await confirm_finish(event, context)


async def confirm_finish(event, context: MemoryContext):
    data = await context.get_data()
    name    = data.get("client_name", "")
    phone   = data.get("client_phone", "")
    address = data.get("client_address", "")
    openings = data.get("openings", [])
    authorized_name = data.get("authorized_name", "")

    client_data = {
        "client_name": name, "client_phone": phone, "client_address": address,
        "openings": [{**op, "photo": "есть" if op["photos"] else "нет"} for op in openings]
    }
    img_path = generate_measurement_image(client_data)
    await bot.send_message(
        chat_id=TARGET_CHAT_ID,
        text=f"Имя: {name}\nТелефон: {phone}\nАдрес: {address}",
        attachments=[InputMedia(path=img_path)]
    )
    os.remove(img_path)

    for i, op in enumerate(openings, start=1):
        for j, photo_url in enumerate(op.get("photos", []), start=1):
            overlay_text = f"Фото {j} проёма #{i} ({op['room']})"
            try:
                overlaid = await overlay_text_on_photo(photo_url, overlay_text)
                await bot.send_message(
                    chat_id=TARGET_CHAT_ID,
                    attachments=[InputMedia(path=overlaid)]
                )
                os.remove(overlaid)
            except Exception as e:
                logging.error("Ошибка обработки фото: %s", e)

    await context.clear()
    await context.update_data(authorized_name=authorized_name)
    await context.set_state(MeasureStates.menu)
    await bot.send_message(
        chat_id=event.chat_id,
        text="Замер успешно отправлен в рабочий чат. Вы можете начать новый замер.",
        attachments=[kb([("Новый замер", "new_measure")])]
    )


# ─── EDIT ─────────────────────────────────────────────────────────────────────

FIELD_LABELS = [
    ("Комната",       "room"),
    ("Тип двери",     "door_type"),
    ("Размеры",       "dimensions"),
    ("Полотно",       "canvas"),
    ("Добор",         "dobor"),
    ("Кол-во доборов","dobor_count"),
    ("Наличники",     "nalichniki"),
    ("Порог",         "threshold"),
    ("Демонтаж",      "demontage"),
    ("Открывание",    "opening"),
    ("Комментарий",   "comment"),
]


async def show_edit_choice(event, context: MemoryContext):
    data = await context.get_data()
    openings = data.get("openings", [])
    if not openings:
        await event.message.answer("У вас нет добавленных проёмов.")
        await show_opening_menu(event, context)
        return
    await context.set_state(MeasureStates.edit_choice)
    rows = [[(f"Проём {i+1}: {op['room']}", f"eop_{i}")] for i, op in enumerate(openings)]
    await event.message.answer(
        text="Выберите проём для редактирования:",
        attachments=[kb(*rows)]
    )


@dp.message_callback(MeasureStates.edit_choice)
async def cb_edit_choice(event: MessageCallback, context: MemoryContext):
    p = event.callback.payload
    if not p.startswith("eop_"):
        return
    idx = int(p.split("_")[1])
    await context.update_data(edit_index=idx)
    await context.set_state(MeasureStates.edit_field)
    rows = [[(label, f"ef_{key}")] for label, key in FIELD_LABELS]
    rows.append([("Готово", "ef_done")])
    await event.message.answer(
        text="Выберите поле для изменения:",
        attachments=[kb(*rows)]
    )


@dp.message_callback(MeasureStates.edit_field)
async def cb_edit_field(event: MessageCallback, context: MemoryContext):
    p = event.callback.payload
    if p == "ef_done":
        await show_opening_menu(event, context)
        return
    if not p.startswith("ef_"):
        return
    field_key = p[3:]
    await context.update_data(edit_field=field_key)
    await context.set_state(MeasureStates.edit_value)
    label = next((l for l, k in FIELD_LABELS if k == field_key), field_key)
    await event.message.answer(f"Введите новое значение для «{label}»:")


@dp.message_created(F.message.body.text, MeasureStates.edit_value)
async def edit_value(event: MessageCreated, context: MemoryContext):
    data = await context.get_data()
    idx   = data.get("edit_index", 0)
    field = data.get("edit_field", "")
    openings = data.get("openings", [])
    if 0 <= idx < len(openings):
        openings[idx][field] = event.message.body.text
        await context.update_data(openings=openings)
    await event.message.answer(f"Значение обновлено: {event.message.body.text}")
    await context.set_state(MeasureStates.edit_field)
    rows = [[(label, f"ef_{key}")] for label, key in FIELD_LABELS]
    rows.append([("Готово", "ef_done")])
    await event.message.answer(
        text="Выберите следующее поле или нажмите «Готово»:",
        attachments=[kb(*rows)]
    )


# ─── DELETE ───────────────────────────────────────────────────────────────────

async def show_delete_choice(event, context: MemoryContext):
    data = await context.get_data()
    openings = data.get("openings", [])
    if not openings:
        await event.message.answer("У вас нет добавленных проёмов.")
        await show_opening_menu(event, context)
        return
    await context.set_state(MeasureStates.delete_choice)
    rows = [[(f"Проём {i+1}: {op['room']}", f"dop_{i}")] for i, op in enumerate(openings)]
    await event.message.answer(
        text="Выберите проём для удаления:",
        attachments=[kb(*rows)]
    )


@dp.message_callback(MeasureStates.delete_choice)
async def cb_delete_choice(event: MessageCallback, context: MemoryContext):
    p = event.callback.payload
    if not p.startswith("dop_"):
        return
    idx = int(p.split("_")[1])
    data = await context.get_data()
    openings = data.get("openings", [])
    if 0 <= idx < len(openings):
        room = openings[idx]["room"]
        await context.update_data(delete_index=idx)
        await context.set_state(MeasureStates.delete_confirm)
        await event.message.answer(
            text=f"Удалить «Проём {idx+1}: {room}»?",
            attachments=[kb([("Да, удалить", "del_yes"), ("Отмена", "del_no")])]
        )


@dp.message_callback(MeasureStates.delete_confirm)
async def cb_delete_confirm(event: MessageCallback, context: MemoryContext):
    p = event.callback.payload
    if p == "del_yes":
        data = await context.get_data()
        idx = data.get("delete_index", 0)
        openings = data.get("openings", [])
        if 0 <= idx < len(openings):
            room = openings.pop(idx)["room"]
            await context.update_data(openings=openings)
            await event.message.answer(f"Проём «{room}» удалён.")
    else:
        await event.message.answer("Удаление отменено.")
    await show_opening_menu(event, context)


# ─── MAIN ─────────────────────────────────────────────────────────────────────

async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
