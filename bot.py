import asyncio
import logging
import aiosqlite
from datetime import datetime
import json
import hmac
import hashlib
import urllib.parse

from aiogram import Bot, Dispatcher, Router, F, types
from aiogram.filters import CommandStart, StateFilter, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    CallbackQuery,
    Document,
    FSInputFile,
    BotCommand,
    BotCommandScopeDefault,
    BotCommandScopeChat,
    WebAppInfo
)

from aiohttp import web
import aiohttp_cors

# --- Конфигурация ---
# !!! НЕ ЗАБУДЬТЕ СМЕНИТЬ ТОКЕН НА НОВЫЙ !!!
BOT_TOKEN = "8013022321:AAGhzkK4PdxUhIERIJ_VhinG3D9ffdNHWgc"
ADMIN_CHAT_ID = -1002188124654
MAIN_CHAT_ID = -1002777829971
DB_FILE = "bot.db"
ADMIN_IDS = [370144165]  # <-- ВАЖНО: ЗАМЕНИТЕ ЭТО НА СВОЙ ID
REFERRAL_REWARD = 100

# URL, куда вы загрузите ваши index.html, style.css, app.js
# (Пока можно оставить так, но для работы /profile его нужно будет заменить)
WEB_APP_URL = "https://www.helpers.ltd/" 

# Адрес для запуска локального веб-сервера
WEB_SERVER_HOST = "127.0.0.1"
WEB_SERVER_PORT = 8080
# ---------------------

# Настройка логирования
logging.basicConfig(level=logging.INFO)

# Объекты
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
router = Router()
dp.include_router(router)

# --- БЛОК: РАБОТА С БАЗОЙ ДАННЫХ ---
# (Тут ничего не меняется, просто копируем)
async def init_db():
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER UNIQUE NOT NULL,
                username TEXT, full_name TEXT, experience TEXT,
                status TEXT NOT NULL DEFAULT 'new', 
                join_date DATETIME NOT NULL, decision_date DATETIME,
                referrer_id INTEGER, referral_count INTEGER NOT NULL DEFAULT 0,
                balance INTEGER NOT NULL DEFAULT 0
            )
        """)
        await db.commit()
async def db_update_anket(user_id: int, full_name: str, experience: str):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("UPDATE users SET full_name = ?, experience = ?, status = 'pending' WHERE user_id = ?", (full_name, experience, user_id))
        await db.commit()
async def db_update_status(user_id: int, status: str):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("UPDATE users SET status = ?, decision_date = ? WHERE user_id = ?", (status, datetime.now(), user_id))
        await db.commit()
# --- КОНЕЦ БЛОКА БД ---

# --- БЛОК: УСТАНОВКА КОМАНД МЕНЮ ---
async def set_bot_commands(bot_instance: Bot): # Здесь имя bot_instance не важно, т.к. мы его передаем явно
    default_commands = [
        BotCommand(command="start", description="🚀 Перезапустить бота"),
        BotCommand(command="myrefs", description="🤝 Моя реф. ссылка"),
        BotCommand(command="profile", description="💎 Мой Профиль (Mini App)")
    ]
    await bot_instance.set_my_commands(commands=default_commands, scope=BotCommandScopeDefault())
    admin_commands = default_commands + [
        BotCommand(command="admin", description="📊 Админ: Статистика")
    ]
    for admin_id in ADMIN_IDS:
        try:
            await bot_instance.set_my_commands(commands=admin_commands, scope=BotCommandScopeChat(chat_id=admin_id))
        except Exception as e:
            logging.error(f"Не удалось установить команды для админа {admin_id}: {e}")
# --- КОНЕЦ БЛОКА КОМАНД ---

# --- БЛОК: ВЕБ-СЕРВЕР И АВТОРИЗАЦИЯ MINI APP ---
# (Тут ничего не меняется, просто копируем)
def is_valid_initdata(init_data: str, bot_token: str) -> (bool, dict | None):
    try:
        parsed_data = urllib.parse.parse_qs(init_data)
        hash_str = parsed_data.pop('hash', [None])[0]
        if not hash_str: return False, None
        data_check_string = "\n".join([f"{k}={v[0]}" for k, v in sorted(parsed_data.items())])
        secret_key = hmac.new("WebAppData".encode(), bot_token.encode(), hashlib.sha256).digest()
        calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        if calculated_hash != hash_str: return False, None
        user_data = parsed_data.get('user', [None])[0]
        if not user_data: return False, None
        return True, json.loads(user_data)
    except Exception as e:
        logging.error(f"Ошибка валидации initData: {e}")
        return False, None

async def handle_get_user_data(request: web.Request):
    try:
        data = await request.json()
        init_data = data.get('initData')
        if not init_data: return web.json_response({"error": "No initData"}, status=400)
        is_valid, user_data = is_valid_initdata(init_data, BOT_TOKEN)
        if not is_valid: return web.json_response({"error": "Invalid validation"}, status=401)
        user_id = user_data.get('id')
        if not user_id: return web.json_response({"error": "No user ID"}, status=400)
        
        async with aiosqlite.connect(DB_FILE) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT balance, join_date FROM users WHERE user_id = ?", (user_id,)) as cursor:
                user_db_data = await cursor.fetchone()
        if not user_db_data: return web.json_response({"error": "User not found in DB"}, status=404)
        
        bot_info = await bot.get_me()
        referral_link = f"https://t.me/{bot_info.username}?start={user_id}"
        
        response_data = {"balance": user_db_data['balance'], "join_date": user_db_data['join_date'], "ref_link": referral_link}
        return web.json_response(response_data)
    except Exception as e:
        logging.error(f"Ошибка в handle_get_user_data: {e}")
        return web.json_response({"error": "Internal server error"}, status=500)
# --- КОНЕЦ БЛОКА ВЕБ-СЕРВЕРА ---

# ---
# --- ВСТАВЬТЕ СЮДА ВСЕ ВАШИ ХЭНДЛЕРЫ ---
# --- 1. Определение "Состояний" (FSM) ---
class AnketStates(StatesGroup):
    waiting_for_name = State()
    waiting_for_experience = State()
    waiting_for_cv = State()


# --- 2. Хэндлер на команду /start (Ловит рефералов) ---
@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    
    referrer_id = None
    try:
        # Пытаемся достать ID из команды (например, /start 12345678)
        referrer_id = int(message.text.split()[1])
        if referrer_id == message.from_user.id:
            referrer_id = None # Нельзя пригласить самого себя
    except (IndexError, ValueError, TypeError):
        pass # У юзера обычный /start, без реферала

    # Добавляем пользователя и его реферера в БД
    async with aiosqlite.connect(DB_FILE) as db:
        try:
            # balance и referral_count по умолчанию 0, так что их не указываем
            await db.execute(
                "INSERT INTO users (user_id, username, join_date, referrer_id) VALUES (?, ?, ?, ?)",
                (message.from_user.id, message.from_user.username, datetime.now(), referrer_id)
            )
            await db.commit()
        except aiosqlite.IntegrityError:
            pass # Пользователь уже есть, не обновляем реферера

    
    start_button = InlineKeyboardButton(
        text="➡️ Подать заявку",
        callback_data="start_anket"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[start_button]])
    
    # Отправляем фото из папки
    photo_file = FSInputFile("welcome.jpg") 

    try:
        await message.answer_photo(
            photo=photo_file,
            caption=(
                "Здравствуйте!\n\n"
                "Вы подаете заявку на вступление в Helpers Community — закрытое "
                "профессиональное сообщество ассистентов.\n\n"
                "Чтобы поддерживать высокое качество нетворкинга и контента, "
                "мы не пускаем в чат ботов, спам и случайных людей. "
                "Для входа необходимо пройти быструю верификацию.\n\n"
                "Это займет 2 минуты.\n"
                "Готовы начать?"
            ),
            reply_markup=keyboard
        )
    except Exception as e:
        logging.error(f"Ошибка при отправке фото: {e}. Убедитесь, что 'welcome.jpg' лежит в папке.")
        await message.answer("Ошибка: не могу загрузить стартовое фото. Свяжитесь с админом.")


# --- 3. Хэндлер на нажатие кнопки "Подать заявку" ---
@router.callback_query(F.data == "start_anket")
async def anket_start(callback: CallbackQuery, state: FSMContext):
    if callback.message.photo:
        await callback.message.edit_reply_markup(reply_markup=None)
    else:
        # На случай, если фото не загрузилось
        await callback.message.edit_text(callback.message.text, reply_markup=None)
    
    await callback.message.answer(
        "Отлично. Давайте знакомиться.\n\n"
        "Как к вам обращаться? (Напишите, пожалуйста, реальные Имя и Фамилию)"
    )
    await state.set_state(AnketStates.waiting_for_name)
    await callback.answer()


# --- 4. Хэндлер, который ловит Имя ---
@router.message(StateFilter(AnketStates.waiting_for_name))
async def name_received(message: Message, state: FSMContext):
    await state.update_data(name=message.text)
    
    exp_buttons = [
        [InlineKeyboardButton(text="Я новичок (ищу первую работу)", callback_data="exp_newbie")],
        [InlineKeyboardButton(text="Менее 1 года", callback_data="exp_less_1")],
        [InlineKeyboardButton(text="1-3 года", callback_data="exp_1_3")],
        [InlineKeyboardButton(text="3+ года (Pro)", callback_data="exp_3_plus")],
    ]
    keyboard = InlineKeyboardMarkup(inline_keyboard=exp_buttons)

    await message.answer(
        f"Приятно познакомиться, {message.text}.\n\n"
        "Какой у вас сейчас опыт работы ассистентом?",
        reply_markup=keyboard
    )
    await state.set_state(AnketStates.waiting_for_experience)


# --- 5. Хэндлер, который ловит Опыт ---
@router.callback_query(StateFilter(AnketStates.waiting_for_experience), F.data.startswith("exp_"))
async def experience_received(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_reply_markup(reply_markup=None)
    
    experience_text = "Неизвестно"
    if callback.data == "exp_newbie": experience_text = "Я новичок (ищу первую работу)"
    elif callback.data == "exp_less_1": experience_text = "Менее 1 года"
    elif callback.data == "exp_1_3": experience_text = "1-3 года"
    elif callback.data == "exp_3_plus": experience_text = "3+ года (Pro)"

    await state.update_data(experience=experience_text)

    await callback.message.answer(
        "Понял. Теперь главный шаг.\n\n"
        "Пожалуйста, прикрепите ваше резюме (CV) в формате PDF или .docx.\n\n"
        "Если у вас нет резюме:\n"
        "Напишите вместо этого 3-5 предложений о себе в одном сообщении:\n"
        "• С какими задачами работали (календарь, тревел, документы)?\n"
        "• Какими инструментами владеете (Notion, AI, Google Workspace)?\n"
        "• Зачем хотите вступить в комьюнити?\n\n"
        "Мы не будем публиковать это. Эта информация нужна только админу для одобрения заявки."
    )
    await state.set_state(AnketStates.waiting_for_cv)
    await callback.answer()


# --- 6. Хэндлер, который ловит Резюме ---
@router.message(StateFilter(AnketStates.waiting_for_cv), (F.text | F.document))
async def cv_received(message: Message, state: FSMContext):
    data = await state.get_data()
    user_name = data.get("name")
    user_experience = data.get("experience")
    
    # Записываем анкету в БД со статусом 'pending'
    await db_update_anket(message.from_user.id, user_name, user_experience)
    
    admin_message_text = (
        f"Новая заявка!\n\n"
        f"ID: {message.from_user.id}\n"
        f"Username: @{message.from_user.username or 'не указан'}\n"
        f"Имя: {user_name}\n"
        f"Опыт: {user_experience}\n\n"
    )
    
    approve_button = InlineKeyboardButton(
        text="✅ Одобрить",
        callback_data=f"approve:{message.from_user.id}:{user_name}"
    )
    reject_button = InlineKeyboardButton(
        text="🚫 Отклонить",
        callback_data=f"reject:{message.from_user.id}:{user_name}"
    )
    admin_keyboard = InlineKeyboardMarkup(inline_keyboard=[[approve_button, reject_button]])

    try:
        # Отправляем в админский чат
        sent_message = await bot.send_message(
            ADMIN_CHAT_ID, admin_message_text, reply_markup=admin_keyboard
        )
        if message.document:
            await bot.send_document(
                ADMIN_CHAT_ID, message.document.file_id, reply_to_message_id=sent_message.message_id 
            )
        elif message.text:
            await bot.send_message(
                ADMIN_CHAT_ID, f"Резюме (текстом):\n\n{message.text}", reply_to_message_id=sent_message.message_id
            )
    except Exception as e:
        logging.error(f"Ошибка отправки админу: {e}")
        await message.answer("Произошла ошибка при отправке заявки. Свяжитесь с админом.")
        await state.clear()
        return

    # Сообщение "Зал ожидания"
    await message.answer(
        "Принято!\n\n"
        "Ваша заявка отправлена на ручную проверку.\n\n"
        "🗓️ **Что дальше:**\n"
        "Админ рассмотрит вашу заявку (обычно это занимает от 2 до 24 часов в будние дни).\n"
        "Я напишу вам сюда, как только будет решение.\n\n"
        "Пока вы ждете, вот что полезного есть у Helpers:\n"
        "• [Наш блог/сайт] — (https://...)\n" # <-- Вставьте свои ссылки
        "• [Наша Академия] — (https://...)\n\n" # <-- Вставьте свои ссылки
        "Не закрывайте этот диалог, я скоро вернусь с ответом.",
        disable_web_page_preview=True
    )
    await state.clear()


# --- 7. Хэндлеры для Админа (нажатия кнопок) ---
@router.callback_query(F.data.startswith("approve:"))
async def approve_user(callback: CallbackQuery):
    try:
        data_parts = callback.data.split(":")
        user_id = int(data_parts[1])
        user_name = ":".join(data_parts[2:])
    except (IndexError, ValueError):
        await callback.answer("Ошибка: не удалось распознать ID.", show_alert=True)
        return

    try:
        invite_link = await bot.create_chat_invite_link(chat_id=MAIN_CHAT_ID, member_limit=1)
        
        # Сообщение юзеру
        await bot.send_message(
            user_id,
            f"Здравствуйте, {user_name}!\n\n"
            "Отличные новости: ваша заявка одобрена. Добро пожаловать в Helpers Community!\n\n"
            f"🔑 Вот ваша персональная ссылка-приглашение:\n"
            f"{invite_link.invite_link}\n\n"
            "Ссылка активна 24 часа и предназначена только для вас.\n\n"
            "Увидимся в комьюнити!"
        )
        
        # Обновляем сообщение в админ-чате
        await callback.message.edit_text(
            callback.message.text + f"\n\n✅ ОДОБРЕНО (админом @{callback.from_user.username or 'N/A'})",
            reply_markup=None
        )
        
        # Обновляем статус в БД
        await db_update_status(user_id, 'approved')
        
        await callback.answer("Заявка одобрена.", show_alert=True)

        # --- Начисляем Токены рефереру ---
        async with aiosqlite.connect(DB_FILE) as db:
            db.row_factory = aiosqlite.Row
            # 1. Находим реферера этого юзера
            async with db.execute("SELECT referrer_id FROM users WHERE user_id = ?", (user_id,)) as cursor:
                result = await cursor.fetchone()
                
            if result and result['referrer_id']:
                referrer_id = result['referrer_id']
                # 2. Обновляем счетчик И БАЛАНС рефереру
                await db.execute(
                    "UPDATE users SET referral_count = referral_count + 1, balance = balance + ? WHERE user_id = ?",
                    (REFERRAL_REWARD, referrer_id) # Передаем сумму награды
                )
                await db.commit()
                logging.info(f"Начислен +1 реферал и +{REFERRAL_REWARD} токенов пользователю {referrer_id}")

    except Exception as e:
        logging.error(f"Ошибка при одобрении {user_id}: {e}")
        await callback.answer(f"Ошибка! {e}", show_alert=True)


@router.callback_query(F.data.startswith("reject:"))
async def reject_user(callback: CallbackQuery):
    try:
        data_parts = callback.data.split(":")
        user_id = int(data_parts[1])
        user_name = ":".join(data_parts[2:])
    except (IndexError, ValueError):
        await callback.answer("Ошибка: не удалось распознать ID.", show_alert=True)
        return

    try:
        # Сообщение юзеру
        await bot.send_message(
            user_id,
            f"Здравствуйте, {user_name}.\n\n"
            "Админ рассмотрел вашу заявку. К сожалению, сейчас мы не можем ее одобрить.\n"
            "Наше основное комьюнити сейчас сфокусировано на ассистентах с опытом от [X] лет / [причина отказа].\n\n" # <-- Укажите причину
            "**НО!**\n"
            "Судя по вашей анкете, вам идеально подойдет наша Академия Helpers. "
            "Там вы сможете быстро получить необходимые навыки, и все наши "
            "выпускники получают гарантированный доступ в комьюнити.\n\n"
            "👉 [Ссылка на описание Академии]\n\n" # <-- Вставьте ссылку
            "Спасибо за ваш интерес!"
        )
        
        # Обновляем сообщение в админ-чате
        await callback.message.edit_text(
            callback.message.text + f"\n\n🚫 ОТКЛОНЕНО (админом @{callback.from_user.username or 'N/A'})",
            reply_markup=None
        )
        
        # Обновляем статус в БД
        await db_update_status(user_id, 'rejected')
        
        await callback.answer("Заявка отклонена.", show_alert=True)
    except Exception as e:
        logging.error(f"Ошибка при отклонении {user_id}: {e}")
        await callback.answer(f"Ошибка! {e}", show_alert=True)


# --- 8. БЛОК: АНАЛИТИКА (Только для Админов) ---
@router.message(Command("admin"), F.from_user.id.in_(ADMIN_IDS))
async def cmd_admin_stats(message: Message):
    async with aiosqlite.connect(DB_FILE) as db:
        db.row_factory = aiosqlite.Row 
        async with db.execute("SELECT status, COUNT(id) as count FROM users GROUP BY status") as cursor:
            stats = await cursor.fetchall()

        async with db.execute("SELECT COUNT(id) as total FROM users") as cursor:
            total = await cursor.fetchone()

    # Форматируем статистику
    stats_dict = {row['status']: row['count'] for row in stats}
    total_users = total['total']
    approved = stats_dict.get('approved', 0)
    rejected = stats_dict.get('rejected', 0)
    pending = stats_dict.get('pending', 0)
    new = stats_dict.get('new', 0) # Те, кто только нажал /start

    text = (
        f"📊 **Статистика Helpers Community**\n\n"
        f"**Всего пользователей:** {total_users}\n"
        f"------------------------------\n"
        f"✅ **Одобрено:** {approved}\n"
        f"🚫 **Отклонено:** {rejected}\n"
        f"⏳ **В ожидании (заполнили анкету):** {pending}\n"
        f"🆕 **Новые (только нажали /start):** {new}"
    )
    
    await message.answer(text, parse_mode="Markdown")

# --- 9. БЛОК: РЕФЕРАЛЬНАЯ ПРОГРАММА (Для всех) ---
@router.message(Command("myrefs"))
async def cmd_my_referrals(message: Message):
    try:
        # Получаем имя бота (чтобы ссылка была красивой)
        bot_info = await bot.get_me()
        bot_username = bot_info.username
        
        referral_link = f"https://t.me/{bot_username}?start={message.from_user.id}"
        
        # Получаем кол-во рефералов И баланс из БД
        async with aiosqlite.connect(DB_FILE) as db:
            async with db.execute("SELECT referral_count, balance FROM users WHERE user_id = ?", (message.from_user.id,)) as cursor:
                result = await cursor.fetchone()
                
        referral_count = result[0] if result else 0
        balance = result[1] if result else 0

        text = (
            f"🤝 **Ваша реферальная программа**\n\n"
            f"Приглашайте коллег в наше комьюнити! "
            f"За каждого одобренного участника вы получите **{REFERRAL_REWARD} токенов**.\n\n"
            f"**Ваша ссылка:**\n"
            f"`{referral_link}`\n"
            f"(Нажмите, чтобы скопировать)\n\n"
            f"--- **Ваши успехи** ---\n"
            f"**Приглашено (одобрено):** {referral_count} чел.\n"
            f"**Ваш баланс:** {balance} 💎 токенов"
        )
        
        await message.answer(text, parse_mode="Markdown")
    except Exception as e:
        logging.error(f"Ошибка в /myrefs: {e}")
        await message.answer("Произошла ошибка при генерации реферальной ссылки.")

# --- НОВЫЙ ХЭНДЛЕР: Кнопка "Мой Профиль" ---
@router.message(Command("profile"))
async def cmd_profile(message: Message):
    profile_button = InlineKeyboardButton(
        text="💎 Открыть Мой Профиль",
        web_app=WebAppInfo(url=WEB_APP_URL)
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[profile_button]])
    await message.answer(
        "Нажмите кнопку ниже, чтобы открыть свой профиль, "
        "посмотреть баланс токенов и получить реферальную ссылку.",
        reply_markup=keyboard
    )
# --- КОНЕЦ НОВОГО ХЭНДЛЕРА ---


# --- БЛОК: ЗАПУСК БОТА И СЕРВЕРА ---

# --- ИСПРАВЛЕНИЕ ЗДЕСЬ ---
# Я переименовал 'bot_instance' -> 'bot', чтобы соответствовать тому,
# как aiogram передает этот аргумент.
async def on_startup(dispatcher: Dispatcher, bot: Bot, app: web.Application):
    """Выполняется при старте бота"""
    # 1. Инициализируем БД
    await init_db()
    # 2. Устанавливаем команды меню
    await set_bot_commands(bot) # <-- Используем 'bot'
    
    # 3. Запускаем веб-сервер
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, WEB_SERVER_HOST, WEB_SERVER_PORT)
    await site.start()
    
    # Сохраняем runner, чтобы потом его остановить
    dispatcher['web_runner'] = runner
    logging.info(f"Веб-сервер запущен на http://{WEB_SERVER_HOST}:{WEB_SERVER_PORT}")
# --- КОНЕЦ ИСПРАВЛЕНИЯ ---

async def on_shutdown(dispatcher: Dispatcher):
    """Выполняется при остановке бота"""
    logging.info("Остановка веб-сервера...")
    if 'web_runner' in dispatcher:
        await dispatcher['web_runner'].cleanup()
    logging.info("Бот остановлен.")

async def main():
    app = web.Application()
    cors = aiohttp_cors.setup(app, defaults={
        "*": aiohttp_cors.ResourceOptions(
            allow_credentials=True, expose_headers="*",
            allow_headers="*", allow_methods="*",
        )
    })
    route = app.router.add_post("/get_user_data", handle_get_user_data)
    cors.add(route)

    # Регистрируем startup/shutdown
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    try:
        logging.info("Бот запускается в режиме polling...")
        await bot.delete_webhook(drop_pending_updates=True)
        # Передаем 'app' в start_polling, чтобы он был доступен в on_startup
        await dp.start_polling(bot, app=app) 
    finally:
        await bot.session.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Бот остановлен вручную.")