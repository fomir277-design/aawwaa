import os
import sys
import json
import asyncio
import random
import time
from datetime import datetime, timedelta
import pytz
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pyrogram.errors import SessionPasswordNeeded, FloodWait, PhoneCodeInvalid, PhoneCodeExpired

# --- Глушитель мусорных ошибок Pyrogram ---
def custom_exception_handler(loop, context):
    exc = context.get('exception')
    msg = context.get('message', '')
    if exc:
        exc_str = str(exc)
        if isinstance(exc, ValueError) and "Peer id invalid" in exc_str: return
        if isinstance(exc, KeyError) and "ID not found" in exc_str: return
    if "Peer id invalid" in msg or "ID not found" in msg: return
    loop.default_exception_handler(context)

sys.stdout.reconfigure(line_buffering=True)
START_TIME = time.time()

# ──────────────────────────────────────────────
# Файловая система и Константы
# ──────────────────────────────────────────────
DATA_DIR = "/data"
SESSIONS_DIR = os.path.join(DATA_DIR, "sessions")
CONFIGS_DIR = os.path.join(DATA_DIR, "configs")
os.makedirs(SESSIONS_DIR, exist_ok=True)
os.makedirs(CONFIGS_DIR, exist_ok=True)

GAME_BOT = "phonegetcardsbot"

API_ID   = os.environ.get("API_ID")
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()

user_states    = {}
active_clients = {}
buy_states     = {}

RARITIES = [
    ("Ширпотрёб",     "Ширпотреб",     "S"),
    ("Необычный",     "Необычный",     "N"),
    ("Редкий",        "Редкий",        "R"),
    ("Мистический",   "Мистический",   "M"),
    ("Хроматический", "Хроматический", "C"),
    ("Аркана",        "Аркана",        "A"),
    ("Платиновый",    "Платиновый",    "P"),
]

TCARD_INTERVALS = [185, 175, 165, 155, 145, 135, 125, 65]

# ──────────────────────────────────────────────
# Конфигурация
# ──────────────────────────────────────────────
def get_config_path(session_name): return os.path.join(CONFIGS_DIR, f"{session_name}.json")

def load_config(session_name):
    defaults = {
        "enabled":          True,
        "target_user":      None,
        "target_amount":    0,
        "tcard_enabled":    False,
        "tcard_interval":   185,
        "eday_enabled":     False,
        "last_mining_date": "",
        "buy_enabled":      False,
        "buy_rarity":       None,
        "buy_count":        1,
    }
    path = get_config_path(session_name)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f: defaults.update(json.load(f))
    return defaults

def save_config(session_name, config):
    with open(get_config_path(session_name), "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=4)

def find_session_by_user_id(user_id):
    for sess_name, client in active_clients.items():
        if client.me and client.me.id == user_id:
            return sess_name
    return None

# ──────────────────────────────────────────────
# Воркеры и задачи
# ──────────────────────────────────────────────
async def delayed_payment(client: Client, session_name: str):
    await asyncio.sleep(random.randint(60, 180))
    config = load_config(session_name)
    if config.get("enabled") and config.get("target_user") and config.get("target_amount"):
        user   = config["target_user"]
        amount = config["target_amount"]
        await client.send_message(GAME_BOT, f"/pay {user} {amount} Майнинг ферма")
        print(f"[{session_name}] 💸 Отправлен перевод {amount} для {user}")

async def send_shop_command(client: Client, session_name: str):
    await asyncio.sleep(5)
    try:
        await client.send_message(GAME_BOT, "Магазин телефонов")
    except Exception as e:
        buy_states.pop(session_name, None)

async def tcard_worker(client: Client, session_name: str):
    last_sent = 0.0
    while True:
        await asyncio.sleep(30)
        try:
            config = load_config(session_name)
            interval_min = config.get("tcard_interval", 0)
            if config.get("enabled") and config.get("tcard_enabled") and interval_min > 0:
                if time.time() - last_sent >= interval_min * 60:
                    await client.send_message(GAME_BOT, "ткарточка")
                    last_sent = time.time()
                    print(f"[{session_name}] 🃏 ТКарточка отправлена")
        except Exception: pass

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
                        if now.hour >= 1: next_run = now + timedelta(seconds=15)
                        else: next_run = msk.localize(datetime(now.year, now.month, now.day, random.randint(1, 4), random.randint(0, 59), random.randint(0, 59)))
                    else:
                        tomorrow = now + timedelta(days=1)
                        next_run = msk.localize(datetime(tomorrow.year, tomorrow.month, tomorrow.day, random.randint(1, 4), random.randint(0, 59), random.randint(0, 59)))

                sleep_sec = (next_run - now).total_seconds()
                if sleep_sec > 0: await asyncio.sleep(sleep_sec)

                config = load_config(session_name)
                if not config.get("enabled"): continue

                config["last_mining_date"] = datetime.now(msk).strftime("%Y-%m-%d")
                save_config(session_name, config)

                if config.get("eday_enabled"):
                    await client.send_message(GAME_BOT, "Ежедневная награда")
                    await asyncio.sleep(10)

                await client.send_message(GAME_BOT, "тмайнинг")
        except Exception: pass
        await asyncio.sleep(60)

# ──────────────────────────────────────────────
# Обработчик сообщений игрового бота
# ──────────────────────────────────────────────
async def handle_bot_message(client: Client, message: Message):
    if not message.chat or message.chat.username != GAME_BOT: return
    session_name = client.name
    config = load_config(session_name)
    if not config.get("enabled"): return
    if not message.reply_markup or not message.reply_markup.inline_keyboard: return

    def cb_str(button):
        raw = button.callback_data
        if raw is None: return ""
        return raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)

    buy_state = buy_states.get(session_name)
    if buy_state:
        if time.time() - buy_state.get("started", 0) > 300:
            buy_states.pop(session_name, None)
        else:
            rarity, count, step = buy_state["rarity"], buy_state["count"], buy_state["step"]
            step_target = {
                "wait_rarity": f"shop_rarity_{rarity}", "wait_phone": f"shop_phone_{rarity}_1",
                "wait_propose": f"shop_propose_bulk_{rarity}_1", "wait_bulk": f"shop_bulk_select_{rarity}_1_{count}",
                "wait_confirm": f"shop_confirm_bulk_{rarity}_1_{count}",
            }
            step_next = {
                "wait_rarity": "wait_phone", "wait_phone": "wait_propose",
                "wait_propose": "wait_bulk", "wait_bulk": "wait_confirm", "wait_confirm": "done",
            }
            target_cb = step_target.get(step)
            if target_cb:
                for row in message.reply_markup.inline_keyboard:
                    for btn in row:
                        if cb_str(btn) == target_cb:
                            try:
                                await client.request_callback_answer(message.chat.id, message.id, btn.callback_data)
                                nxt = step_next[step]
                                if nxt == "done": buy_states.pop(session_name, None)
                                else: buy_states[session_name]["step"] = nxt
                            except Exception: buy_states.pop(session_name, None)
                            return

    for row in message.reply_markup.inline_keyboard:
        for btn in row:
            cbs = cb_str(btn)
            if "farm_claim" in cbs:
                try:
                    await client.request_callback_answer(message.chat.id, message.id, btn.callback_data)
                    asyncio.create_task(delayed_payment(client, session_name))
                    if config.get("buy_enabled") and config.get("buy_rarity") and config.get("buy_count"):
                        buy_states[session_name] = {"step": "wait_rarity", "rarity": config["buy_rarity"], "count": config["buy_count"], "started": time.time()}
                        asyncio.create_task(send_shop_command(client, session_name))
                except Exception: pass
            elif config.get("eday_enabled") and ("confirm_daily_claim" in cbs or "Забрать" in str(btn.text)):
                try:
                    if btn.callback_data: await client.request_callback_answer(message.chat.id, message.id, btn.callback_data)
                    else: await message.click(btn.text)
                except Exception: pass
            elif cbs.startswith("pay_confirm_"):
                try:
                    parts = cbs.split("_")
                    if len(parts) >= 5:
                        btn_target_id, btn_amount, btn_sender_id = int(parts[2]), int(parts[3]), int(parts[4])
                        if btn_sender_id == client.me.id and btn_amount == config.get("target_amount", 0):
                            target_match = False
                            if "target_user_id" in config: target_match = (btn_target_id == config["target_user_id"])
                            else:
                                conf_target = config.get("target_user")
                                if conf_target:
                                    try:
                                        t_user = await client.get_users(conf_target)
                                        config["target_user_id"] = t_user.id
                                        save_config(session_name, config)
                                        target_match = (btn_target_id == t_user.id)
                                    except Exception: pass
                            if target_match:
                                await client.request_callback_answer(message.chat.id, message.id, btn.callback_data)
                except Exception: pass

# ──────────────────────────────────────────────
# Запуск юзерботов
# ──────────────────────────────────────────────
async def launch_userbot_instance(session_name):
    if session_name in active_clients: return
    try:
        client = Client(name=session_name, workdir=SESSIONS_DIR, api_id=int(API_ID), api_hash=API_HASH, plugins=None)
        @client.on_message(filters.chat(GAME_BOT))
        async def b_handler(c, m): await handle_bot_message(c, m)
        
        await client.start()
        try:
            async for _ in client.get_dialogs(limit=20): pass
        except Exception: pass

        active_clients[session_name] = client
        asyncio.create_task(tcard_worker(client, session_name))
        asyncio.create_task(daily_and_mining_worker(client, session_name))
        print(f"Юзербот {session_name} успешно запущен!")
    except Exception as e: print(f"Критическая ошибка старта юзербота {session_name}: {e}")

async def init_existing_sessions():
    await asyncio.sleep(2)
    files = [f for f in os.listdir(SESSIONS_DIR) if f.endswith(".session")]
    for f in files:
        s_name = f.replace(".session", "")
        # Добавляем master_bot_v2, чтобы скрипт не трогал этот файл
        if s_name in ["auth_manager_bot", "master_bot", "master_bot_v2"]: 
            continue
        asyncio.create_task(launch_userbot_instance(s_name))

# ──────────────────────────────────────────────
# ПАНЕЛЬ УПРАВЛЕНИЯ (MASTER BOT)
# ──────────────────────────────────────────────
def get_pin_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("1", callback_data="pin_1"), InlineKeyboardButton("2", callback_data="pin_2"), InlineKeyboardButton("3", callback_data="pin_3")],
        [InlineKeyboardButton("4", callback_data="pin_4"), InlineKeyboardButton("5", callback_data="pin_5"), InlineKeyboardButton("6", callback_data="pin_6")],
        [InlineKeyboardButton("7", callback_data="pin_7"), InlineKeyboardButton("8", callback_data="pin_8"), InlineKeyboardButton("9", callback_data="pin_9")],
        [InlineKeyboardButton("⬅️", callback_data="pin_del"), InlineKeyboardButton("0", callback_data="pin_0"), InlineKeyboardButton("🗑", callback_data="pin_clear")],
        [InlineKeyboardButton("❌ Отмена", callback_data="pin_cancel")],
    ])

def format_code_display(code: str):
    display = " ".join(list(code))
    if len(code) < 5:
        if len(code) > 0: display += " "
        display += " ".join(["⚪️"] * (5 - len(code)))
    return display

def get_main_keyboard(sess):
    cfg = load_config(sess)
    status_text = "🔴 Включить бота" if not cfg.get("enabled") else "🟢 Выключить бота"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(status_text, callback_data="cfg_toggle")],
        [InlineKeyboardButton("🛍 Покупать телефоны", callback_data="cfg_buymenu")],
        [InlineKeyboardButton("🃏 Отправлять ТКарточка", callback_data="cfg_tcardmenu")],
        [InlineKeyboardButton("🎯 Установить цель и сумму", callback_data="cfg_target")],
        [InlineKeyboardButton("🛠 Запросить Дебаг", callback_data="cfg_debug")],
        [InlineKeyboardButton("📋 Список активных сессий", callback_data="cfg_sesslist_0")],
        [InlineKeyboardButton("🗂 Управление сессиями", callback_data="cfg_sessmanage_0")]
    ])

def setup_bot_handlers(bot: Client):
    @bot.on_message(filters.command("start") & filters.private)
    async def start_cmd(c, m):
        user_states[m.chat.id] = {"step": "IDLE"}
        await m.reply_text(
            "📟 **PGUB CORE SYSTEM**\n\n"
            "Доступные команды терминала:\n"
            "**/auth** (или /авторизация) — Авторизовать новый аккаунт\n"
            "**/config** (или /настройки) — Открыть панель управления"
        )

    # ── ОБРАБОТКА ВСЕГО ТЕКСТА ──
    @bot.on_message(filters.text & filters.private)
    async def process_text(c, m):
        text = m.text.strip()
        chat_id = m.chat.id
        state = user_states.get(chat_id, {"step": "IDLE"})
        step = state.get("step")

        # Перехват команд из любого состояния
        if text.lower() in ["/config", "/настройки"]:
            user_states[chat_id] = {"step": "IDLE"}
            sess = find_session_by_user_id(m.from_user.id)
            if not sess:
                if active_clients: sess = list(active_clients.keys())[0] # Fallback на первую сессию
                else:
                    await m.reply_text("❌ **Нет активных сессий.** Напиши /auth для авторизации.")
                    return
            user_states[chat_id]["editing_sess"] = sess
            await m.reply_text(f"📟 **ПАНЕЛЬ УПРАВЛЕНИЯ**\n\n🟣 **Сессия:** `{sess}`", reply_markup=get_main_keyboard(sess))
            return

        if text.lower() in ["/auth", "/авторизация"]:
            user_states[chat_id] = {"step": "WAIT_PHONE"}
            await m.reply_text("📟 **ТЕРМИНАЛ АВТОРИЗАЦИИ**\n\nВведи номер телефона в формате `+79991234567`:")
            return

        # ── ОБРАБОТКА СОСТОЯНИЙ ──
        if step == "WAIT_PHONE":
            if text.startswith("+") and len(text) > 9:
                phone = text.replace(" ", "")
                session_name = f"user_{phone.replace('+', '')}"
                await m.reply_text("Устанавливаю соединение… ⏳")
                client = Client(name=session_name, workdir=SESSIONS_DIR, api_id=int(API_ID), api_hash=API_HASH, in_memory=False)
                try:
                    await client.connect()
                    code_info = await client.send_code(phone)
                    user_states[chat_id] = {
                        "step": "WAIT_CODE", "phone": phone, "session_name": session_name,
                        "client": client, "phone_code_hash": code_info.phone_code_hash, "entered_code": ""
                    }
                    await m.reply_text(f"📲 Код отправлен на {phone}.\n\n**Ввод:** {format_code_display('')}\n\nНабирай на клавиатуре:", reply_markup=get_pin_keyboard())
                except Exception as e:
                    await m.reply_text(f"❌ Ошибка: {e}")
                    await client.disconnect()
            else:
                await m.reply_text("❌ Номер должен начинаться с `+`.")

        elif step == "WAIT_CODE":
            msg = await m.reply_text("⚠️ Вводи код инлайн-кнопками выше.")
            await asyncio.sleep(3)
            await msg.delete()

        elif step == "WAIT_PASSWORD":
            client, session_name = state["client"], state["session_name"]
            try:
                await client.check_password(text)
                await m.reply_text("✅ 2FA подтвержден. Запускаю юзербота...\nОткрой настройки через **/config**")
                await client.disconnect()
                user_states[chat_id] = {"step": "IDLE"}
                asyncio.create_task(launch_userbot_instance(session_name))
            except Exception as e:
                await m.reply_text(f"❌ Неверный пароль: {e}")
                await client.disconnect()
                user_states[chat_id] = {"step": "IDLE"}

        elif step == "WAIT_TARGET":
            sess = state.get("editing_sess")
            parts = text.split()
            if len(parts) >= 2:
                target_user = parts[0]
                try:
                    amount = int(parts[1])
                    cfg = load_config(sess)
                    cfg["target_user"] = target_user
                    cfg["target_amount"] = amount
                    # Пробуем получить ID
                    if sess in active_clients:
                        try:
                            t_obj = await active_clients[sess].get_users(target_user)
                            cfg["target_user_id"] = t_obj.id
                        except: pass
                    save_config(sess, cfg)
                    user_states[chat_id] = {"step": "IDLE"}
                    await m.reply_text(f"✅ **Цель успешно привязана!**\nПользователь: {target_user}\nСумма: {amount} ТОчек", reply_markup=get_main_keyboard(sess))
                except ValueError:
                    await m.reply_text("❌ Сумма должна быть числом. Повтори ввод (например: `@user 1000`):")
            else:
                await m.reply_text("❌ Неверный формат. Повтори ввод (например: `@user 1000`):")

        elif step == "WAIT_RENAME":
            old_sess = state.get("editing_sess")
            new_sess = text.replace(" ", "_")
            if new_sess in active_clients or new_sess in ["auth_manager_bot", "master_bot"]:
                await m.reply_text("❌ Имя занято или недопустимо. Придумай другое:")
                return
                
            if old_sess in active_clients:
                await active_clients[old_sess].stop()
                del active_clients[old_sess]

            old_db = os.path.join(SESSIONS_DIR, f"{old_sess}.session")
            new_db = os.path.join(SESSIONS_DIR, f"{new_sess}.session")
            if os.path.exists(old_db): os.rename(old_db, new_db)

            old_cfg = get_config_path(old_sess)
            new_cfg = get_config_path(new_sess)
            if os.path.exists(old_cfg): os.rename(old_cfg, new_cfg)

            user_states[chat_id] = {"step": "IDLE", "editing_sess": new_sess}
            await m.reply_text(f"✅ **Успех.** Сессия переименована в `{new_sess}`.\nЗапускаю алгоритмы...", reply_markup=get_main_keyboard(new_sess))
            asyncio.create_task(launch_userbot_instance(new_sess))

    # ── ИНЛАЙН ПИН-ПАД ──
    @bot.on_callback_query(filters.regex(r"^pin_"))
    async def pin_callback(c, cq: CallbackQuery):
        chat_id = cq.message.chat.id
        state   = user_states.get(chat_id)
        if not state or state.get("step") != "WAIT_CODE":
            await cq.answer("Сессия устарела. Напиши /auth", show_alert=True)
            return

        action, current_code, client = cq.data.split("_")[1], state.get("entered_code", ""), state["client"]

        if action == "cancel":
            await client.disconnect()
            user_states[chat_id] = {"step": "IDLE"}
            await cq.message.edit_text("🛑 Авторизация прервана.")
            return
        elif action == "clear": current_code = ""
        elif action == "del": current_code = current_code[:-1]
        elif action.isdigit() and len(current_code) < 5: current_code += action

        state["entered_code"] = current_code

        if len(current_code) == 5:
            await cq.message.edit_text(f"🔐 Синхронизация: {format_code_display(current_code)} …")
            try:
                await client.sign_in(state["phone"], state["phone_code_hash"], current_code)
                sess = state["session_name"]
                user_states[chat_id] = {"step": "IDLE", "editing_sess": sess}
                await client.disconnect()
                await cq.message.edit_text("✅ **Синхронизация завершена.**", reply_markup=get_main_keyboard(sess))
                asyncio.create_task(launch_userbot_instance(sess))
            except SessionPasswordNeeded:
                user_states[chat_id]["step"] = "WAIT_PASSWORD"
                await cq.message.edit_text("🔒 Введи облачный пароль (2FA) текстом:")
            except (PhoneCodeInvalid, PhoneCodeExpired):
                await cq.message.edit_text("❌ Код отклонен. Начни заново: /auth")
                await client.disconnect()
                user_states[chat_id] = {"step": "IDLE"}
            except Exception as e:
                await cq.message.edit_text(f"❌ Критический сбой: {e}")
                await client.disconnect()
                user_states[chat_id] = {"step": "IDLE"}
        else:
            try: await cq.message.edit_text(f"📲 Сгенерирован код для {state['phone']}.\n\n**Ввод:** {format_code_display(current_code)}\n\nНабирай:", reply_markup=get_pin_keyboard())
            except Exception: pass
            await cq.answer()

    # ── ИНЛАЙН ПАНЕЛЬ УПРАВЛЕНИЯ ──
    @bot.on_callback_query(filters.regex(r"^cfg_"))
    async def config_callback(c, cq: CallbackQuery):
        chat_id = cq.message.chat.id
        sess = user_states.get(chat_id, {}).get("editing_sess")
        if not sess:
            await cq.answer("❌ Сессия не выбрана. Напиши /config", show_alert=True)
            return
            
        data = cq.data
        cfg = load_config(sess)

        if data == "cfg_main":
            user_states[chat_id]["step"] = "IDLE"
            await cq.message.edit_text(f"📟 **ПАНЕЛЬ УПРАВЛЕНИЯ**\n\n🟣 **Сессия:** `{sess}`", reply_markup=get_main_keyboard(sess))

        elif data == "cfg_toggle":
            cfg["enabled"] = not cfg.get("enabled")
            save_config(sess, cfg)
            await cq.message.edit_reply_markup(reply_markup=get_main_keyboard(sess))

        elif data == "cfg_target":
            user_states[chat_id]["step"] = "WAIT_TARGET"
            kb = [[InlineKeyboardButton("🔙 Отмена", callback_data="cfg_main")]]
            await cq.message.edit_text("🎯 **Настройка цели**\n\nОтправь мне сообщением логин пользователя и сумму через пробел.\n\n**Пример:** `@username 5000`", reply_markup=InlineKeyboardMarkup(kb))

        elif data == "cfg_debug":
            uptime = int(time.time() - START_TIME)
            u_h, u_m, u_s = uptime // 3600, (uptime % 3600) // 60, uptime % 60
            t_st = f"✅ {cfg['tcard_interval']} мин." if cfg.get("tcard_enabled") else "❌ выкл"
            if cfg.get("buy_enabled") and cfg.get("buy_rarity"):
                rlabel = next((l for l, k, c in RARITIES if k == cfg["buy_rarity"]), cfg["buy_rarity"])
                b_st = f"✅ {rlabel} × {cfg.get('buy_count', 1)}"
            else: b_st = "❌ выкл"
            bot_st = "🟢 Активен" if cfg.get("enabled") else "🔴 Остановлен"
            
            text = (
                f"🛠 **Системный Дебаг**\n"
                f"───────────────────\n"
                f"⏱️ **Аптайм ядра:** {u_h}ч {u_m}м {u_s}с\n"
                f"───────────────────\n"
                f"⚙️ **Активная сессия:** `{sess}`\n"
                f"  🤖 Статус: {bot_st}\n"
                f"  🎯 Получатель: {cfg.get('target_user') or '❌ не задана'} ({cfg.get('target_amount') or 0})\n"
                f"  🃏 ТКарточка: {t_st}\n"
                f"  🛍 Закупка: {b_st}\n"
            )
            kb = [[InlineKeyboardButton("🔙 Назад в меню", callback_data="cfg_main")]]
            await cq.message.edit_text(text, reply_markup=InlineKeyboardMarkup(kb))

        elif data == "cfg_tcardmenu":
            kb = [[InlineKeyboardButton("🚫 Отключить ТКарточку", callback_data="cfg_tcardset_0")]]
            row = []
            for m in TCARD_INTERVALS:
                row.append(InlineKeyboardButton(f"{m} мин", callback_data=f"cfg_tcardset_{m}"))
                if len(row) == 2:
                    kb.append(row)
                    row = []
            if row: kb.append(row)
            kb.append([InlineKeyboardButton("🔙 Назад", callback_data="cfg_main")])
            await cq.message.edit_text("🃏 **ТКарточка**\n\nВыбери подходящий кулдаун (погрешность в 5 минут уже заложена).", reply_markup=InlineKeyboardMarkup(kb))

        elif data.startswith("cfg_tcardset_"):
            val = int(data.split("_")[2])
            cfg["tcard_enabled"] = (val > 0)
            cfg["tcard_interval"] = val
            save_config(sess, cfg)
            await cq.message.edit_text(f"✅ Интервал ТКарточки обновлен.", reply_markup=get_main_keyboard(sess))

        elif data == "cfg_buymenu":
            kb = [[InlineKeyboardButton("🚫 Отключить авто-покупку", callback_data="cfg_buyset_OFF_0")]]
            row = []
            for label, key, short in RARITIES:
                row.append(InlineKeyboardButton(label, callback_data=f"cfg_buyrar_{short}"))
                if len(row) == 2:
                    kb.append(row)
                    row = []
            if row: kb.append(row)
            kb.append([InlineKeyboardButton("🔙 Назад", callback_data="cfg_main")])
            await cq.message.edit_text("🛍 **Авто-покупка**\n\nВыбери редкость приобретаемых устройств:", reply_markup=InlineKeyboardMarkup(kb))

        elif data.startswith("cfg_buyrar_"):
            short = data.split("_")[2]
            label = next((l for l, k, c in RARITIES if c == short), "Неизвестно")
            kb = []
            row = []
            for i in range(1, 26):
                row.append(InlineKeyboardButton(str(i), callback_data=f"cfg_buyset_{short}_{i}"))
                if len(row) == 5:
                    kb.append(row)
                    row = []
            kb.append([InlineKeyboardButton("🔙 Назад к редкостям", callback_data="cfg_buymenu")])
            await cq.message.edit_text(f"🛍 Редкость: **{label}**\n\nВыбери количество за один клик:", reply_markup=InlineKeyboardMarkup(kb))

        elif data.startswith("cfg_buyset_"):
            short, qty = data.split("_")[2], data.split("_")[3]
            if short == "OFF":
                cfg["buy_enabled"] = False
            else:
                cfg["buy_enabled"] = True
                cfg["buy_rarity"] = next((k for l, k, c in RARITIES if c == short), None)
                cfg["buy_count"] = int(qty)
            save_config(sess, cfg)
            await cq.message.edit_text("✅ Настройки магазина сохранены.", reply_markup=get_main_keyboard(sess))

        elif data.startswith("cfg_sesslist_"):
            page = int(data.split("_")[2])
            sessions = [s for s in active_clients.keys() if s != "master_bot"]
            per_page = 10
            total_pages = max(1, (len(sessions) + per_page - 1) // per_page)
            current_sessions = sessions[page * per_page : (page + 1) * per_page]
            
            text = f"📋 **АКТИВНЫЕ СЕССИИ (Стр. {page+1}/{total_pages})**\n\n"
            for s in current_sessions: text += f"• `{s}`\n"
            
            nav_row = []
            if page > 0: nav_row.append(InlineKeyboardButton("⬅️ Пред", callback_data=f"cfg_sesslist_{page-1}"))
            if page < total_pages - 1: nav_row.append(InlineKeyboardButton("След ➡️", callback_data=f"cfg_sesslist_{page+1}"))
            
            kb = [nav_row] if nav_row else []
            kb.append([InlineKeyboardButton("🔙 Назад в меню", callback_data="cfg_main")])
            await cq.message.edit_text(text, reply_markup=InlineKeyboardMarkup(kb))

        elif data.startswith("cfg_sessmanage_"):
            page = int(data.split("_")[2])
            sessions = [s for s in active_clients.keys() if s != "master_bot"]
            per_page = 10
            total_pages = max(1, (len(sessions) + per_page - 1) // per_page)
            current_sessions = sessions[page * per_page : (page + 1) * per_page]
            
            kb = [[InlineKeyboardButton(f"⚙️ {s}", callback_data=f"cfg_select_{s}")] for s in current_sessions]
            
            nav_row = []
            if page > 0: nav_row.append(InlineKeyboardButton("⬅️", callback_data=f"cfg_sessmanage_{page-1}"))
            if page < total_pages - 1: nav_row.append(InlineKeyboardButton("➡️", callback_data=f"cfg_sessmanage_{page+1}"))
            if nav_row: kb.append(nav_row)
            
            kb.append([InlineKeyboardButton("🔙 Назад в меню", callback_data="cfg_main")])
            await cq.message.edit_text(f"🗂 **МЕНЕДЖЕР СЕССИЙ (Стр. {page+1}/{total_pages})**\n\nВыбери сессию для управления:", reply_markup=InlineKeyboardMarkup(kb))

        elif data.startswith("cfg_select_"):
            selected_sess = data.replace("cfg_select_", "")
            user_states[chat_id]["editing_sess"] = selected_sess
            kb = [
                [InlineKeyboardButton("📝 Переименовать", callback_data="cfg_rename")],
                [InlineKeyboardButton("🗑 Удалить навсегда", callback_data="cfg_delete")],
                [InlineKeyboardButton("🔙 Вернуться к списку", callback_data="cfg_sessmanage_0")]
            ]
            await cq.message.edit_text(f"🗂 **Управление сессией:** `{selected_sess}`", reply_markup=InlineKeyboardMarkup(kb))

        elif data == "cfg_rename":
            user_states[chat_id]["step"] = "WAIT_RENAME"
            kb = [[InlineKeyboardButton("🔙 Отмена", callback_data="cfg_main")]]
            await cq.message.edit_text(f"📝 **Переименование**\n\nТекущее имя: `{sess}`\nОтправь сообщением новое имя (без пробелов):", reply_markup=InlineKeyboardMarkup(kb))

        elif data == "cfg_delete":
            kb = [
                [InlineKeyboardButton("⚠️ ДА, УДАЛИТЬ", callback_data="cfg_confirmdel")],
                [InlineKeyboardButton("❌ Отмена", callback_data="cfg_main")]
            ]
            await cq.message.edit_text(f"🗑 **ВНИМАНИЕ!**\nВы собираетесь безвозвратно удалить сессию `{sess}` с сервера.\n\nПродолжить?", reply_markup=InlineKeyboardMarkup(kb))

        elif data == "cfg_confirmdel":
            if sess in active_clients:
                try: await active_clients[sess].stop()
                except Exception: pass
                del active_clients[sess]
            
            p_sess = os.path.join(SESSIONS_DIR, f"{sess}.session")
            p_conf = get_config_path(sess)
            if os.path.exists(p_sess): os.remove(p_sess)
            if os.path.exists(p_conf): os.remove(p_conf)
            
            # Сброс выбранной сессии
            fallback = [s for s in active_clients.keys() if s != "master_bot"]
            new_sess = fallback[0] if fallback else None
            user_states[chat_id]["editing_sess"] = new_sess
            
            if new_sess:
                await cq.message.edit_text(f"✅ Сессия `{sess}` уничтожена.", reply_markup=get_main_keyboard(new_sess))
            else:
                await cq.message.edit_text(f"✅ Сессия `{sess}` уничтожена. Активных аккаунтов больше нет. Используй /auth")

        try: await cq.answer()
        except Exception: pass

# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────
async def main():
    if not API_ID or not API_HASH:
        print("КРИТИЧЕСКАЯ ОШИБКА: Не заданы API_ID или API_HASH!")
        return

    loop = asyncio.get_event_loop()
    loop.set_exception_handler(custom_exception_handler)

    session_envs = {k: v for k, v in os.environ.items() if k.startswith("SESSION_STRING")}
    for key, string_value in session_envs.items():
        if not string_value.strip(): continue
        s_name = key.lower()
        if s_name in active_clients: continue
        try:
            client = Client(name=s_name, session_string=string_value.strip(), api_id=int(API_ID), api_hash=API_HASH, plugins=None, in_memory=True)
            @client.on_message(filters.chat(GAME_BOT))
            async def b_handler(c, m): await handle_bot_message(c, m)
            await client.start()
            try:
                async for _ in client.get_dialogs(limit=20): pass
            except Exception: pass
            active_clients[s_name] = client
            asyncio.create_task(tcard_worker(client, s_name))
            asyncio.create_task(daily_and_mining_worker(client, s_name))
            print(f"Аккаунт из переменных {s_name} запущен.")
        except Exception as e: print(f"Ошибка инициализации {key}: {e}")

    asyncio.create_task(init_existing_sessions())

    if BOT_TOKEN:
        print("Запуск Сервисного Бот-Интерфейса…")
        try:
            bot_client = Client(
                name="master_bot_v2",  # <-- Изменили имя с "master_bot" на "master_bot_v2"
                api_id=int(API_ID),
                api_hash=API_HASH,
                bot_token=BOT_TOKEN,
                workdir=SESSIONS_DIR
            )
            setup_bot_handlers(bot_client)
            await bot_client.start()
            print("Сервисный бот онлайн и готов принимать команды.")
        except Exception as e: print(f"КРИТИЧЕСКАЯ ОШИБКА СЕРВИСНОГО БОТА: {e}")
    else:
        print("Внимание: BOT_TOKEN не задан.")

    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())