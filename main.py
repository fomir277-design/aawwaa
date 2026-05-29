import os
import sys
import json
import asyncio
import random
from datetime import datetime, timedelta
import pytz
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pyrogram.errors import SessionPasswordNeeded, FloodWait, PhoneCodeInvalid, PhoneCodeExpired

sys.stdout.reconfigure(line_buffering=True)

DATA_DIR = "/data"
SESSIONS_DIR = os.path.join(DATA_DIR, "sessions")
CONFIGS_DIR = os.path.join(DATA_DIR, "configs")

os.makedirs(SESSIONS_DIR, exist_ok=True)
os.makedirs(CONFIGS_DIR, exist_ok=True)

GAME_BOT = "phonegetcardsbot"

API_ID = os.environ.get("API_ID")
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()

user_states = {}
active_session_names = set()

def get_config_path(session_name):
    return os.path.join(CONFIGS_DIR, f"{session_name}.json")

def load_config(session_name):
    path = get_config_path(session_name)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "enabled": True,
        "target_user": None,
        "target_amount": 0,
        "tcard_enabled": False,
        "eday_enabled": False,
        "last_mining_date": ""
    }

def save_config(session_name, config):
    path = get_config_path(session_name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=4)

async def delayed_payment(client: Client, session_name: str):
    await asyncio.sleep(random.randint(60, 180))
    config = load_config(session_name)
    if config.get("enabled") and config.get("target_user") and config.get("target_amount"):
        user = config["target_user"]
        amount = config["target_amount"]
        await client.send_message(GAME_BOT, f"/pay {user} {amount} Майнинг ферма")
        print(f"[{session_name}] Отправлен перевод {amount} для {user}")

async def handle_bot_message(client: Client, message: Message):
    if not message.chat or message.chat.username != GAME_BOT:
        return
        
    session_name = client.name
    config = load_config(session_name)
    if not config.get("enabled"):
        return

    if message.reply_markup and message.reply_markup.inline_keyboard:
        for row in message.reply_markup.inline_keyboard:
            for button in row:
                if button.callback_data == "farm_claim":
                    try:
                        await message.click(button.callback_data)
                        print(f"[{session_name}] Нажата кнопка 'Снять деньги с фермы'")
                        asyncio.create_task(delayed_payment(client, session_name))
                    except Exception as e:
                        print(f"[{session_name}] Ошибка клика farm_claim: {e}")
                
                elif config.get("eday_enabled") and ("confirm_daily_claim" in str(button.callback_data) or "Забрать" in str(button.text)):
                    try:
                        await message.click(button.callback_data)
                        print(f"[{session_name}] Забрана ежедневная награда")
                    except Exception as e:
                        print(f"[{session_name}] Ошибка клика eday: {e}")

async def handle_user_commands(client: Client, message: Message):
    if not message.text:
        return
        
    text = message.text.strip()
    session_name = client.name
    config = load_config(session_name)
    
    if text in [".on", ".вкл"]:
        config["enabled"] = True
        save_config(session_name, config)
        await message.reply_text("Юзербот включен.")
        return
    elif text in [".off", ".выкл"]:
        config["enabled"] = False
        save_config(session_name, config)
        await message.reply_text("Юзербот выключен.")
        return
        
    if not config["enabled"]:
        return
        
    if text.startswith((".target", ".цель")):
        parts = text.split()
        if len(parts) == 3:
            target_user = parts[1]
            try:
                target_amount = int(parts[2])
                config["target_user"] = target_user
                config["target_amount"] = target_amount
                save_config(session_name, config)
                await message.reply_text(f"Настройка сохранена: цель {target_user}, сумма {target_amount}")
            except ValueError:
                await message.reply_text("Сумма перевода должна быть числом.")
        else:
            await message.reply_text("Формат команды: .цель @user X")
            
    elif text in [".tcard", ".ткарточка"]:
        config["tcard_enabled"] = not config.get("tcard_enabled", False)
        save_config(session_name, config)
        status = "включена" if config["tcard_enabled"] else "выключена"
        await message.reply_text(f"Отправка 'ткарточка' каждые 187 минут {status}.")
        
    elif text in [".eday", ".ежедн"]:
        config["eday_enabled"] = not config.get("eday_enabled", False)
        save_config(session_name, config)
        status = "включен" if config["eday_enabled"] else "выключен"
        await message.reply_text(f"Автосбор ежедневной награды {status}.")

async def tcard_worker(client: Client, session_name: str):
    while True:
        try:
            config = load_config(session_name)
            if config.get("enabled") and config.get("tcard_enabled"):
                await client.send_message(GAME_BOT, "ткарточка")
        except Exception as e:
            print(f"[{session_name}] Ошибка tcard_worker: {e}")
        await asyncio.sleep(187 * 60)

async def daily_and_mining_worker(client: Client, session_name: str):
    msk = pytz.timezone("Europe/Moscow")
    while True:
        try:
            config = load_config(session_name)
            if config.get("enabled"):
                now = datetime.now(msk)
                last_run = config.get("last_mining_date", "")
                today_str = now.strftime("%Y-%m-%d")
                
                if last_run == today_str:
                    tomorrow = now + timedelta(days=1)
                    next_run = msk.localize(datetime(tomorrow.year, tomorrow.month, tomorrow.day, random.randint(1, 4), random.randint(0, 59), random.randint(0, 59)))
                else:
                    if now.hour < 5:
                        if now.hour >= 1:
                            next_run = now + timedelta(seconds=15)
                        else:
                            next_run = msk.localize(datetime(now.year, now.month, now.day, random.randint(1, 4), random.randint(0, 59), random.randint(0, 59)))
                    else:
                        tomorrow = now + timedelta(days=1)
                        next_run = msk.localize(datetime(tomorrow.year, tomorrow.month, tomorrow.day, random.randint(1, 4), random.randint(0, 59), random.randint(0, 59)))
                
                sleep_seconds = (next_run - now).total_seconds()
                if sleep_seconds > 0:
                    await asyncio.sleep(sleep_seconds)
                
                config = load_config(session_name)
                if not config.get("enabled"):
                    continue
                
                config["last_mining_date"] = datetime.now(msk).strftime("%Y-%m-%d")
                save_config(session_name, config)
                
                if config.get("eday_enabled"):
                    await client.send_message(GAME_BOT, "Ежедневная награда")
                    await asyncio.sleep(10)
                
                await client.send_message(GAME_BOT, "тмайнинг")
        except Exception as e:
            print(f"[{session_name}] Ошибка daily_and_mining_worker: {e}")
        await asyncio.sleep(60)

async def launch_userbot_instance(session_name):
    if session_name in active_session_names:
        return
    try:
        client = Client(
            name=session_name,
            workdir=SESSIONS_DIR,
            api_id=int(API_ID),
            api_hash=API_HASH,
            plugins=None
        )
        
        @client.on_message(filters.me)
        async def u_handler(c, m):
            await handle_user_commands(c, m)
            
        @client.on_message(filters.chat(GAME_BOT))
        async def b_handler(c, m):
            await handle_bot_message(c, m)
            
        await client.start()
        asyncio.create_task(tcard_worker(client, session_name))
        asyncio.create_task(daily_and_mining_worker(client, session_name))
        active_session_names.add(session_name)
        print(f"Юзербот {session_name} успешно запущен в работу!")
    except Exception as e:
        print(f"Ошибка при старте юзербота {session_name}: {e}")

async def init_existing_sessions():
    await asyncio.sleep(2)
    files = [f for f in os.listdir(SESSIONS_DIR) if f.endswith(".session")]
    for f in files:
        s_name = f.replace(".session", "")
        if s_name == "master_bot":
            continue
        print(f"Найдена существующая сессия: {s_name}. Запуск...")
        asyncio.create_task(launch_userbot_instance(s_name))

def get_pin_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("1", callback_data="pin_1"), InlineKeyboardButton("2", callback_data="pin_2"), InlineKeyboardButton("3", callback_data="pin_3")],
        [InlineKeyboardButton("4", callback_data="pin_4"), InlineKeyboardButton("5", callback_data="pin_5"), InlineKeyboardButton("6", callback_data="pin_6")],
        [InlineKeyboardButton("7", callback_data="pin_7"), InlineKeyboardButton("8", callback_data="pin_8"), InlineKeyboardButton("9", callback_data="pin_9")],
        [InlineKeyboardButton("⬅️", callback_data="pin_del"), InlineKeyboardButton("0", callback_data="pin_0"), InlineKeyboardButton("🗑", callback_data="pin_clear")],
        [InlineKeyboardButton("❌ Отмена", callback_data="pin_cancel")]
    ])

def format_code_display(code: str):
    # Форматируем как "1 2 3 ⚪️ ⚪️"
    display = " ".join(list(code))
    if len(code) < 5:
        if len(code) > 0:
            display += " "
        display += " ".join(["⚪️"] * (5 - len(code)))
    return display

def setup_bot_handlers(bot: Client):
    @bot.on_message(filters.command("start") & filters.private)
    async def start_cmd(c, m):
        user_states[m.chat.id] = {"step": "IDLE"}
        await m.reply_text("Привет! Отправь мне номер телефона аккаунта для авторизации юзербота в международном формате (например, +79991234567).")

    @bot.on_message(filters.text & filters.private)
    async def process_auth(c, m):
        chat_id = m.chat.id
        text = m.text.strip()
        state = user_states.get(chat_id, {"step": "IDLE"})
        step = state.get("step")

        if step == "IDLE":
            if text.startswith("+") and len(text) > 9:
                phone = text.replace(" ", "")
                session_name = f"user_{phone.replace('+', '')}"
                
                await m.reply_text("Связываюсь с серверами Telegram, ожидайте... ⏳")
                
                client = Client(
                    name=session_name,
                    workdir=SESSIONS_DIR,
                    api_id=int(API_ID),
                    api_hash=API_HASH,
                    in_memory=False
                )
                try:
                    await client.connect()
                    code_info = await client.send_code(phone)
                    
                    user_states[chat_id] = {
                        "step": "WAIT_CODE",
                        "phone": phone,
                        "session_name": session_name,
                        "client": client,
                        "phone_code_hash": code_info.phone_code_hash,
                        "entered_code": ""
                    }
                    
                    await m.reply_text(
                        f"📲 Telegram отправил код на номер {phone}.\n\n"
                        f"**Код:** {format_code_display('')}\n\n"
                        f"Используй клавиатуру ниже для ввода:",
                        reply_markup=get_pin_keyboard()
                    )
                except FloodWait as e:
                    await m.reply_text(f"⚠️ Ошибка лимита запросов. Подожди {e.value} секунд.")
                    await client.disconnect()
                except Exception as e:
                    await m.reply_text(f"❌ Произошла ошибка при отправке кода: {e}")
                    await client.disconnect()
            else:
                await m.reply_text("Неверный формат номера. Отправь номер телефона, начиная с `+`")

        elif step == "WAIT_CODE":
            # Игнорируем текстовые сообщения на этапе ожидания кода, чтобы заставить использовать кнопки
            message = await m.reply_text("⚠️ Пожалуйста, используй кнопки выше для безопасного ввода кода.")
            await asyncio.sleep(3)
            await message.delete()

        elif step == "WAIT_PASSWORD":
            client = state["client"]
            session_name = state["session_name"]
            try:
                await client.check_password(text)
                await m.reply_text("✅ Пароль принят! Авторизация успешна. Юзербот запущен.")
                await client.disconnect()
                user_states[chat_id] = {"step": "IDLE"}
                asyncio.create_task(launch_userbot_instance(session_name))
            except Exception as e:
                await m.reply_text(f"❌ Неверный пароль или ошибка: {e}. Попробуй заново через /start")
                await client.disconnect()
                user_states[chat_id] = {"step": "IDLE"}

    @bot.on_callback_query(filters.regex(r"^pin_"))
    async def pin_callback(c, cq: CallbackQuery):
        chat_id = cq.message.chat.id
        state = user_states.get(chat_id)
        
        if not state or state.get("step") != "WAIT_CODE":
            await cq.answer("Код больше не ожидается. Начни заново через /start", show_alert=True)
            return
            
        action = cq.data.split("_")[1]
        current_code = state.get("entered_code", "")
        client = state["client"]
        
        if action == "cancel":
            await client.disconnect()
            user_states[chat_id] = {"step": "IDLE"}
            await cq.message.edit_text("🛑 Авторизация отменена.")
            return
            
        elif action == "clear":
            current_code = ""
        elif action == "del":
            current_code = current_code[:-1]
        elif action.isdigit():
            if len(current_code) < 5:
                current_code += action

        state["entered_code"] = current_code
        
        if len(current_code) == 5:
            await cq.message.edit_text(f"🔐 Проверка кода: {format_code_display(current_code)} ...")
            
            try:
                phone = state["phone"]
                phone_code_hash = state["phone_code_hash"]
                session_name = state["session_name"]
                
                await client.sign_in(phone, phone_code_hash, current_code)
                await cq.message.edit_text("✅ Авторизация успешна! Юзербот запущен в работу.")
                await client.disconnect()
                user_states[chat_id] = {"step": "IDLE"}
                asyncio.create_task(launch_userbot_instance(session_name))
                
            except SessionPasswordNeeded:
                user_states[chat_id]["step"] = "WAIT_PASSWORD"
                await cq.message.edit_text("🔒 На аккаунте включен облачный пароль (2FA).\n\nОтправь свой пароль сообщением в этот чат:")
            except (PhoneCodeInvalid, PhoneCodeExpired):
                await cq.message.edit_text("❌ Неверный или просроченный код. Попробуй заново через /start")
                await client.disconnect()
                user_states[chat_id] = {"step": "IDLE"}
            except Exception as e:
                await cq.message.edit_text(f"❌ Ошибка при авторизации: {e}")
                await client.disconnect()
                user_states[chat_id] = {"step": "IDLE"}
        else:
            try:
                await cq.message.edit_text(
                    f"📲 Telegram отправил код на номер {state['phone']}.\n\n"
                    f"**Код:** {format_code_display(current_code)}\n\n"
                    f"Используй клавиатуру ниже для ввода:",
                    reply_markup=get_pin_keyboard()
                )
            except Exception:
                pass 
            await cq.answer()

async def main():
    if not API_ID or not API_HASH:
        print("КРИТИЧЕСКАЯ ОШИБКА: Не указаны API_ID и/или API_HASH!")
        return

    asyncio.create_task(init_existing_sessions())

    if BOT_TOKEN:
        print("Запуск главного Сервисного Бот-Интерфейса...")
        try:
            bot_client = Client(
                name="master_bot",
                api_id=int(API_ID),
                api_hash=API_HASH,
                bot_token=BOT_TOKEN,
                workdir=SESSIONS_DIR
            )
            setup_bot_handlers(bot_client)
            await bot_client.start()
            print("Сервисный бот онлайн и готов принимать авторизации.")
        except Exception as e:
             print(f"КРИТИЧЕСКАЯ ОШИБКА СЕРВИСНОГО БОТА (Скорее всего неверный BOT_TOKEN): {e}")
    else:
        print("Внимание: Переменная BOT_TOKEN пуста. Добавление сессий через чат отключено.")

    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())