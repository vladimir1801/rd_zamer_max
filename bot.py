import logging
import io
import os
import json
import re

from PIL import Image, ImageDraw, ImageFont

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    KeyboardButton,
    InputMediaPhoto,
    Contact
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ConversationHandler,
    ContextTypes
)
from telegram.request import HTTPXRequest

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# -------------------------------------------------------------------
# 1) ПАРАМЕТРЫ БОТА И ЗАГРУЗКА БАЗЫ ДАННЫХ
# -------------------------------------------------------------------
TOKEN = os.environ.get("TOKEN")  # На Railway переменная должна называться именно "TOKEN"
TARGET_CHAT_ID = -1002551145135       # ID чата для отправки итогового замера

# Файл с разрешёнными номерами
ALLOWED_NUMBERS_FILE = "allowed_numbers.json"

try:
    with open(ALLOWED_NUMBERS_FILE, "r", encoding="utf-8") as f:
        ALLOWED_NUMBERS = json.load(f)  # пример: {"89123816215": "Владимир"}
except Exception as e:
    logging.error("Ошибка загрузки базы номеров: %s", e)
    ALLOWED_NUMBERS = {}

SKIP_TEXT = "Пропустить"
DONE_TEXT = "Готово"
CANCEL_TEXT = "Отключить бот"
LAUNCH_TEXT = "Запустить"
EDIT_TEXT = "Редактировать замер"

# -------------------------------------------------------------------
# 2) СОСТОЯНИЯ ConversationHandler
# Определяем новые состояния; вводим этап авторизации (AUTH)
# -------------------------------------------------------------------
# Состояние авторизации
AUTH, MENU, GET_NAME, GET_PHONE, GET_ADDRESS = range(5)
(
    ENTER_ROOM,
    ENTER_DOOR_TYPE,
    ENTER_DOOR_TYPE_CUSTOM,
    ENTER_DIMENSIONS,
    ENTER_CANVAS,
    ENTER_DOBOR,
    ENTER_DOBOR_CUSTOM,
    ENTER_DOBOR_COUNT,
    ENTER_DOBOR_COUNT_CUSTOM,
    ENTER_NALICHNIKI_CHOICE,
    ENTER_NALICHNIKI_CUSTOM,
    ENTER_THRESHOLD_CHOICE,
    ENTER_DEMONTAGE_CHOICE,
    ENTER_OPENING_CHOICE,
    ENTER_OPENING_CUSTOM,
    ENTER_COMMENT,
    ENTER_PHOTOS,
    OPENING_MENU
) = range(5, 23)
EDIT_CHOICE, EDIT_FIELD, EDIT_VALUE, DELETE_CHOICE, DELETE_CONFIRM = range(23, 28)
CHECK_MEASURE = 28

# -------------------------------------------------------------------
# 3) ФУНКЦИЯ ГЕНЕРАЦИИ PNG (ТАБЛИЦЫ) + ЛОГОТИП
# -------------------------------------------------------------------
def generate_measurement_image(client_data: dict) -> io.BytesIO:
    col_widths = [50, 150, 200, 200, 100, 100, 110, 110, 80, 100, 120, 200]
    headers = [
        "№", "Комната", "Тип двери", "Размеры", "Полотно",
        "Добор", "Кол-во доборов", "Наличники",
        "Порог", "Демонтаж", "Открывание", "Комментарий"
    ]
    openings = client_data.get("openings", [])
    rows = [headers]
    for i, op in enumerate(openings, start=1):
        row = [
            str(i),
            op["room"],
            op["door_type"],
            op["dimensions"],
            op["canvas"],
            op["dobor"],
            op["dobor_count"],
            op["nalichniki"],
            op["threshold"],
            op["demontage"],
            op["opening"],
            op["comment"]
        ]
        rows.append(row)
    client_info = (
        f"Имя: {client_data.get('client_name', '')}\n"
        f"Телефон: {client_data.get('client_phone', '')}\n"
        f"Адрес: {client_data.get('client_address', '')}\n"
    )
    try:
        font = ImageFont.truetype("Montserrat-Regular.ttf", 16)
    except Exception as e:
        logging.error("Ошибка загрузки шрифта: %s", e)
        font = ImageFont.load_default()
    temp_img = Image.new("RGB", (10, 10))
    draw_temp = ImageDraw.Draw(temp_img)
    def get_text_size(text, font_obj):
        left, top, right, bottom = draw_temp.textbbox((0, 0), text, font=font_obj)
        return (right - left, bottom - top)
    def wrap_text(text, max_width):
        words = text.split()
        lines = []
        if not words:
            return [""]
        current_line = words[0]
        for w in words[1:]:
            test_line = current_line + " " + w
            w_test, _ = get_text_size(test_line, font)
            if w_test <= max_width:
                current_line = test_line
            else:
                lines.append(current_line)
                current_line = w
        lines.append(current_line)
        return lines
    cell_padding = 10
    line_spacing = 5
    row_lines = []
    row_heights = []
    for row_idx, row_data in enumerate(rows):
        max_height = 0
        row_lines.append([])
        for col_idx, cell_text in enumerate(row_data):
            cell_text = str(cell_text)
            effective_width = col_widths[col_idx] - 2 * cell_padding
            lines = wrap_text(cell_text, effective_width)
            _, line_h = get_text_size("A", font)
            line_height_with_spacing = line_h + line_spacing
            cell_height = len(lines) * line_height_with_spacing + 3 * cell_padding
            if cell_height > max_height:
                max_height = cell_height
            row_lines[row_idx].append(lines)
        row_heights.append(max_height)
    margin = 50
    table_width = sum(col_widths) + margin * 2
    info_lines = client_info.strip().split("\n")
    _, line_h = get_text_size("A", font)
    info_block_height = line_h * len(info_lines) + 40
    logo_path = "Logo_rusdver.png"
    try:
        logo = Image.open(logo_path).convert("RGBA")
        logo.thumbnail((150, 9999))
        logo_width, logo_height = logo.size
    except Exception as e:
        logging.error("Ошибка загрузки логотипа: %s", e)
        logo = None
        logo_width, logo_height = 0, 0
    top_block_height = max(info_block_height, logo_height) + 20
    table_height = sum(row_heights) + margin * 2
    total_height = top_block_height + table_height
    img = Image.new("RGB", (table_width, total_height), color="white")
    draw = ImageDraw.Draw(img)
    y_offset = 20
    draw.text((margin, y_offset), client_info, font=font, fill="black")
    if logo:
        x_logo = table_width - margin - logo_width
        y_logo = 20
        img.paste(logo, (x_logo, y_logo), logo)
    y_offset = top_block_height
    for row_idx, row_data in enumerate(rows):
        row_h = row_heights[row_idx]
        x_offset = margin
        for col_idx, _ in enumerate(row_data):
            w_col = col_widths[col_idx]
            draw.rectangle([x_offset, y_offset, x_offset + w_col, y_offset + row_h],
                           outline="black", width=1)
            lines = row_lines[row_idx][col_idx]
            text_x = x_offset + cell_padding
            text_y = y_offset + cell_padding
            _, line_h = get_text_size("A", font)
            line_height_with_spacing = line_h + line_spacing
            for line in lines:
                draw.text((text_x, text_y), line, font=font, fill="black")
                text_y += line_height_with_spacing
            x_offset += w_col
        y_offset += row_h
    bio = io.BytesIO()
    bio.name = "zamery.png"
    img.save(bio, "PNG")
    bio.seek(0)
    return bio

# -------------------------------------------------------------------
# 4) ФУНКЦИИ ДЛЯ НАЛОЖЕНИЯ ПОДПИСЕЙ НА ФОТО И ОТПРАВКИ АЛЬБОМА
# -------------------------------------------------------------------
FONT_PATH = "Montserrat-Regular.ttf"

async def overlay_text_on_photo(context: ContextTypes.DEFAULT_TYPE, file_id: str, text: str) -> io.BytesIO:
    temp_path = "temp_photo.jpg"
    telegram_file = await context.bot.get_file(file_id)
    await telegram_file.download_to_drive(temp_path)
    img = Image.open(temp_path).convert("RGBA")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype(FONT_PATH, 24)
    except Exception as e:
        logging.error("Ошибка загрузки шрифта overlay: %s", e)
        font = ImageFont.load_default()
    text_x = 20
    text_y = img.height - 60
    text_w, text_h = draw.textsize(text, font=font)
    box = [text_x - 10, text_y - 10, text_x + text_w + 10, text_y + text_h + 10]
    draw.rectangle(box, fill=(0, 0, 0, 128))
    draw.text((text_x, text_y), text, fill=(255, 255, 255, 255), font=font)
    out_buf = io.BytesIO()
    out_buf.name = "photo.png"
    img.save(out_buf, "PNG")
    out_buf.seek(0)
    if os.path.exists(temp_path):
        os.remove(temp_path)
    return out_buf

from telegram import InputMediaPhoto

async def send_photos_with_overlay_as_album(context: ContextTypes.DEFAULT_TYPE, chat_id: int, photo_overlays: list):
    media_group = []
    album_caption = "Все фото с подписями"
    for i, (file_id, overlay_text) in enumerate(photo_overlays):
        processed_img = await overlay_text_on_photo(context, file_id, overlay_text)
        if i == 0:
            media_group.append(InputMediaPhoto(processed_img, caption=album_caption))
        else:
            media_group.append(InputMediaPhoto(processed_img))
    await context.bot.send_media_group(chat_id=chat_id, media=media_group)

# -------------------------------------------------------------------
# 5) АВТОРИЗАЦИЯ. ЭТАП: "Запустить" → "Поделиться контактом"
# -------------------------------------------------------------------
async def show_auth_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Эта функция вызывается по команде /start (или можно сделать ее entry_point)
    keyboard = [
        [KeyboardButton(LAUNCH_TEXT)],
        [KeyboardButton(CANCEL_TEXT)]
    ]
    markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text("Добро пожаловать! Для использования бота нажмите «Запустить».", reply_markup=markup)
    return MENU  # Если пользователь нажмет "Запустить", перейдем в AUTH

async def auth_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [KeyboardButton("Поделиться контактом", request_contact=True)],
        [KeyboardButton(CANCEL_TEXT)]
    ]
    markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text("Для авторизации, пожалуйста, поделитесь своим контактом.", reply_markup=markup)
    return AUTH

async def handle_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    contact: Contact = update.message.contact
    if not contact:
        await update.message.reply_text("Контакт не получен. Попробуйте ещё раз.")
        return AUTH

    # Нормализуем номер: убираем все нецифровые символы
    phone = re.sub(r"\D", "", contact.phone_number)
    if phone in ALLOWED_NUMBERS:
        name = ALLOWED_NUMBERS[phone]
        context.user_data["authorized_name"] = name
        greeting = f"Здравствуйте, {name}! Для начала замера нажмите кнопку 'Новый замер' или 'Отключить бот' для отмены."
        keyboard = [
            [KeyboardButton("Новый замер")],
            [KeyboardButton(CANCEL_TEXT)]
        ]
        markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        await update.message.reply_text(greeting, reply_markup=markup)
        return MENU
    else:
        await update.message.reply_text("Извините, ваш номер не найден в базе. Доступ закрыт.")
        return ConversationHandler.END

# -------------------------------------------------------------------
# 6) ОСНОВНАЯ ЛОГИКА ЗАМЕРА (как в предыдущем варианте)
# -------------------------------------------------------------------
async def menu_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == CANCEL_TEXT:
        return await cancel(update, context)
    if text == "Новый замер":
        context.user_data["openings"] = []
        context.user_data.pop("client_name", None)
        context.user_data.pop("client_phone", None)
        context.user_data.pop("client_address", None)
        await update.message.reply_text("Введите имя клиента:", reply_markup=ReplyKeyboardRemove())
        return GET_NAME
    else:
        await update.message.reply_text("Нажмите кнопку 'Новый замер'.")
        return MENU

async def get_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == CANCEL_TEXT:
        return await cancel(update, context)
    context.user_data["client_name"] = update.message.text
    await update.message.reply_text("Введите телефон клиента:")
    return GET_PHONE

async def get_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == CANCEL_TEXT:
        return await cancel(update, context)
    context.user_data["client_phone"] = update.message.text
    await update.message.reply_text("Введите адрес клиента:")
    return GET_ADDRESS

async def get_address(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == CANCEL_TEXT:
        return await cancel(update, context)
    context.user_data["client_address"] = update.message.text
    return await start_opening(update, context)

async def start_opening(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == CANCEL_TEXT:
        return await cancel(update, context)
    context.user_data["current_opening"] = {
        "room": "",
        "door_type": "",
        "dimensions": "",
        "canvas": "---",
        "dobor": "---",
        "dobor_count": "---",
        "nalichniki": "---",
        "threshold": "",
        "demontage": "",
        "opening": "---",
        "comment": "",
        "photos": []
    }
    await update.message.reply_text("Введите название комнаты (например, 'Кухня'):", reply_markup=ReplyKeyboardRemove())
    return ENTER_ROOM

async def enter_room(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == CANCEL_TEXT:
        return await cancel(update, context)
    context.user_data["current_opening"]["room"] = update.message.text
    door_types = [
        ["Межкомнатная дверь"],
        ["Скрытая дверь"],
        ["Входная дверь"],
        ["Облагораживание проема"],
        ["Складная дверь (книжка)"],
        ["Раздвижная дверь (одностворчатая)"],
        ["Раздвижная дверь (двустворчатая)"],
        ["Двустворчатая дверь (распашная)"],
        ["Иное"]
    ]
    # Добавляем кнопку "Отключить бот" в конце клавиатуры, если нужно
    door_types.append([CANCEL_TEXT])
    markup = ReplyKeyboardMarkup(door_types, resize_keyboard=True)
    await update.message.reply_text("Выберите тип двери:", reply_markup=markup)
    return ENTER_DOOR_TYPE

async def enter_door_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == CANCEL_TEXT:
        return await cancel(update, context)
    if text == "Иное":
        await update.message.reply_text("Введите ваш вариант типа двери:", reply_markup=ReplyKeyboardRemove())
        return ENTER_DOOR_TYPE_CUSTOM
    else:
        context.user_data["current_opening"]["door_type"] = text
        await update.message.reply_text("Введите размеры проёма (высота, ширина, толщина стены):", reply_markup=ReplyKeyboardRemove())
        return ENTER_DIMENSIONS

async def enter_door_type_custom(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == CANCEL_TEXT:
        return await cancel(update, context)
    context.user_data["current_opening"]["door_type"] = update.message.text
    await update.message.reply_text("Введите размеры проёма (высота, ширина, толщина стены):", reply_markup=ReplyKeyboardRemove())
    return ENTER_DIMENSIONS

async def enter_dimensions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == CANCEL_TEXT:
        return await cancel(update, context)
    context.user_data["current_opening"]["dimensions"] = update.message.text
    door_type = context.user_data["current_opening"]["door_type"]
    if door_type == "Облагораживание проема":
        context.user_data["current_opening"]["canvas"] = "---"
        return await ask_dobor(update, context)
    else:
        canvas_variants = [
            ["600", "700", "800", "Иное"],
            [CANCEL_TEXT]
        ]
        markup = ReplyKeyboardMarkup(canvas_variants, resize_keyboard=True)
        await update.message.reply_text("Введите рекомендуемое полотно:", reply_markup=markup)
        return ENTER_CANVAS

async def enter_canvas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == CANCEL_TEXT:
        return await cancel(update, context)
    if text == "Иное":
        await update.message.reply_text("Введите ваш вариант полотна:", reply_markup=ReplyKeyboardRemove())
        return ENTER_CANVAS
    else:
        context.user_data["current_opening"]["canvas"] = text
        return await ask_dobor(update, context)

async def ask_dobor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == CANCEL_TEXT:
        return await cancel(update, context)
    dobor_variants = [
        ["100 мм", "150 мм"],
        ["200 мм", "нет"],
        ["Иное"],
    ]
    # Добавляем кнопку "Отключить бот"
    dobor_variants.append([CANCEL_TEXT])
    markup = ReplyKeyboardMarkup(dobor_variants, resize_keyboard=True)
    await update.message.reply_text("Введите ширину добора:", reply_markup=markup)
    return ENTER_DOBOR

async def get_dobor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.lower()
    if text == CANCEL_TEXT.lower():
        return await cancel(update, context)
    if text == "иное":
        await update.message.reply_text("Введите ваш вариант добора:", reply_markup=ReplyKeyboardRemove())
        return ENTER_DOBOR_CUSTOM
    elif text in ["100 мм", "150 мм", "200 мм", "нет"]:
        context.user_data["current_opening"]["dobor"] = text
        if text == "нет":
            context.user_data["current_opening"]["dobor_count"] = "---"
            return await ask_nalichniki(update, context)
        else:
            dobor_count_variants = [
                ["1,5", "2,5", "3", "нет"],
                ["Иное"]
            ]
            dobor_count_variants.append([CANCEL_TEXT])
            markup = ReplyKeyboardMarkup(dobor_count_variants, resize_keyboard=True)
            await update.message.reply_text("Введите кол-во доборов:", reply_markup=markup)
            return ENTER_DOBOR_COUNT
    else:
        await update.message.reply_text("Выберите один из вариантов или 'Иное'.")
        return ENTER_DOBOR

async def enter_dobor_custom(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == CANCEL_TEXT:
        return await cancel(update, context)
    context.user_data["current_opening"]["dobor"] = update.message.text
    dobor_count_variants = [
        ["1,5", "2,5", "3", "нет"],
        ["Иное"]
    ]
    dobor_count_variants.append([CANCEL_TEXT])
    markup = ReplyKeyboardMarkup(dobor_count_variants, resize_keyboard=True)
    await update.message.reply_text("Введите кол-во доборов:", reply_markup=markup)
    return ENTER_DOBOR_COUNT

async def enter_dobor_count(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.lower()
    if text == CANCEL_TEXT.lower():
        return await cancel(update, context)
    if text == "иное":
        await update.message.reply_text("Введите ваш вариант кол-ва доборов:", reply_markup=ReplyKeyboardRemove())
        return ENTER_DOBOR_COUNT_CUSTOM
    elif text in ["1,5", "2,5", "3", "нет"]:
        context.user_data["current_opening"]["dobor_count"] = text
        return await ask_nalichniki(update, context)
    else:
        await update.message.reply_text("Выберите один из вариантов или 'Иное'.")
        return ENTER_DOBOR_COUNT

async def enter_dobor_count_custom(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == CANCEL_TEXT:
        return await cancel(update, context)
    context.user_data["current_opening"]["dobor_count"] = update.message.text
    return await ask_nalichniki(update, context)

async def ask_nalichniki(update: Update, context: ContextTypes.DEFAULT_TYPE):
    door_type = context.user_data["current_opening"]["door_type"]
    if door_type in ["Скрытая дверь", "Входная дверь"]:
        context.user_data["current_opening"]["nalichniki"] = "---"
        return await ask_threshold(update, context)
    else:
        # Добавляем кнопку "5,5" между "5" и "6"
        variants = [
            ["2,5"],
            ["5"],
            ["5,5"],
            ["6"],
            ["нет"],
            ["Иное"]
        ]
        variants.append([CANCEL_TEXT])
        markup = ReplyKeyboardMarkup(variants, resize_keyboard=True)
        await update.message.reply_text("Введите кол-во наличников:", reply_markup=markup)
        return ENTER_NALICHNIKI_CHOICE

async def enter_nalichniki_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.lower()
    if text == CANCEL_TEXT.lower():
        return await cancel(update, context)
    if text == "иное":
        await update.message.reply_text("Введите ваш вариант кол-ва наличников:", reply_markup=ReplyKeyboardRemove())
        return ENTER_NALICHNIKI_CUSTOM
    elif text in ["2,5", "5", "5,5", "6", "нет"]:
        context.user_data["current_opening"]["nalichniki"] = text
        return await ask_threshold(update, context)
    else:
        await update.message.reply_text("Выберите один из вариантов или 'Иное'.")
        return ENTER_NALICHNIKI_CHOICE

async def enter_nalichniki_custom(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == CANCEL_TEXT:
        return await cancel(update, context)
    context.user_data["current_opening"]["nalichniki"] = update.message.text
    return await ask_threshold(update, context)

async def ask_threshold(update: Update, context: ContextTypes.DEFAULT_TYPE):
    door_type = context.user_data["current_opening"]["door_type"]
    if door_type == "Облагораживание проема":
        context.user_data["current_opening"]["threshold"] = "---"
        return await ask_demontage(update, context)
    else:
        btns = [
            ["да", "нет"],
            [CANCEL_TEXT]
        ]
        markup = ReplyKeyboardMarkup(btns, resize_keyboard=True)
        await update.message.reply_text("Наличие порога?", reply_markup=markup)
        return ENTER_THRESHOLD_CHOICE

async def threshold_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.lower()
    if text == CANCEL_TEXT.lower():
        return await cancel(update, context)
    if text in ["да", "нет"]:
        context.user_data["current_opening"]["threshold"] = text
        return await ask_demontage(update, context)
    else:
        await update.message.reply_text("Выберите 'да' или 'нет'.")
        return ENTER_THRESHOLD_CHOICE

async def ask_demontage(update: Update, context: ContextTypes.DEFAULT_TYPE):
    btns = [
        ["да", "нет"],
        [CANCEL_TEXT]
    ]
    markup = ReplyKeyboardMarkup(btns, resize_keyboard=True)
    await update.message.reply_text("Демонтаж старой двери?", reply_markup=markup)
    return ENTER_DEMONTAGE_CHOICE

async def demontage_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.lower()
    if text == CANCEL_TEXT.lower():
        return await cancel(update, context)
    if text not in ["да", "нет"]:
        await update.message.reply_text("Выберите 'да' или 'нет'.")
        return ENTER_DEMONTAGE_CHOICE
    context.user_data["current_opening"]["demontage"] = text
    return await ask_opening(update, context)

async def ask_opening(update: Update, context: ContextTypes.DEFAULT_TYPE):
    door_type = context.user_data["current_opening"]["door_type"]
    if door_type in ["Облагораживание проема", "Складная дверь (книжка)", "Раздвижная дверь (одностворчатая)", "Раздвижная дверь (двустворчатая)", "Двустворчатая дверь (распашная)"]:
        context.user_data["current_opening"]["opening"] = "---"
        return await ask_comment(update, context)
    else:
        variants = [
            ["Левое", "Правое"],
            ["Левое рев.", "Правое рев."],
            ["Иное"]
        ]
        variants.append([CANCEL_TEXT])
        markup = ReplyKeyboardMarkup(variants, resize_keyboard=True)
        await update.message.reply_text("Введите открывание:", reply_markup=markup)
        return ENTER_OPENING_CHOICE

async def opening_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == CANCEL_TEXT:
        return await cancel(update, context)
    if text == "Иное":
        await update.message.reply_text("Введите ваш вариант открывания:", reply_markup=ReplyKeyboardRemove())
        return ENTER_OPENING_CUSTOM
    elif text in ["Левое", "Правое", "Левое рев.", "Правое рев."]:
        context.user_data["current_opening"]["opening"] = text
        return await ask_comment(update, context)
    else:
        await update.message.reply_text("Выберите один из вариантов или 'Иное'.")
        return ENTER_OPENING_CHOICE

async def opening_custom(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == CANCEL_TEXT:
        return await cancel(update, context)
    context.user_data["current_opening"]["opening"] = update.message.text
    return await ask_comment(update, context)

async def ask_comment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[KeyboardButton(SKIP_TEXT)], [CANCEL_TEXT]]
    markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text("Введите комментарий или нажмите «Пропустить»:", reply_markup=markup)
    return ENTER_COMMENT

async def enter_comment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == CANCEL_TEXT:
        return await cancel(update, context)
    if text == SKIP_TEXT:
        context.user_data["current_opening"]["comment"] = ""
    else:
        context.user_data["current_opening"]["comment"] = text
    keyboard = [
        [KeyboardButton(DONE_TEXT)],
        [KeyboardButton(SKIP_TEXT)],
        [CANCEL_TEXT]
    ]
    markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text("Прикрепите фото (по одной). Когда закончите, нажмите «Готово». Если нет фото – «Пропустить».", reply_markup=markup)
    return ENTER_PHOTOS

async def enter_photos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == CANCEL_TEXT:
        return await cancel(update, context)
    if text == SKIP_TEXT or text == DONE_TEXT:
        return await save_opening(update, context)
    if update.message.photo:
        file_id = update.message.photo[-1].file_id
        context.user_data["current_opening"]["photos"].append(file_id)
        await update.message.reply_text("Фото сохранено. Можете отправить ещё, или нажмите «Готово».")
        return ENTER_PHOTOS
    else:
        await update.message.reply_text("Отправьте фото, либо нажмите «Готово» или «Пропустить».")
        return ENTER_PHOTOS

async def save_opening(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == CANCEL_TEXT:
        return await cancel(update, context)
    current = context.user_data["current_opening"]
    context.user_data["openings"].append(current)
    keyboard = [
        [KeyboardButton("Следующий проём")],
        [KeyboardButton("Редактировать проём"), KeyboardButton("Удалить проём")],
        [KeyboardButton("Проверить и завершить")],
        [CANCEL_TEXT]
    ]
    markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text("Проём сохранён. Что делаем дальше?", reply_markup=markup)
    return OPENING_MENU

async def handle_opening_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == CANCEL_TEXT:
        return await cancel(update, context)
    if text == "Следующий проём":
        return await start_opening(update, context)
    elif text == "Редактировать проём":
        return await edit_choice(update, context)
    elif text == "Удалить проём":
        return await delete_choice(update, context)
    elif text == "Проверить и завершить":
        return await check_measure(update, context)
    else:
        await update.message.reply_text("Выберите: «Следующий проём», «Редактировать проём», «Удалить проём» или «Проверить и завершить».")
        return OPENING_MENU

# -------------------------------------------------------------------
# ЭТАП ПРОВЕРКИ: "Проверить и завершить"
# -------------------------------------------------------------------
async def check_measure(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == CANCEL_TEXT:
        return await cancel(update, context)
    name = context.user_data.get("client_name", "")
    phone = context.user_data.get("client_phone", "")
    address = context.user_data.get("client_address", "")
    openings = context.user_data.get("openings", [])
    client_data = {
        "client_name": name,
        "client_phone": phone,
        "client_address": address,
        "openings": []
    }
    for op in openings:
        copy_op = dict(op)
        copy_op["photo"] = "есть" if copy_op["photos"] else "нет"
        client_data["openings"].append(copy_op)
    image_data = generate_measurement_image(client_data)
    caption_text = f"Имя: {name}\nТелефон: {phone}\nАдрес: {address}"
    await update.message.reply_photo(photo=image_data, caption=caption_text)
    keyboard = [
        [KeyboardButton("Редактировать замер")],
        [KeyboardButton("Завершить замер")],
        [CANCEL_TEXT]
    ]
    markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text("Проверьте замер. Если всё правильно – нажмите «Завершить замер», иначе – «Редактировать замер».", reply_markup=markup)
    return CHECK_MEASURE

async def check_measure_response(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "Редактировать замер":
        return await edit_choice(update, context)
    elif text == "Завершить замер":
        return await confirm_finish(update, context)
    else:
        await update.message.reply_text("Пожалуйста, выберите «Редактировать замер» или «Завершить замер».")
        return CHECK_MEASURE

async def confirm_finish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == CANCEL_TEXT:
        return await cancel(update, context)
    name = context.user_data.get("client_name", "")
    phone = context.user_data.get("client_phone", "")
    address = context.user_data.get("client_address", "")
    openings = context.user_data.get("openings", [])
    client_data = {
        "client_name": name,
        "client_phone": phone,
        "client_address": address,
        "openings": []
    }
    for op in openings:
        copy_op = dict(op)
        copy_op["photo"] = "есть" if copy_op["photos"] else "нет"
        client_data["openings"].append(copy_op)
    image_data = generate_measurement_image(client_data)
    caption_text = f"Имя: {name}\nТелефон: {phone}\nАдрес: {address}"
    await context.bot.send_photo(chat_id=TARGET_CHAT_ID, photo=image_data, caption=caption_text)
    photo_overlays = []
    for i, op in enumerate(openings, start=1):
        for j, file_id in enumerate(op["photos"], start=1):
            overlay_text = f"Фото {j} проёма #{i} ({op['room']})"
            photo_overlays.append((file_id, overlay_text))
    if photo_overlays:
        await send_photos_with_overlay_as_album(context, TARGET_CHAT_ID, photo_overlays)
    keyboard = [[KeyboardButton("Новый замер")]]
    markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text("Замер успешно отправлен в рабочий чат. Вы можете начать новый замер.", reply_markup=markup)
    return MENU

# -------------------------------------------------------------------
# РЕДАКТИРОВАНИЕ / УДАЛЕНИЕ
# -------------------------------------------------------------------
async def edit_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    openings = context.user_data.get("openings", [])
    if not openings:
        await update.message.reply_text("У вас нет добавленных проёмов.")
        return OPENING_MENU
    kb = []
    for i, op in enumerate(openings, start=1):
        kb.append([KeyboardButton(f"Проём {i}: {op['room']}")])
    markup = ReplyKeyboardMarkup(kb, resize_keyboard=True)
    await update.message.reply_text("Выберите проём для редактирования:", reply_markup=markup)
    return EDIT_CHOICE

async def edit_choice_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    parts = text.split()
    if len(parts) < 2 or parts[0] != "Проём":
        await update.message.reply_text("Неверный формат. Выберите проём из списка.")
        return EDIT_CHOICE
    number_str = parts[1].rstrip(":")
    try:
        number = int(number_str)
        index = number - 1
        openings = context.user_data["openings"]
        if index < 0 or index >= len(openings):
            raise ValueError
    except:
        await update.message.reply_text("Неверный выбор проёма.")
        return EDIT_CHOICE
    context.user_data["edit_index"] = index
    fields = [
        "Комната", "Тип двери", "Размеры", "Полотно",
        "Добор", "Кол-во доборов", "Наличники",
        "Порог", "Демонтаж", "Открывание", "Комментарий"
    ]
    kb = [[KeyboardButton(f)] for f in fields] + [[KeyboardButton("Готово")]]
    markup = ReplyKeyboardMarkup(kb, resize_keyboard=True)
    await update.message.reply_text("Выберите поле для изменения:", reply_markup=markup)
    return EDIT_FIELD

async def edit_field_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.lower()
    if text == "готово":
        return await opening_menu_return(update, context)
    field_map = {
        "комната": "room",
        "тип двери": "door_type",
        "размеры": "dimensions",
        "полотно": "canvas",
        "добор": "dobor",
        "кол-во доборов": "dobor_count",
        "наличники": "nalichniki",
        "порог": "threshold",
        "демонтаж": "demontage",
        "открывание": "opening",
        "комментарий": "comment"
    }
    if text not in field_map:
        await update.message.reply_text("Выберите поле из списка или 'Готово'.")
        return EDIT_FIELD
    context.user_data["edit_field"] = field_map[text]
    await update.message.reply_text(f"Введите новое значение для «{text}»:")
    return EDIT_VALUE

async def edit_value_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    new_value = update.message.text
    index = context.user_data["edit_index"]
    field = context.user_data["edit_field"]
    openings = context.user_data["openings"]
    openings[index][field] = new_value
    await update.message.reply_text(f"Поле «{field}» обновлено на: {new_value}.")
    fields = [
        "Комната", "Тип двери", "Размеры", "Полотно",
        "Добор", "Кол-во доборов", "Наличники",
        "Порог", "Демонтаж", "Открывание", "Комментарий"
    ]
    kb = [[KeyboardButton(f)] for f in fields] + [[KeyboardButton("Готово")]]
    markup = ReplyKeyboardMarkup(kb, resize_keyboard=True)
    await update.message.reply_text("Выберите поле для изменения ещё или нажмите «Готово»:", reply_markup=markup)
    return EDIT_FIELD

async def opening_menu_return(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [KeyboardButton("Следующий проём")],
        [KeyboardButton("Редактировать проём"), KeyboardButton("Удалить проём")],
        [KeyboardButton("Проверить и завершить")]
    ]
    markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text("Что делаем дальше?", reply_markup=markup)
    return OPENING_MENU

async def delete_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    openings = context.user_data.get("openings", [])
    if not openings:
        await update.message.reply_text("У вас нет добавленных проёмов.")
        return OPENING_MENU
    kb = []
    for i, op in enumerate(openings, start=1):
        kb.append([KeyboardButton(f"Проём {i}: {op['room']}")])
    markup = ReplyKeyboardMarkup(kb, resize_keyboard=True)
    await update.message.reply_text("Выберите проём для удаления:", reply_markup=markup)
    return DELETE_CHOICE

async def delete_choice_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    parts = text.split()
    if len(parts) < 2 or parts[0] != "Проём":
        await update.message.reply_text("Неверный формат. Выберите проём из списка.")
        return DELETE_CHOICE
    number_str = parts[1].rstrip(":")
    try:
        number = int(number_str)
        index = number - 1
        openings = context.user_data["openings"]
        if index < 0 or index >= len(openings):
            raise ValueError
    except:
        await update.message.reply_text("Неверный выбор проёма.")
        return DELETE_CHOICE
    context.user_data["delete_index"] = index
    proem = context.user_data["openings"][index]
    kb = [[KeyboardButton("Да, удалить"), KeyboardButton("Отмена")]]
    markup = ReplyKeyboardMarkup(kb, resize_keyboard=True)
    await update.message.reply_text(f"Вы уверены, что хотите удалить «Проём {number}: {proem['room']}»?", reply_markup=markup)
    return DELETE_CONFIRM

async def delete_confirm_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.lower()
    if text == "да, удалить":
        index = context.user_data["delete_index"]
        proem = context.user_data["openings"].pop(index)
        await update.message.reply_text(f"Проём «{proem['room']}» удалён.")
    else:
        await update.message.reply_text("Удаление отменено.")
    return await opening_menu_return(update, context)

# -------------------------------------------------------------------
# 7) /Отмена и fallback
# -------------------------------------------------------------------
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Диалог отменён. Бот отключён. Для повторного запуска нажмите 'Запустить'.")
    return ConversationHandler.END

async def fallback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Непредвиденная команда или сообщение. Диалог завершён.\nНажмите 'Запустить', чтобы начать заново.")
    return ConversationHandler.END

# -------------------------------------------------------------------
# 8) ГЛОБАЛЬНАЯ АВТОРИЗАЦИЯ: ЭТАП АВТОРИЗАЦИИ
# -------------------------------------------------------------------
async def auth_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [KeyboardButton("Поделиться контактом", request_contact=True)],
        [KeyboardButton(CANCEL_TEXT)]
    ]
    markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text("Для авторизации, пожалуйста, поделитесь своим контактом.", reply_markup=markup)
    return AUTH

async def handle_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    contact = update.message.contact
    if not contact:
        await update.message.reply_text("Контакт не получен. Попробуйте ещё раз.")
        return AUTH
    phone = re.sub(r"\D", "", contact.phone_number)
    if phone in ALLOWED_NUMBERS:
        name = ALLOWED_NUMBERS[phone]
        context.user_data["authorized_name"] = name
        greeting = f"Здравствуйте, {name}! Для начала замера нажмите кнопку 'Новый замер' или 'Отключить бот' для отмены."
        keyboard = [
            [KeyboardButton("Новый замер")],
            [KeyboardButton(CANCEL_TEXT)]
        ]
        markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        await update.message.reply_text(greeting, reply_markup=markup)
        return MENU
    else:
        await update.message.reply_text("Извините, ваш номер не найден в базе. Доступ закрыт.")
        return ConversationHandler.END

# -------------------------------------------------------------------
# 9) ЭТАП ПРОВЕРКИ: "Проверить и завершить"
# -------------------------------------------------------------------
async def check_measure(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == CANCEL_TEXT:
        return await cancel(update, context)
    name = context.user_data.get("client_name", "")
    phone = context.user_data.get("client_phone", "")
    address = context.user_data.get("client_address", "")
    openings = context.user_data.get("openings", [])
    client_data = {
        "client_name": name,
        "client_phone": phone,
        "client_address": address,
        "openings": []
    }
    for op in openings:
        copy_op = dict(op)
        copy_op["photo"] = "есть" if copy_op["photos"] else "нет"
        client_data["openings"].append(copy_op)
    image_data = generate_measurement_image(client_data)
    caption_text = f"Имя: {name}\nТелефон: {phone}\nАдрес: {address}"
    await update.message.reply_photo(photo=image_data, caption=caption_text)
    keyboard = [
        [KeyboardButton("Редактировать замер")],
        [KeyboardButton("Завершить замер")],
        [KeyboardButton(CANCEL_TEXT)]
    ]
    markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text("Проверьте замер. Если всё правильно – нажмите 'Завершить замер', иначе – 'Редактировать замер'.", reply_markup=markup)
    return CHECK_MEASURE

async def check_measure_response(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "Редактировать замер":
        return await edit_choice(update, context)
    elif text == "Завершить замер":
        return await confirm_finish(update, context)
    else:
        await update.message.reply_text("Пожалуйста, выберите 'Редактировать замер' или 'Завершить замер'.")
        return CHECK_MEASURE

async def confirm_finish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == CANCEL_TEXT:
        return await cancel(update, context)
    name = context.user_data.get("client_name", "")
    phone = context.user_data.get("client_phone", "")
    address = context.user_data.get("client_address", "")
    openings = context.user_data.get("openings", [])
    client_data = {
        "client_name": name,
        "client_phone": phone,
        "client_address": address,
        "openings": []
    }
    for op in openings:
        copy_op = dict(op)
        copy_op["photo"] = "есть" if copy_op["photos"] else "нет"
        client_data["openings"].append(copy_op)
    image_data = generate_measurement_image(client_data)
    caption_text = f"Имя: {name}\nТелефон: {phone}\nАдрес: {address}"
    await context.bot.send_photo(chat_id=TARGET_CHAT_ID, photo=image_data, caption=caption_text)
    photo_overlays = []
    for i, op in enumerate(openings, start=1):
        for j, file_id in enumerate(op["photos"], start=1):
            overlay_text = f"Фото {j} проёма #{i} ({op['room']})"
            photo_overlays.append((file_id, overlay_text))
    if photo_overlays:
        await send_photos_with_overlay_as_album(context, TARGET_CHAT_ID, photo_overlays)
    keyboard = [[KeyboardButton("Новый замер")]]
    markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text("Замер успешно отправлен в рабочий чат. Вы можете начать новый замер.", reply_markup=markup)
    return MENU

# -------------------------------------------------------------------
# 10) РЕДАКТИРОВАНИЕ / УДАЛЕНИЕ
# -------------------------------------------------------------------
async def edit_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    openings = context.user_data.get("openings", [])
    if not openings:
        await update.message.reply_text("У вас нет добавленных проёмов.")
        return OPENING_MENU
    kb = []
    for i, op in enumerate(openings, start=1):
        kb.append([KeyboardButton(f"Проём {i}: {op['room']}")])
    markup = ReplyKeyboardMarkup(kb, resize_keyboard=True)
    await update.message.reply_text("Выберите проём для редактирования:", reply_markup=markup)
    return EDIT_CHOICE

async def edit_choice_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    parts = text.split()
    if len(parts) < 2 or parts[0] != "Проём":
        await update.message.reply_text("Неверный формат. Выберите проём из списка.")
        return EDIT_CHOICE
    number_str = parts[1].rstrip(":")
    try:
        number = int(number_str)
        index = number - 1
        openings = context.user_data["openings"]
        if index < 0 or index >= len(openings):
            raise ValueError
    except:
        await update.message.reply_text("Неверный выбор проёма.")
        return EDIT_CHOICE
    context.user_data["edit_index"] = index
    fields = [
        "Комната", "Тип двери", "Размеры", "Полотно",
        "Добор", "Кол-во доборов", "Наличники",
        "Порог", "Демонтаж", "Открывание", "Комментарий"
    ]
    kb = [[KeyboardButton(f)] for f in fields] + [[KeyboardButton("Готово")]]
    markup = ReplyKeyboardMarkup(kb, resize_keyboard=True)
    await update.message.reply_text("Выберите поле для изменения:", reply_markup=markup)
    return EDIT_FIELD

async def edit_field_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.lower()
    if text == "готово":
        return await opening_menu_return(update, context)
    field_map = {
        "комната": "room",
        "тип двери": "door_type",
        "размеры": "dimensions",
        "полотно": "canvas",
        "добор": "dobor",
        "кол-во доборов": "dobor_count",
        "наличники": "nalichniki",
        "порог": "threshold",
        "демонтаж": "demontage",
        "открывание": "opening",
        "комментарий": "comment"
    }
    if text not in field_map:
        await update.message.reply_text("Выберите поле из списка или 'Готово'.")
        return EDIT_FIELD
    context.user_data["edit_field"] = field_map[text]
    await update.message.reply_text(f"Введите новое значение для «{text}»:")
    return EDIT_VALUE

async def edit_value_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    new_value = update.message.text
    index = context.user_data["edit_index"]
    field = context.user_data["edit_field"]
    openings = context.user_data["openings"]
    openings[index][field] = new_value
    await update.message.reply_text(f"Поле «{field}» обновлено на: {new_value}.")
    fields = [
        "Комната", "Тип двери", "Размеры", "Полотно",
        "Добор", "Кол-во доборов", "Наличники",
        "Порог", "Демонтаж", "Открывание", "Комментарий"
    ]
    kb = [[KeyboardButton(f)] for f in fields] + [[KeyboardButton("Готово")]]
    markup = ReplyKeyboardMarkup(kb, resize_keyboard=True)
    await update.message.reply_text("Выберите поле для изменения ещё или нажмите 'Готово':", reply_markup=markup)
    return EDIT_FIELD

async def opening_menu_return(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [KeyboardButton("Следующий проём")],
        [KeyboardButton("Редактировать проём"), KeyboardButton("Удалить проём")],
        [KeyboardButton("Проверить и завершить")]
    ]
    markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text("Что делаем дальше?", reply_markup=markup)
    return OPENING_MENU

async def delete_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    openings = context.user_data.get("openings", [])
    if not openings:
        await update.message.reply_text("У вас нет добавленных проёмов.")
        return OPENING_MENU
    kb = []
    for i, op in enumerate(openings, start=1):
        kb.append([KeyboardButton(f"Проём {i}: {op['room']}")])
    markup = ReplyKeyboardMarkup(kb, resize_keyboard=True)
    await update.message.reply_text("Выберите проём для удаления:", reply_markup=markup)
    return DELETE_CHOICE

async def delete_choice_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    parts = text.split()
    if len(parts) < 2 or parts[0] != "Проём":
        await update.message.reply_text("Неверный формат. Выберите проём из списка.")
        return DELETE_CHOICE
    number_str = parts[1].rstrip(":")
    try:
        number = int(number_str)
        index = number - 1
        openings = context.user_data["openings"]
        if index < 0 or index >= len(openings):
            raise ValueError
    except:
        await update.message.reply_text("Неверный выбор проёма.")
        return DELETE_CHOICE
    context.user_data["delete_index"] = index
    proem = context.user_data["openings"][index]
    kb = [[KeyboardButton("Да, удалить"), KeyboardButton("Отмена")]]
    markup = ReplyKeyboardMarkup(kb, resize_keyboard=True)
    await update.message.reply_text(f"Вы уверены, что хотите удалить «Проём {number}: {proem['room']}»?", reply_markup=markup)
    return DELETE_CONFIRM

async def delete_confirm_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.lower()
    if text == "да, удалить":
        index = context.user_data["delete_index"]
        proem = context.user_data["openings"].pop(index)
        await update.message.reply_text(f"Проём «{proem['room']}» удалён.")
    else:
        await update.message.reply_text("Удаление отменено.")
    return await opening_menu_return(update, context)

# -------------------------------------------------------------------
# 11) /Отмена – кнопка "Отключить бот"
# -------------------------------------------------------------------
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Диалог отменён. Бот отключён. Для повторного запуска нажмите 'Запустить'.")
    return ConversationHandler.END

async def fallback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Непредвиденная команда или сообщение. Диалог завершён.\nНажмите 'Запустить', чтобы начать заново.")
    return ConversationHandler.END

# -------------------------------------------------------------------
# 12) ОСНОВНАЯ ФУНКЦИЯ АВТОРИЗАЦИИ – запуск и обработка контакта
# -------------------------------------------------------------------
async def start_auth(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Entry point для авторизации – вместо /start показываем кнопку "Запустить"
    keyboard = [
        [KeyboardButton(LAUNCH_TEXT)],
        [KeyboardButton(CANCEL_TEXT)]
    ]
    markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text("Нажмите 'Запустить' для авторизации.", reply_markup=markup)
    return MENU

async def menu_auth_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == LAUNCH_TEXT:
        return await auth_request(update, context)
    elif text == CANCEL_TEXT:
        return await cancel(update, context)
    else:
        await update.message.reply_text("Нажмите 'Запустить' для авторизации или 'Отключить бот' для отмены.")
        return MENU

# -------------------------------------------------------------------
# 13) ЭТАП АВТОРИЗАЦИИ: запрашиваем контакт
# -------------------------------------------------------------------
async def auth_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [KeyboardButton("Поделиться контактом", request_contact=True)],
        [KeyboardButton(CANCEL_TEXT)]
    ]
    markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text("Для авторизации поделитесь, пожалуйста, своим контактом.", reply_markup=markup)
    return AUTH

async def handle_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    contact = update.message.contact
    if not contact:
        await update.message.reply_text("Контакт не получен. Попробуйте ещё раз.")
        return AUTH
    # Нормализуем номер (оставляем только цифры)
    phone = re.sub(r"\D", "", contact.phone_number)
    if phone in ALLOWED_NUMBERS:
        name = ALLOWED_NUMBERS[phone]
        context.user_data["authorized_name"] = name
        greeting = f"Здравствуйте, {name}! Для начала замера нажмите 'Новый замер' или 'Отключить бот' для отмены."
        keyboard = [
            [KeyboardButton("Новый замер")],
            [KeyboardButton(CANCEL_TEXT)]
        ]
        markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        await update.message.reply_text(greeting, reply_markup=markup)
        return MENU
    else:
        await update.message.reply_text("Извините, ваш номер не найден в базе. Доступ закрыт.")
        return ConversationHandler.END

# -------------------------------------------------------------------
# 14) ОСНОВНАЯ ЛОГИКА ЗАМЕРА (остальные функции остаются без изменений)
# -------------------------------------------------------------------
async def menu_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == CANCEL_TEXT:
        return await cancel(update, context)
    if text == "Новый замер":
        context.user_data["openings"] = []
        context.user_data.pop("client_name", None)
        context.user_data.pop("client_phone", None)
        context.user_data.pop("client_address", None)
        await update.message.reply_text("Введите имя клиента:", reply_markup=ReplyKeyboardRemove())
        return GET_NAME
    else:
        await update.message.reply_text("Нажмите кнопку 'Новый замер'.")
        return MENU

async def get_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == CANCEL_TEXT:
        return await cancel(update, context)
    context.user_data["client_name"] = update.message.text
    await update.message.reply_text("Введите телефон клиента:")
    return GET_PHONE

async def get_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == CANCEL_TEXT:
        return await cancel(update, context)
    context.user_data["client_phone"] = update.message.text
    await update.message.reply_text("Введите адрес клиента:")
    return GET_ADDRESS

async def get_address(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == CANCEL_TEXT:
        return await cancel(update, context)
    context.user_data["client_address"] = update.message.text
    return await start_opening(update, context)

async def start_opening(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == CANCEL_TEXT:
        return await cancel(update, context)
    context.user_data["current_opening"] = {
        "room": "",
        "door_type": "",
        "dimensions": "",
        "canvas": "---",
        "dobor": "---",
        "dobor_count": "---",
        "nalichniki": "---",
        "threshold": "",
        "demontage": "",
        "opening": "---",
        "comment": "",
        "photos": []
    }
    await update.message.reply_text("Введите название комнаты (например, 'Кухня'):", reply_markup=ReplyKeyboardRemove())
    return ENTER_ROOM

async def enter_room(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == CANCEL_TEXT:
        return await cancel(update, context)
    context.user_data["current_opening"]["room"] = update.message.text
    door_types = [
        ["Межкомнатная дверь"],
        ["Скрытая дверь"],
        ["Входная дверь"],
        ["Облагораживание проема"],
        ["Складная дверь (книжка)"],
        ["Раздвижная дверь (одностворчатая)"],
        ["Раздвижная дверь (двустворчатая)"],
        ["Двустворчатая дверь (распашная)"],
        ["Иное"]
    ]
    door_types.append([CANCEL_TEXT])
    markup = ReplyKeyboardMarkup(door_types, resize_keyboard=True)
    await update.message.reply_text("Выберите тип двери:", reply_markup=markup)
    return ENTER_DOOR_TYPE

async def enter_door_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == CANCEL_TEXT:
        return await cancel(update, context)
    if text == "Иное":
        await update.message.reply_text("Введите ваш вариант типа двери:", reply_markup=ReplyKeyboardRemove())
        return ENTER_DOOR_TYPE_CUSTOM
    else:
        context.user_data["current_opening"]["door_type"] = text
        await update.message.reply_text("Введите размеры проёма (высота, ширина, толщина стены):", reply_markup=ReplyKeyboardRemove())
        return ENTER_DIMENSIONS

async def enter_door_type_custom(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == CANCEL_TEXT:
        return await cancel(update, context)
    context.user_data["current_opening"]["door_type"] = update.message.text
    await update.message.reply_text("Введите размеры проёма (высота, ширина, толщина стены):", reply_markup=ReplyKeyboardRemove())
    return ENTER_DIMENSIONS

async def enter_dimensions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == CANCEL_TEXT:
        return await cancel(update, context)
    context.user_data["current_opening"]["dimensions"] = update.message.text
    door_type = context.user_data["current_opening"]["door_type"]
    if door_type == "Облагораживание проема":
        context.user_data["current_opening"]["canvas"] = "---"
        return await ask_dobor(update, context)
    else:
        canvas_variants = [
            ["600", "700", "800", "Иное"],
            [CANCEL_TEXT]
        ]
        markup = ReplyKeyboardMarkup(canvas_variants, resize_keyboard=True)
        await update.message.reply_text("Введите рекомендуемое полотно:", reply_markup=markup)
        return ENTER_CANVAS

async def enter_canvas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == CANCEL_TEXT:
        return await cancel(update, context)
    if text == "Иное":
        await update.message.reply_text("Введите ваш вариант полотна:", reply_markup=ReplyKeyboardRemove())
        return ENTER_CANVAS
    else:
        context.user_data["current_opening"]["canvas"] = text
        return await ask_dobor(update, context)

async def ask_dobor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == CANCEL_TEXT:
        return await cancel(update, context)
    dobor_variants = [
        ["100 мм", "150 мм"],
        ["200 мм", "нет"],
        ["Иное"]
    ]
    dobor_variants.append([CANCEL_TEXT])
    markup = ReplyKeyboardMarkup(dobor_variants, resize_keyboard=True)
    await update.message.reply_text("Введите ширину добора:", reply_markup=markup)
    return ENTER_DOBOR

async def get_dobor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.lower()
    if text == CANCEL_TEXT.lower():
        return await cancel(update, context)
    if text == "иное":
        await update.message.reply_text("Введите ваш вариант добора:", reply_markup=ReplyKeyboardRemove())
        return ENTER_DOBOR_CUSTOM
    elif text in ["100 мм", "150 мм", "200 мм", "нет"]:
        context.user_data["current_opening"]["dobor"] = text
        if text == "нет":
            context.user_data["current_opening"]["dobor_count"] = "---"
            return await ask_nalichniki(update, context)
        else:
            dobor_count_variants = [
                ["1,5", "2,5", "3", "нет"],
                ["Иное"]
            ]
            dobor_count_variants.append([CANCEL_TEXT])
            markup = ReplyKeyboardMarkup(dobor_count_variants, resize_keyboard=True)
            await update.message.reply_text("Введите кол-во доборов:", reply_markup=markup)
            return ENTER_DOBOR_COUNT
    else:
        await update.message.reply_text("Выберите один из вариантов или 'Иное'.")
        return ENTER_DOBOR

async def enter_dobor_custom(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == CANCEL_TEXT:
        return await cancel(update, context)
    context.user_data["current_opening"]["dobor"] = update.message.text
    dobor_count_variants = [
        ["1,5", "2,5", "3", "нет"],
        ["Иное"]
    ]
    dobor_count_variants.append([CANCEL_TEXT])
    markup = ReplyKeyboardMarkup(dobor_count_variants, resize_keyboard=True)
    await update.message.reply_text("Введите кол-во доборов:", reply_markup=markup)
    return ENTER_DOBOR_COUNT

async def enter_dobor_count(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.lower()
    if text == CANCEL_TEXT.lower():
        return await cancel(update, context)
    if text == "иное":
        await update.message.reply_text("Введите ваш вариант кол-ва доборов:", reply_markup=ReplyKeyboardRemove())
        return ENTER_DOBOR_COUNT_CUSTOM
    elif text in ["1,5", "2,5", "3", "нет"]:
        context.user_data["current_opening"]["dobor_count"] = text
        return await ask_nalichniki(update, context)
    else:
        await update.message.reply_text("Выберите один из вариантов или 'Иное'.")
        return ENTER_DOBOR_COUNT

async def enter_dobor_count_custom(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == CANCEL_TEXT:
        return await cancel(update, context)
    context.user_data["current_opening"]["dobor_count"] = update.message.text
    return await ask_nalichniki(update, context)

async def ask_nalichniki(update: Update, context: ContextTypes.DEFAULT_TYPE):
    door_type = context.user_data["current_opening"]["door_type"]
    if door_type in ["Скрытая дверь", "Входная дверь"]:
        context.user_data["current_opening"]["nalichniki"] = "---"
        return await ask_threshold(update, context)
    else:
        variants = [
            ["2,5"],
            ["5"],
            ["5,5"],   # Добавленная кнопка "5,5"
            ["6"],
            ["нет"],
            ["Иное"]
        ]
        variants.append([CANCEL_TEXT])
        markup = ReplyKeyboardMarkup(variants, resize_keyboard=True)
        await update.message.reply_text("Введите кол-во наличников:", reply_markup=markup)
        return ENTER_NALICHNIKI_CHOICE

async def enter_nalichniki_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.lower()
    if text == CANCEL_TEXT.lower():
        return await cancel(update, context)
    if text == "иное":
        await update.message.reply_text("Введите ваш вариант кол-ва наличников:", reply_markup=ReplyKeyboardRemove())
        return ENTER_NALICHNIKI_CUSTOM
    elif text in ["2,5", "5", "5,5", "6", "нет"]:
        context.user_data["current_opening"]["nalichniki"] = text
        return await ask_threshold(update, context)
    else:
        await update.message.reply_text("Выберите один из вариантов или 'Иное'.")
        return ENTER_NALICHNIKI_CHOICE

async def enter_nalichniki_custom(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == CANCEL_TEXT:
        return await cancel(update, context)
    context.user_data["current_opening"]["nalichniki"] = update.message.text
    return await ask_threshold(update, context)

async def ask_threshold(update: Update, context: ContextTypes.DEFAULT_TYPE):
    door_type = context.user_data["current_opening"]["door_type"]
    if door_type == "Облагораживание проема":
        context.user_data["current_opening"]["threshold"] = "---"
        return await ask_demontage(update, context)
    else:
        btns = [
            ["да", "нет"],
            [CANCEL_TEXT]
        ]
        markup = ReplyKeyboardMarkup(btns, resize_keyboard=True)
        await update.message.reply_text("Наличие порога?", reply_markup=markup)
        return ENTER_THRESHOLD_CHOICE

async def threshold_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.lower()
    if text == CANCEL_TEXT.lower():
        return await cancel(update, context)
    if text in ["да", "нет"]:
        context.user_data["current_opening"]["threshold"] = text
        return await ask_demontage(update, context)
    else:
        await update.message.reply_text("Выберите 'да' или 'нет'.")
        return ENTER_THRESHOLD_CHOICE

async def ask_demontage(update: Update, context: ContextTypes.DEFAULT_TYPE):
    btns = [
        ["да", "нет"],
        [CANCEL_TEXT]
    ]
    markup = ReplyKeyboardMarkup(btns, resize_keyboard=True)
    await update.message.reply_text("Демонтаж старой двери?", reply_markup=markup)
    return ENTER_DEMONTAGE_CHOICE

async def demontage_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.lower()
    if text == CANCEL_TEXT.lower():
        return await cancel(update, context)
    if text not in ["да", "нет"]:
        await update.message.reply_text("Выберите 'да' или 'нет'.")
        return ENTER_DEMONTAGE_CHOICE
    context.user_data["current_opening"]["demontage"] = text
    return await ask_opening(update, context)

async def ask_opening(update: Update, context: ContextTypes.DEFAULT_TYPE):
    door_type = context.user_data["current_opening"]["door_type"]
    if door_type in ["Облагораживание проема", "Складная дверь (книжка)", "Раздвижная дверь (одностворчатая)",
                     "Раздвижная дверь (двустворчатая)", "Двустворчатая дверь (распашная)"]:
        context.user_data["current_opening"]["opening"] = "---"
        return await ask_comment(update, context)
    else:
        variants = [
            ["Левое", "Правое"],
            ["Левое рев.", "Правое рев."],
            ["Иное"]
        ]
        variants.append([CANCEL_TEXT])
        markup = ReplyKeyboardMarkup(variants, resize_keyboard=True)
        await update.message.reply_text("Введите открывание:", reply_markup=markup)
        return ENTER_OPENING_CHOICE

async def opening_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == CANCEL_TEXT:
        return await cancel(update, context)
    if text == "Иное":
        await update.message.reply_text("Введите ваш вариант открывания:", reply_markup=ReplyKeyboardRemove())
        return ENTER_OPENING_CUSTOM
    elif text in ["Левое", "Правое", "Левое рев.", "Правое рев."]:
        context.user_data["current_opening"]["opening"] = text
        return await ask_comment(update, context)
    else:
        await update.message.reply_text("Выберите один из вариантов или 'Иное'.")
        return ENTER_OPENING_CHOICE

async def opening_custom(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == CANCEL_TEXT:
        return await cancel(update, context)
    context.user_data["current_opening"]["opening"] = update.message.text
    return await ask_comment(update, context)

async def ask_comment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [KeyboardButton(SKIP_TEXT)],
        [CANCEL_TEXT]
    ]
    markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text("Введите комментарий или нажмите «Пропустить»:", reply_markup=markup)
    return ENTER_COMMENT

async def enter_comment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == CANCEL_TEXT:
        return await cancel(update, context)
    if text == SKIP_TEXT:
        context.user_data["current_opening"]["comment"] = ""
    else:
        context.user_data["current_opening"]["comment"] = text
    keyboard = [
        [KeyboardButton(DONE_TEXT)],
        [KeyboardButton(SKIP_TEXT)],
        [CANCEL_TEXT]
    ]
    markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text("Прикрепите фото (по одной). Когда закончите, нажмите «Готово». Если нет фото – «Пропустить».", reply_markup=markup)
    return ENTER_PHOTOS

async def enter_photos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == CANCEL_TEXT:
        return await cancel(update, context)
    if update.message.text in [SKIP_TEXT, DONE_TEXT]:
        return await save_opening(update, context)
    if update.message.photo:
        file_id = update.message.photo[-1].file_id
        context.user_data["current_opening"]["photos"].append(file_id)
        await update.message.reply_text("Фото сохранено. Можете отправить ещё, или нажмите «Готово».")
        return ENTER_PHOTOS
    else:
        await update.message.reply_text("Отправьте фото, либо нажмите «Готово» или «Пропустить».")
        return ENTER_PHOTOS

async def save_opening(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == CANCEL_TEXT:
        return await cancel(update, context)
    current = context.user_data["current_opening"]
    context.user_data["openings"].append(current)
    keyboard = [
        [KeyboardButton("Следующий проём")],
        [KeyboardButton("Редактировать проём"), KeyboardButton("Удалить проём")],
        [KeyboardButton("Проверить и завершить")],
        [CANCEL_TEXT]
    ]
    markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text("Проём сохранён. Что делаем дальше?", reply_markup=markup)
    return OPENING_MENU

async def handle_opening_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == CANCEL_TEXT:
        return await cancel(update, context)
    if text == "Следующий проём":
        return await start_opening(update, context)
    elif text == "Редактировать проём":
        return await edit_choice(update, context)
    elif text == "Удалить проём":
        return await delete_choice(update, context)
    elif text == "Проверить и завершить":
        return await check_measure(update, context)
    else:
        await update.message.reply_text("Выберите: «Следующий проём», «Редактировать проём», «Удалить проём» или «Проверить и завершить».")
        return OPENING_MENU

# -------------------------------------------------------------------
# ЭТАП ПРОВЕРКИ: "Проверить и завершить"
# -------------------------------------------------------------------
async def check_measure(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == CANCEL_TEXT:
        return await cancel(update, context)
    name = context.user_data.get("client_name", "")
    phone = context.user_data.get("client_phone", "")
    address = context.user_data.get("client_address", "")
    openings = context.user_data.get("openings", [])
    client_data = {
        "client_name": name,
        "client_phone": phone,
        "client_address": address,
        "openings": []
    }
    for op in openings:
        copy_op = dict(op)
        copy_op["photo"] = "есть" if copy_op["photos"] else "нет"
        client_data["openings"].append(copy_op)
    image_data = generate_measurement_image(client_data)
    caption_text = f"Имя: {name}\nТелефон: {phone}\nАдрес: {address}"
    await update.message.reply_photo(photo=image_data, caption=caption_text)
    keyboard = [
        [KeyboardButton("Редактировать замер")],
        [KeyboardButton("Завершить замер")],
        [CANCEL_TEXT]
    ]
    markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text("Проверьте замер. Если всё правильно – нажмите 'Завершить замер', иначе – 'Редактировать замер'.", reply_markup=markup)
    return CHECK_MEASURE

async def check_measure_response(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "Редактировать замер":
        return await edit_choice(update, context)
    elif text == "Завершить замер":
        return await confirm_finish(update, context)
    else:
        await update.message.reply_text("Пожалуйста, выберите 'Редактировать замер' или 'Завершить замер'.")
        return CHECK_MEASURE

async def confirm_finish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == CANCEL_TEXT:
        return await cancel(update, context)
    name = context.user_data.get("client_name", "")
    phone = context.user_data.get("client_phone", "")
    address = context.user_data.get("client_address", "")
    openings = context.user_data.get("openings", [])
    client_data = {
        "client_name": name,
        "client_phone": phone,
        "client_address": address,
        "openings": []
    }
    for op in openings:
        copy_op = dict(op)
        copy_op["photo"] = "есть" if copy_op["photos"] else "нет"
        client_data["openings"].append(copy_op)
    image_data = generate_measurement_image(client_data)
    caption_text = f"Имя: {name}\nТелефон: {phone}\nАдрес: {address}"
    await context.bot.send_photo(chat_id=TARGET_CHAT_ID, photo=image_data, caption=caption_text)
    photo_overlays = []
    for i, op in enumerate(openings, start=1):
        for j, file_id in enumerate(op["photos"], start=1):
            overlay_text = f"Фото {j} проёма #{i} ({op['room']})"
            photo_overlays.append((file_id, overlay_text))
    if photo_overlays:
        await send_photos_with_overlay_as_album(context, TARGET_CHAT_ID, photo_overlays)
    keyboard = [[KeyboardButton("Новый замер")]]
    markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text("Замер успешно отправлен в рабочий чат. Вы можете начать новый замер.", reply_markup=markup)
    return MENU

# -------------------------------------------------------------------
# РЕДАКТИРОВАНИЕ / УДАЛЕНИЕ
# -------------------------------------------------------------------
async def edit_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    openings = context.user_data.get("openings", [])
    if not openings:
        await update.message.reply_text("У вас нет добавленных проёмов.")
        return OPENING_MENU
    kb = []
    for i, op in enumerate(openings, start=1):
        kb.append([KeyboardButton(f"Проём {i}: {op['room']}")])
    markup = ReplyKeyboardMarkup(kb, resize_keyboard=True)
    await update.message.reply_text("Выберите проём для редактирования:", reply_markup=markup)
    return EDIT_CHOICE

async def edit_choice_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    parts = text.split()
    if len(parts) < 2 or parts[0] != "Проём":
        await update.message.reply_text("Неверный формат. Выберите проём из списка.")
        return EDIT_CHOICE
    number_str = parts[1].rstrip(":")
    try:
        number = int(number_str)
        index = number - 1
        openings = context.user_data["openings"]
        if index < 0 or index >= len(openings):
            raise ValueError
    except:
        await update.message.reply_text("Неверный выбор проёма.")
        return EDIT_CHOICE
    context.user_data["edit_index"] = index
    fields = [
        "Комната", "Тип двери", "Размеры", "Полотно",
        "Добор", "Кол-во доборов", "Наличники",
        "Порог", "Демонтаж", "Открывание", "Комментарий"
    ]
    kb = [[KeyboardButton(f)] for f in fields] + [[KeyboardButton("Готово")]]
    markup = ReplyKeyboardMarkup(kb, resize_keyboard=True)
    await update.message.reply_text("Выберите поле для изменения:", reply_markup=markup)
    return EDIT_FIELD

async def edit_field_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.lower()
    if text == "готово":
        return await opening_menu_return(update, context)
    field_map = {
        "комната": "room",
        "тип двери": "door_type",
        "размеры": "dimensions",
        "полотно": "canvas",
        "добор": "dobor",
        "кол-во доборов": "dobor_count",
        "наличники": "nalichniki",
        "порог": "threshold",
        "демонтаж": "demontage",
        "открывание": "opening",
        "комментарий": "comment"
    }
    if text not in field_map:
        await update.message.reply_text("Выберите поле из списка или 'Готово'.")
        return EDIT_FIELD
    context.user_data["edit_field"] = field_map[text]
    await update.message.reply_text(f"Введите новое значение для «{text}»:")
    return EDIT_VALUE

async def edit_value_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    new_value = update.message.text
    index = context.user_data["edit_index"]
    field = context.user_data["edit_field"]
    openings = context.user_data["openings"]
    openings[index][field] = new_value
    await update.message.reply_text(f"Поле «{field}» обновлено на: {new_value}.")
    fields = [
        "Комната", "Тип двери", "Размеры", "Полотно",
        "Добор", "Кол-во доборов", "Наличники",
        "Порог", "Демонтаж", "Открывание", "Комментарий"
    ]
    kb = [[KeyboardButton(f)] for f in fields] + [[KeyboardButton("Готово")]]
    markup = ReplyKeyboardMarkup(kb, resize_keyboard=True)
    await update.message.reply_text("Выберите поле для изменения ещё или нажмите 'Готово':", reply_markup=markup)
    return EDIT_FIELD

async def opening_menu_return(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [KeyboardButton("Следующий проём")],
        [KeyboardButton("Редактировать проём"), KeyboardButton("Удалить проём")],
        [KeyboardButton("Проверить и завершить")]
    ]
    markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text("Что делаем дальше?", reply_markup=markup)
    return OPENING_MENU

async def delete_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    openings = context.user_data.get("openings", [])
    if not openings:
        await update.message.reply_text("У вас нет добавленных проёмов.")
        return OPENING_MENU
    kb = []
    for i, op in enumerate(openings, start=1):
        kb.append([KeyboardButton(f"Проём {i}: {op['room']}")])
    markup = ReplyKeyboardMarkup(kb, resize_keyboard=True)
    await update.message.reply_text("Выберите проём для удаления:", reply_markup=markup)
    return DELETE_CHOICE

async def delete_choice_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    parts = text.split()
    if len(parts) < 2 or parts[0] != "Проём":
        await update.message.reply_text("Неверный формат. Выберите проём из списка.")
        return DELETE_CHOICE
    number_str = parts[1].rstrip(":")
    try:
        number = int(number_str)
        index = number - 1
        openings = context.user_data["openings"]
        if index < 0 or index >= len(openings):
            raise ValueError
    except:
        await update.message.reply_text("Неверный выбор проёма.")
        return DELETE_CHOICE
    context.user_data["delete_index"] = index
    proem = context.user_data["openings"][index]
    kb = [[KeyboardButton("Да, удалить"), KeyboardButton("Отмена")]]
    markup = ReplyKeyboardMarkup(kb, resize_keyboard=True)
    await update.message.reply_text(f"Вы уверены, что хотите удалить «Проём {number}: {proem['room']}»?", reply_markup=markup)
    return DELETE_CONFIRM

async def delete_confirm_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.lower()
    if text == "да, удалить":
        index = context.user_data["delete_index"]
        proem = context.user_data["openings"].pop(index)
        await update.message.reply_text(f"Проём «{proem['room']}» удалён.")
    else:
        await update.message.reply_text("Удаление отменено.")
    return await opening_menu_return(update, context)

# -------------------------------------------------------------------
# 13) ENTRY-POINT И ОБЪЕДИНЕНИЕ ВСЕХ ЭТАПОВ
# -------------------------------------------------------------------
def main():
    request = HTTPXRequest(connect_timeout=60.0, read_timeout=60.0)
    app = Application.builder().token(TOKEN).request(request).build()

    conv_handler = ConversationHandler(
        entry_points=[
            # Начало работы: пользователь может нажать кнопку "Запустить" или использовать /start
            MessageHandler(filters.Regex(f"^{LAUNCH_TEXT}$"), auth_request),
            CommandHandler("start", show_auth_menu)
        ],
        states={
            MENU: [
                MessageHandler(filters.Regex(f"^{LAUNCH_TEXT}$"), auth_request),
                MessageHandler(filters.TEXT & ~filters.COMMAND, menu_choice)
            ],
            AUTH: [MessageHandler(filters.CONTACT, handle_contact)],
            GET_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_name)],
            GET_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_phone)],
            GET_ADDRESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_address)],
            ENTER_ROOM: [MessageHandler(filters.TEXT & ~filters.COMMAND, enter_room)],
            ENTER_DOOR_TYPE: [MessageHandler(filters.TEXT & ~filters.COMMAND, enter_door_type)],
            ENTER_DOOR_TYPE_CUSTOM: [MessageHandler(filters.TEXT & ~filters.COMMAND, enter_door_type_custom)],
            ENTER_DIMENSIONS: [MessageHandler(filters.TEXT & ~filters.COMMAND, enter_dimensions)],
            ENTER_CANVAS: [MessageHandler(filters.TEXT & ~filters.COMMAND, enter_canvas)],
            ENTER_DOBOR: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_dobor)],
            ENTER_DOBOR_CUSTOM: [MessageHandler(filters.TEXT & ~filters.COMMAND, enter_dobor_custom)],
            ENTER_DOBOR_COUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, enter_dobor_count)],
            ENTER_DOBOR_COUNT_CUSTOM: [MessageHandler(filters.TEXT & ~filters.COMMAND, enter_dobor_count_custom)],
            ENTER_NALICHNIKI_CHOICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, enter_nalichniki_choice)],
            ENTER_NALICHNIKI_CUSTOM: [MessageHandler(filters.TEXT & ~filters.COMMAND, enter_nalichniki_custom)],
            ENTER_THRESHOLD_CHOICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, threshold_choice)],
            ENTER_DEMONTAGE_CHOICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, demontage_choice)],
            ENTER_OPENING_CHOICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, opening_choice)],
            ENTER_OPENING_CUSTOM: [MessageHandler(filters.TEXT & ~filters.COMMAND, opening_custom)],
            ENTER_COMMENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, enter_comment)],
            ENTER_PHOTOS: [
                MessageHandler(filters.PHOTO, enter_photos),
                MessageHandler(filters.Regex(f"^{DONE_TEXT}$"), enter_photos),
                MessageHandler(filters.Regex(f"^{SKIP_TEXT}$"), enter_photos),
                MessageHandler(filters.TEXT & ~filters.COMMAND, enter_photos)
            ],
            OPENING_MENU: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_opening_menu)],
            CHECK_MEASURE: [MessageHandler(filters.TEXT & ~filters.COMMAND, check_measure_response)],
            EDIT_CHOICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_choice_handler)],
            EDIT_FIELD: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_field_handler)],
            EDIT_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_value_handler)],
            DELETE_CHOICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, delete_choice_handler)],
            DELETE_CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, delete_confirm_handler)]
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            MessageHandler(filters.COMMAND, fallback)
        ]
    )

    app.add_handler(conv_handler)
    app.run_polling()

if __name__ == "__main__":
    main()
