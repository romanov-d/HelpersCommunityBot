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
    WebAppInfo  # <-- НОВОЕ
)

# --- НОВЫЕ ИМПОРТЫ ДЛЯ ВЕБ-СЕРВЕРА ---
from aiohttp import web
import aiohttp_cors

# --- Конфигурация ---
# !!! НЕ ЗАБУДЬТЕ СМЕНИть ТОКЕН НА НОВЫЙ !!!
BOT_TOKEN = "8013022321:AAGhzkK4PdxUhIERIJ_VhinG3D9ffdNHWgc"
ADMIN_CHAT_ID = -1002188124654
MAIN_CHAT_ID = -1002777829971
DB_FILE = "bot.db"
ADMIN_IDS = [370144165]  # <-- ВАЖНО: ЗАМЕНИТЕ ЭТО НА СВОЙ ID
REFERRAL_REWARD = 100  # Баллов ("Токенов") за реферала

# --- НОВЫЕ НАСТРОЙКИ ВЕБ-СЕРВЕРА ---
# URL, куда вы загрузите ваши index.html, style.css, app.js
# !!! ЗАМЕНИТЕ ЭТОТ URL, КОГДА ЗАГРУЗИТЕ ФАЙЛЫ НА ХОСТИНГ !!!
WEB_APP_URL = "https://your-domain.com/index.html" 

# Адрес для запуска локального веб-сервера
WEB_SERVER_HOST = "127.0.0.1"  # "localhost"
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
# (Без изменений, но убедитесь, что у вас есть 'balance')
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
# ... (Остальные функции БД: db_update_anket, db_update_status - без изменений)
async def db_update_anket(user_id: int, full_name: str, experience: str):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(
            "UPDATE users SET full_name = ?, experience = ?, status = 'pending' WHERE user_id = ?",
            (full_name, experience, user_id)
        )
        await db.commit()
async def db_update_status(user_id: int, status: str):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(
            "UPDATE users SET status = ?, decision_date = ? WHERE user_id = ?",
            (status, datetime.now(), user_id)
        )
        await db.commit()
# --- КОНЕЦ БЛОКА БД ---


# --- БЛОК: УСТАНОВКА КОМАНД МЕНЮ ---
# <-- НОВОЕ: Добавили команду /profile
async def set_bot_commands(bot_instance: Bot):
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
            await bot_instance.set_my_commands(
                commands=admin_commands,
                scope=BotCommandScopeChat(chat_id=admin_id)
            )
        except Exception as e:
            logging.error(f"Не удалось установить команды для админа {admin_id}: {e}")
# --- КОНЕЦ БЛОКА КОМАНД ---


# --- НОВЫЙ БЛОК: ВЕБ-СЕРВЕР И АВТОРИЗАЦИЯ MINI APP ---

def is_valid_initdata(init_data: str, bot_token: str) -> (bool, dict | None):
    """
    Проверяет подлинность данных, полученных от Telegram Mini App.
    Возвращает (True, user_data) или (False, None).
    """
    try:
        # 1. Парсим строку initData
        parsed_data = urllib.parse.parse_qs(init_data)
        
        # 2. Достаем хэш и данные пользователя
        hash_str = parsed_data.pop('hash', [None])[0]
        if not hash_str:
            return False, None

        # 3. Собираем строку для проверки
        data_check_string = "\n".join([
            f"{k}={v[0]}" for k, v in sorted(parsed_data.items())
        ])

        # 4. Генерируем секретный ключ
        secret_key = hmac.new(
            "WebAppData".encode(), bot_token.encode(), hashlib.sha256
        ).digest()

        # 5. Считаем хэш
        calculated_hash = hmac.new(
            secret_key, data_check_string.encode(), hashlib.sha256
        ).hexdigest()

        # 6. Сравниваем хэши
        if calculated_hash != hash_str:
            return False, None

        # 7. Если все ок, достаем данные юзера
        user_data = parsed_data.get('user', [None])[0]
        if not user_data:
            return False, None
            
        return True, json.loads(user_data)

    except Exception as e:
        logging.error(f"Ошибка валидации initData: {e}")
        return False, None


async def handle_get_user_data(request: web.Request):
    """
    Обработчик POST-запроса от Mini App.
    Принимает initData, проверяет и отдает JSON с данными.
    """
    try:
        # 1. Получаем JSON из запроса
        data = await request.json()
        init_data = data.get('initData')

        if not init_data:
            return web.json_response({"error": "No initData"}, status=400)

        # 2. Проверяем подлинность
        is_valid, user_data = is_valid_initdata(init_data, BOT_TOKEN)
        
        if not is_valid:
            return web.json_response({"error": "Invalid validation"}, status=401)
        
        user_id = user_data.get('id')
        if not user_id:
            return web.json_response({"error": "No user ID"}, status=400)

        # 3. Достаем данные из нашей БД
        async with aiosqlite.connect(DB_FILE) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT balance, join_date FROM users WHERE user_id = ?", (user_id,)) as cursor:
                user_db_data = await cursor.fetchone()

        if not user_db_data:
            # Такого быть не должно, т.к. /start его уже создал, но на всякий случай
            return web.json_response({"error": "User not found in DB"}, status=404)

        # 4. Генерируем реферальную ссылку
        bot_info = await bot.get_me()
        referral_link = f"https://t.me/{bot_info.username}?start={user_id}"

        # 5. Формируем и отдаем ответ
        response_data = {
            "balance": user_db_data['balance'],
            "join_date": user_db_data['join_date'],
            "ref_link": referral_link
        }
        return web.json_response(response_data)

    except Exception as e:
        logging.error(f"Ошибка в handle_get_user_data: {e}")
        return web.json_response({"error": "Internal server error"}, status=500)

# --- КОНЕЦ БЛОКА ВЕБ-СЕРВЕРА ---


# --- ОБРАБОТЧИКИ КОМАНД БОТА ---

# (Хэндлеры /start, anket_start, name_received, ... approve_user, reject_user... 
# .../admin, /myrefs - ОСТАЮТСЯ БЕЗ ИЗМЕНЕНИЙ)

# ... (скопируйте сюда все ваши хэндлеры, от /start до /myrefs) ...
# Я их пропущу для краткости, но ОНИ ДОЛЖНЫ ЗДЕСЬ БЫТЬ

# ---
# ... (Код ваших хэндлеров) ...
# ---

# --- НОВЫЙ ХЭНДЛЕР: Кнопка "Мой Профиль" ---
@router.message(Command("profile"))
async def cmd_profile(message: Message):
    """
    Отправляет кнопку-ссылку на Mini App
    """
    # Создаем кнопку, которая открывает WebApp
    profile_button = InlineKeyboardButton(
        text="💎 Открыть Мой Профиль",
        web_app=WebAppInfo(url=WEB_APP_URL) # Используем URL из конфига
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[profile_button]])
    
    await message.answer(
        "Нажмите кнопку ниже, чтобы открыть свой профиль, "
        "посмотреть баланс токенов и получить реферальную ссылку.",
        reply_markup=keyboard
    )
# --- КОНЕЦ НОВОГО ХЭНДЛЕРА ---


# --- БЛОК: ЗАПУСК БОТА И СЕРВЕРА (ПОЛНОСТЬЮ ПЕРЕДЕЛАН) ---

async def on_startup(dispatcher: Dispatcher, bot_instance: Bot, app: web.Application):
    """Выполняется при старте бота"""
    # 1. Инициализируем БД
    await init_db()
    # 2. Устанавливаем команды меню
    await set_bot_commands(bot_instance)
    
    # 3. Запускаем веб-сервер
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, WEB_SERVER_HOST, WEB_SERVER_PORT)
    await site.start()
    
    # Сохраняем runner, чтобы потом его остановить
    dispatcher['web_runner'] = runner
    logging.info(f"Веб-сервер запущен на http://{WEB_SERVER_HOST}:{WEB_SERVER_PORT}")

async def on_shutdown(dispatcher: Dispatcher):
    """Выполняется при остановке бота"""
    logging.info("Остановка веб-сервера...")
    if 'web_runner' in dispatcher:
        await dispatcher['web_runner'].cleanup()
    logging.info("Бот остановлен.")

async def main():
    # 1. Создаем веб-приложение aiohttp
    app = web.Application()

    # 2. Настраиваем CORS (Cross-Origin Resource Sharing)
    # Это *критически важно*, чтобы ваш app.js мог общаться с ботом
    cors = aiohttp_cors.setup(app, defaults={
        "*": aiohttp_cors.ResourceOptions(
            allow_credentials=True,
            expose_headers="*",
            allow_headers="*",
            allow_methods="*", # Разрешаем все методы (включая POST)
        )
    })
    
    # 3. Регистрируем наш обработчик /get_user_data
    route = app.router.add_post("/get_user_data", handle_get_user_data)
    cors.add(route) # Применяем CORS к этому маршруту

    # 4. Регистрируем функции startup/shutdown
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    try:
        # 5. Запускаем бота (он, в свою очередь, запустит веб-сервер)
        logging.info("Бот запускается в режиме polling...")
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot, app=app) # Передаем 'app' в start_polling
    finally:
        await bot.session.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Бот остановлен вручную.")