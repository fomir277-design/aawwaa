import os
import sys
import json
import asyncio
import random
import time
import platform
from datetime import datetime, timedelta
import pytz
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pyrogram.errors import SessionPasswordNeeded, FloodWait, PhoneCodeInvalid, PhoneCodeExpired

sys.stdout.reconfigure(line_buffering=True)

START_TIME = time.time()

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
active_clients = {}

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
                cb_data = button.callback_data
                cb_str = cb_data.decode('utf-8') if isinstance(cb_data, bytes) else str(cb_data) if cb_data else ""
                
                # 1. Сбор фермы
                if "farm_claim" in cb_str:
                    try:
                        await client.request_callback_answer(
                            chat_id=message.chat.id,
                            message_id=message.id,
                            callback_data=button.callback_data
                        )
                        print(f"[{session_name}] Успешно нажата инлайн-кнопка фермы")
                        asyncio.create_task(delayed_payment(client, session_name))
                    except Exception as e:
                        print(f"[{session_name}] Ошибка клика farm_claim: {e}")
                
                # 2. Ежедневная награда
                elif config.get("eday_enabled") and ("confirm_daily_claim" in cb_str or "Забрать" in str(button.text)):
                    try:
                        if button.callback_data:
                            await client.request_callback_answer(
                                chat_id=message.chat.id,
                                message_id=message.id,
                                callback_data=button.callback_data
                            )
                        else:
                            await message.click(button.text)
                        print(f"[{session_name}] Успешно забрана ежедневная награда")
                    except Exception as e:
                        print(f"[{session_name}] Ошибка клика eday: {e}")
                        
                # 3. ПОДТВЕРЖДЕНИЕ ПЕРЕВОДА
                elif cb_str.startswith("pay_confirm_"):
                    try:
                        # Формат: pay_confirm_{TARGET_ID}_{AMOUNT}_{SENDER_ID}
                        parts = cb_str.split("_")
                        if len(parts) >= 5:
                            btn_target_id = int(parts[2])
                            btn_amount = int(parts[3])
                            btn_sender_id = int(parts[4])
                            
                            my_id = client.me.id
                            conf_amount = config.get("target_amount", 0)
                            
                            # Проверяем, что это наш перевод и сумма сходится
                            if btn_sender_id == my_id and btn_amount == conf_amount:
                                target_match = False
                                
                                # Проверяем получателя
                                if "target_user_id" in config:
                                    target_match = (btn_target_id == config["target_user_id"])
                                else:
                                    # Если ID нет в конфиге (старая база), пробуем узнать сейчас
                                    conf_target = config.get("target_user")
                                    if conf_target:
                                        try:
                                            t_user = await client.get_users(conf_target)
                                            config["target_user_id"] = t_user.id
                                            save_config(session_name, config)
                                            target_match = (btn_target_id == t_user.id)
                                        except Exception:
                                            pass
                                            
                                # Финальное подтверждение
                                if target_match:
                                    await client.request_callback_answer(
                                        chat_id=message.chat.id,
                                        message_id=message.id,
                                        callback_data=button.callback_data
                                    )
                                    print(f"[{session_name}] ✅ Успешно подтвержден перевод {btn_amount} ТОчек")
                                else:
                                    print(f"[{session_name}] ⚠️ ID получателя не совпал с целью. Пропуск.")
                    except Exception as e:
                        print(f"[{session_name}] Ошибка обработки подтверждения перевода: {e}")

async def handle_user_commands(client: Client, message: Message):
    if not message.text:
        return
        
    text = message.text.strip()
    parts = text.split()
    if not parts:
        return
        
    command = parts[0].lower()
    session_name = client.name
    config = load_config(session_name)
    
    if command in [".on", ".вкл"]:
        config["enabled"] = True
        save_config(session_name, config)
        await message.edit_text("✅ **Юзербот включен** и готов к работе.")
        return
        
    elif command in [".off", ".выкл"]:
        config["enabled"] = False
        save_config(session_name, config)
        await message.edit_text("❌ **Юзербот выключен**.")
        return
        
    elif command in [".target", ".цель"]:
        if len(parts) >= 3:
            target_user = parts[1]
            try:
                target_amount = int(parts[2])
                config["target_user"] = target_user
                config["target_amount"] = target_amount
                
                msg = await message.edit_text("⏳ Сохранение и проверка ID цели...")
                
                try:
                    # Пытаемся сразу получить ID цели и сохранить для безопасности
                    t_user_obj = await client.get_users(target_user)
                    config["target_user_id"] = t_user_obj.id
                    save_config(session_name, config)
                    await msg.edit_text(f"✅ **Настройки перевода сохранены:**\n🎯 Цель: {target_user} (ID: `{t_user_obj.id}`)\n💰 Сумма: {target_amount}")
                except Exception as e:
                    # Если юзербот не общался с целью, может быть ошибка, но мы всё равно сохраняем
                    save_config(session_name, config)
                    await msg.edit_text(f"✅ **Настройки сохранены (ID будет получен при переводе):**\n🎯 Цель: {target_user}\n💰 Сумма: {target_amount}")
            except ValueError:
                await message.edit_text("❌ Ошибка: Сумма перевода должна быть числом.")
        else:
            await message.edit_text("⚠️ Формат команды: `.цель @username X`")
            
    elif command in [".tcard", ".ткарточка"]:
        config["tcard_enabled"] = not config.get("tcard_enabled", False)
        save_config(session_name, config)
        status = "✅ включена" if config["tcard_enabled"] else "❌ выключена"
        await message.edit_text(f"🃏 Авто-отправка 'ткарточка' каждые 187 минут: {status}.")
        
    elif command in [".eday", ".ежедн"]:
        config["eday_enabled"] = not config.get("eday_enabled", False)
        save_config(session_name, config)
        status = "✅ включен" if config["eday_enabled"] else "❌ выключен"
        await message.edit_text(f"🎁 Автосбор ежедневной награды: {status}.")
        
    elif command in [".debug", ".дебаг"]:
        req_start = time.time()
        await message.edit_text("⏳ Сбор данных для дебага...")
        
        ping_ms = round((time.time() - req_start) * 1000)
        uptime_sec = int(time.time() - START_TIME)
        u_h = uptime_sec // 3600
        u_m = (uptime_sec % 3600) // 60
        u_s = uptime_sec % 60
        
        target = config.get("target_user") or "❌ не задана"
        amount = config.get("target_amount") or "❌ не задана"
        tcard_st = "✅ вкл" if config.get("tcard_enabled") else "❌ выкл"
        eday_st = "✅ вкл" if config.get("eday_enabled") else "❌ выкл"
        bot_st = "✅ включен" if config.get("enabled") else "❌ выключен"
        
        sess_list = "\n".join([f"• `{name}`" for name in active_clients.keys()])
        if not sess_list:
            sess_list = "• Нет активных сессий"
            
        debug_text = f"""🛠 **PGUB Debug Info**
───────────────────
⏱️ **Аптайм:** {u_h}ч {u_m}м {u_s}с
📡 **Telegram Ping:** {ping_ms} ms
🐍 **Python:** {sys.version.split()[0]}
🖥 **Платформа:** {platform.system()} {platform.release()}
───────────────────
⚙️ **Настройки ({session_name}):**
  🤖 Статус бота: {bot_st}
  🎯 Цель перевода: {target}
  💰 Сумма перевода: {amount}
  🃏 ТКарточка: {tcard_st}
  🎁 Ежедн. награда: {eday_st}

📁 **Активные сессии на сервере:**
{sess_list}"""
        await message.edit_text(debug_text)
        
    elif command in [".help", ".помощь", ".справка"]:
        help_text = """📋 **Справка по командам PGUB**
───────────────────
`.вкл` / `.выкл` — включить/выключить бота
`.ткарточка` — авто-карточка (раз в 187 мин)
`.ежедн` — ежедневный бонус
`.target @username <сумма>` — цель и сумма для перевода
`.дебаг` — ваши настройки и статус бота
`.сессии` — список активных сессий
`.удалитьсессию <имя>` — удалить сессию с сервера
`.помощь` — эта справка"""
        await message.edit_text(help_text)
        
    elif command in [".sessions", ".сессии"]:
        sess_list = "\n".join([f"• `{name}`" for name in active_clients.keys()])
        await message.edit_text(f"📁 **Активные сессии в памяти:**\n{sess_list}")
        
    elif command in [".delsession", ".удалитьсессию"]:
        if len(parts) < 2:
            await message.edit_text("❌ Укажите имя сессии.\nПример: `.удалитьсессию user_79991234567`")
            return
            
        target_sess = parts[1]
        
        if target_sess == session_name:
            await message.edit_text("❌ Самоубийство запрещено! Вы не можете удалить собственную сессию с этого же аккаунта.")
            return
            
        deleted = False
        
        if target_sess in active_clients:
            try:
                await active_clients[target_sess].stop()
            except Exception:
                pass
            del active_clients[target_sess]
            deleted = True
            
        sess_path = os.path.join(SESSIONS_DIR, f"{target_sess}.session")
        if os.path.exists(sess_path):
            os.remove(sess_path)
            deleted = True
            
        conf_path = get_config_path(target_sess)
        if os.path.exists(conf_path):
            os.remove(conf_path)
            
        if deleted:
            await message.edit_text(f"✅ Успешно. Сессия `{target_sess}` остановлена и удалена с сервера.")
        else:
            await message.edit_text(f"⚠️ Ошибка: Сессия `{target_sess}` не найдена.")

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
    if session_name in active_clients:
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
        active_clients[session_name] = client
        asyncio.create_task(tcard_worker(client, session_name))
        asyncio.create_task(daily_and_mining_worker(client, session_name))
        print(f"Юзербот {session_name} успешно запущен в работу!")
    except Exception as e:
        print(f"Ошибка при старте юзербота {session_name}: {e}")

async def init_existing_sessions():
    await asyncio.sleep(2)
    files = [f for f in os.listdir(SESSIONS_DIR) if f.endswith(".session")]
    for f in files:
        s_name = f.replace(".session", "")
        
        if s_name == "auth_manager_bot":
            try:
                os.remove(os.path.join(SESSIONS_DIR, f))
            except: pass
            continue
            
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

    session_envs = {k: v for k, v in os.environ.items() if k.startswith("SESSION_STRING")}
    for key, string_value in session_envs.items():
        if not string_value.strip():
            continue
        s_name = key.lower()
        if s_name in active_clients:
            continue
        try:
            client = Client(
                name=s_name,
                session_string=string_value.strip(),
                api_id=int(API_ID),
                api_hash=API_HASH,
                plugins=None,
                in_memory=True
            )
            @client.on_message(filters.me)
            async def u_handler(c, m):
                await handle_user_commands(c, m)
            @client.on_message(filters.chat(GAME_BOT))
            async def b_handler(c, m):
                await handle_bot_message(c, m)
                
            await client.start()
            active_clients[s_name] = client
            asyncio.create_task(tcard_worker(client, s_name))
            asyncio.create_task(daily_and_mining_worker(client, s_name))
            print(f"Аккаунт из переменных {s_name} запущен.")
        except Exception as e:
            print(f"Ошибка инициализации {key}: {e}")

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
             print(f"КРИТИЧЕСКАЯ ОШИБКА СЕРВИСНОГО БОТА: {e}")
    else:
        print("Внимание: Переменная BOT_TOKEN пуста. Добавление сессий через чат отключено.")

    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())