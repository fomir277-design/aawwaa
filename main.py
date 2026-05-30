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
        if isinstance(exc, ValueError) and "Peer id invalid" in exc_str:
            return
        if isinstance(exc, KeyError) and "ID not found" in exc_str:
            return
    if "Peer id invalid" in msg or "ID not found" in msg:
        return
    loop.default_exception_handler(context)
# ------------------------------------------

sys.stdout.reconfigure(line_buffering=True)
START_TIME = time.time()

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
buy_states     = {}   # session_name -> {step, rarity, count, started}

# Редкости для авто-покупки
RARITIES = [
    ("Ширпотрёб",     "Ширпотреб",     "S"),
    ("Необычный",     "Необычный",     "N"),
    ("Редкий",        "Редкий",        "R"),
    ("Мистический",   "Мистический",   "M"),
    ("Хроматический", "Хроматический", "C"),
    ("Аркана",        "Аркана",        "A"),
    ("Платиновый",    "Платиновый",    "P"),
]

# Доступные интервалы ТКарточки (мин.)
TCARD_INTERVALS = [185, 175, 165, 155, 145, 135, 125, 65]

# ──────────────────────────────────────────────
# Работа с конфигурацией
# ──────────────────────────────────────────────

def get_config_path(session_name):
    return os.path.join(CONFIGS_DIR, f"{session_name}.json")

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
        with open(path, "r", encoding="utf-8") as f:
            saved = json.load(f)
        defaults.update(saved)
    return defaults

def save_config(session_name, config):
    path = get_config_path(session_name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=4)

def find_session_by_user_id(user_id):
    """Автоматически находит имя сессии по Telegram ID пользователя"""
    for sess_name, client in active_clients.items():
        if client.me and client.me.id == user_id:
            return sess_name
    return None

# ──────────────────────────────────────────────
# Вспомогательные задачи (Воркеры)
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
        print(f"[{session_name}] 🛍 Открываем магазин для авто-покупки…")
    except Exception as e:
        print(f"[{session_name}] Ошибка открытия магазина: {e}")
        buy_states.pop(session_name, None)

# ──────────────────────────────────────────────
# Обработка сообщений от игрового бота PhoneGet
# ──────────────────────────────────────────────

async def handle_bot_message(client: Client, message: Message):
    if not message.chat or message.chat.username != GAME_BOT:
        return

    session_name = client.name
    config = load_config(session_name)
    if not config.get("enabled"):
        return

    if not message.reply_markup or not message.reply_markup.inline_keyboard:
        return

    def cb_str(button):
        raw = button.callback_data
        if raw is None:
            return ""
        return raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)

    # ── Шаги авто-покупки ──────────────────────────────────────────────────
    buy_state = buy_states.get(session_name)
    if buy_state:
        if time.time() - buy_state.get("started", 0) > 300:
            print(f"[{session_name}] ⚠️ Авто-покупка: таймаут, сбрасываем состояние.")
            buy_states.pop(session_name, None)
        else:
            rarity = buy_state["rarity"]
            count  = buy_state["count"]
            step   = buy_state["step"]

            step_target = {
                "wait_rarity":  f"shop_rarity_{rarity}",
                "wait_phone":   f"shop_phone_{rarity}_1",
                "wait_propose": f"shop_propose_bulk_{rarity}_1",
                "wait_bulk":    f"shop_bulk_select_{rarity}_1_{count}",
                "wait_confirm": f"shop_confirm_bulk_{rarity}_1_{count}",
            }
            step_next = {
                "wait_rarity":  "wait_phone",
                "wait_phone":   "wait_propose",
                "wait_propose": "wait_bulk",
                "wait_bulk":    "wait_confirm",
                "wait_confirm": "done",
            }

            target_cb = step_target.get(step)
            if target_cb:
                for row in message.reply_markup.inline_keyboard:
                    for btn in row:
                        if cb_str(btn) == target_cb:
                            try:
                                await client.request_callback_answer(
                                    chat_id=message.chat.id,
                                    message_id=message.id,
                                    callback_data=btn.callback_data
                                )
                                nxt = step_next[step]
                                if nxt == "done":
                                    print(f"[{session_name}] ✅ Авто-покупка {count}× {rarity} завершена!")
                                    buy_states.pop(session_name, None)
                                else:
                                    buy_states[session_name]["step"] = nxt
                                    print(f"[{session_name}] 🛍 Шаг покупки: {step} → {nxt}")
                            except Exception as e:
                                print(f"[{session_name}] Ошибка шага покупки [{step}]: {e}")
                                buy_states.pop(session_name, None)
                            return

    # ── Стандартные игровые кнопки ─────────────────────────────────────────
    for row in message.reply_markup.inline_keyboard:
        for btn in row:
            cbs = cb_str(btn)

            # 1. Авто-сбор фермы
            if "farm_claim" in cbs:
                try:
                    await client.request_callback_answer(
                        chat_id=message.chat.id,
                        message_id=message.id,
                        callback_data=btn.callback_data
                    )
                    print(f"[{session_name}] ✅ Ферма собрана")
                    asyncio.create_task(delayed_payment(client, session_name))

                    cfg = load_config(session_name)
                    if cfg.get("buy_enabled") and cfg.get("buy_rarity") and cfg.get("buy_count"):
                        buy_states[session_name] = {
                            "step":    "wait_rarity",
                            "rarity":  cfg["buy_rarity"],
                            "count":   cfg["buy_count"],
                            "started": time.time(),
                        }
                        asyncio.create_task(send_shop_command(client, session_name))
                except Exception as e:
                    print(f"[{session_name}] Ошибка клика фермы: {e}")

            # 2. Ежедневная награда
            elif config.get("eday_enabled") and ("confirm_daily_claim" in cbs or "Забрать" in str(btn.text)):
                try:
                    if btn.callback_data:
                        await client.request_callback_answer(
                            chat_id=message.chat.id,
                            message_id=message.id,
                            callback_data=btn.callback_data
                        )
                    else:
                        await message.click(btn.text)
                    print(f"[{session_name}] ✅ Ежедневная награда зафиксирована")
                except Exception as e:
                    print(f"[{session_name}] Ошибка сбора ежедневки: {e}")

            # 3. Подтверждение исходящих переводов
            elif cbs.startswith("pay_confirm_"):
                try:
                    parts = cbs.split("_")
                    if len(parts) >= 5:
                        btn_target_id = int(parts[2])
                        btn_amount    = int(parts[3])
                        btn_sender_id = int(parts[4])

                        my_id       = client.me.id
                        conf_amount = config.get("target_amount", 0)

                        if btn_sender_id == my_id and btn_amount == conf_amount:
                            target_match = False
                            if "target_user_id" in config:
                                target_match = (btn_target_id == config["target_user_id"])
                            else:
                                conf_target = config.get("target_user")
                                if conf_target:
                                    try:
                                        t_user = await client.get_users(conf_target)
                                        config["target_user_id"] = t_user.id
                                        save_config(session_name, config)
                                        target_match = (btn_target_id == t_user.id)
                                    except Exception:
                                        pass

                            if target_match:
                                await client.request_callback_answer(
                                    chat_id=message.chat.id,
                                    message_id=message.id,
                                    callback_data=btn.callback_data
                                )
                                print(f"[{session_name}] ✅ Подтверждён перевод {btn_amount} ТОчек")
                except Exception as e:
                    print(f"[{session_name}] Ошибка подтверждения перевода: {e}")

# ──────────────────────────────────────────────
# Текстовые команды ЮЗЕРБОТА
# ──────────────────────────────────────────────

async def handle_user_commands(client: Client, message: Message):
    if not message.text:
        return

    text    = message.text.strip()
    parts   = text.split()
    if not parts:
        return

    command      = parts[0].lower()
    session_name = client.name
    config       = load_config(session_name)

    if command in [".on", ".вкл"]:
        config["enabled"] = True
        save_config(session_name, config)
        await message.edit_text("✅ **Юзербот включён** и активен.")

    elif command in [".off", ".выкл"]:
        config["enabled"] = False
        save_config(session_name, config)
        await message.edit_text("❌ **Юзербот переведён в спящий режим.**")

    elif command in [".target", ".цель"]:
        if len(parts) >= 3:
            target_user = parts[1]
            try:
                target_amount = int(parts[2])
                config["target_user"]   = target_user
                config["target_amount"] = target_amount

                msg = await message.edit_text("⏳ Привязываем цель…")
                try:
                    t_user_obj = await client.get_users(target_user)
                    config["target_user_id"] = t_user_obj.id
                    save_config(session_name, config)
                    await msg.edit_text(
                        f"✅ **Получатель настроен:**\n"
                        f"🎯 Юзер: {target_user} (ID: `{t_user_obj.id}`)\n"
                        f"💰 Сумма: {target_amount} ТОчек"
                    )
                except Exception:
                    save_config(session_name, config)
                    await msg.edit_text(
                        f"✅ **Настройки сохранены** *(ID определится при первой транзакции)*\n"
                        f"🎯 Юзер: {target_user}\n"
                        f"💰 Сумма: {target_amount} ТОчек"
                    )
            except ValueError:
                await message.edit_text("❌ Сумма перевода должна быть числом.\nПример: `.цель @username 500`")
        else:
            await message.edit_text("⚠️ Использование: `.цель @username <сумма>`")

    elif command in [".tcard", ".ткарточка", ".buy", ".купить"]:
        await message.edit_text("⚙️ **Настройки перенесены!**\nУправляй кнопками прямо в личке официального бота: @phonegetubot через команду `/config`.")

    elif command in [".eday", ".ежедн"]:
        config["eday_enabled"] = not config.get("eday_enabled", False)
        save_config(session_name, config)
        status = "включён" if config["eday_enabled"] else "выключен"
        await message.edit_text(f"🎁 Автосбор ежедневки: **{status}**.")

    elif command in [".debug", ".дебаг"]:
        req_start = time.time()
        await message.edit_text("⏳ Считываем системные метрики…")

        ping_ms  = round((time.time() - req_start) * 1000)
        uptime   = int(time.time() - START_TIME)
        u_h, u_m, u_s = uptime // 3600, (uptime % 3600) // 60, uptime % 60

        target = config.get("target_user")  or "❌ не задана"
        amount = config.get("target_amount") or "❌ не задана"
        tcard_st = f"✅ каждые {config['tcard_interval']} мин." if config.get("tcard_enabled") else "❌ выкл"
        eday_st = "✅ вкл" if config.get("eday_enabled") else "❌ выкл"
        bot_st  = "✅ запущен" if config.get("enabled") else "❌ остановлен"

        if config.get("buy_enabled") and config.get("buy_rarity"):
            rkey = config["buy_rarity"]
            rlabel = next((l for l, k, c in RARITIES if k == rkey), rkey)
            buy_st = f"✅ {rlabel} × {config.get('buy_count', 1)}"
        else:
            buy_st = "❌ выкл"

        await message.edit_text(
            f"🛠 **PGUB Debug Manger**\n"
            f"───────────────────\n"
            f"⏱️ **Аптайм:** {u_h}ч {u_m}м {u_s}с\n"
            f"📡 **Пинг:** {ping_ms} мс\n"
            f"───────────────────\n"
            f"⚙️ **Конфиг сессии ({session_name}):**\n"
            f"  🤖 Робот: {bot_st}\n"
            f"  🎯 Получатель: {target}\n"
            f"  💰 Сумма: {amount}\n"
            f"  🃏 ТКарточка: {tcard_st}\n"
            f"  🎁 Ежедневка: {eday_st}\n"
            f"  🛍 Закупка: {buy_st}\n"
        )

# ──────────────────────────────────────────────
# Фоновые циклы юзербота
# ──────────────────────────────────────────────

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
        except Exception as e:
            print(f"[{session_name}] Ошибка воркера ткарточки: {e}")

async def daily_and_mining_worker(client: Client, session_name: str):
    msk = pytz.timezone("Europe/Moscow")
    while True:
        try:
            config = load_config(session_name)
            if config.get("enabled"):
                now       = datetime.now(msk)
                last_run  = config.get("last_mining_date", "")
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
        except Exception as e:
            print(f"[{session_name}] Ошибка воркера майнинга: {e}")
        await asyncio.sleep(60)

# ──────────────────────────────────────────────
# Запуск инстансов юзерботов
# ──────────────────────────────────────────────

async def launch_userbot_instance(session_name):
    if session_name in active_clients: return
    try:
        client = Client(name=session_name, workdir=SESSIONS_DIR, api_id=int(API_ID), api_hash=API_HASH, plugins=None)

        @client.on_message(filters.me)
        async def u_handler(c, m): await handle_user_commands(c, m)

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
    except Exception as e:
        print(f"Критическая ошибка старта юзербота {session_name}: {e}")

async def init_existing_sessions():
    await asyncio.sleep(2)
    files = [f for f in os.listdir(SESSIONS_DIR) if f.endswith(".session")]
    for f in files:
        s_name = f.replace(".session", "")
        if s_name in ["auth_manager_bot", "master_bot"]: continue
        print(f"Авто-старт сессии: {s_name}…")
        asyncio.create_task(launch_userbot_instance(s_name))

# ──────────────────────────────────────────────
# СЕРВИСНЫЙ БОТ (MASTER BOT @phonegetubot)
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

def setup_bot_handlers(bot: Client):
    @bot.on_message(filters.command("start") & filters.private)
    async def start_cmd(c, m):
        user_states[m.chat.id] = {"step": "IDLE"}
        await m.reply_text(
            "👾 **Добро пожаловать в панель PGUB**\n\n"
            "• Чтобы привязать новый аккаунт, просто отправь номер телефона (напр. +79991234567).\n"
            "• Для изменения параметров работы используй команду /config"
        )

    # === АВТОМАТИЧЕСКАЯ НАСТРОЙКА (БЕЗ ВЫБОРА СЕССИЙ) ===
    @bot.on_message(filters.command("config") & filters.private)
    async def config_cmd(c, m):
        sess = find_session_by_user_id(m.chat.id)
        if not sess:
            await m.reply_text("❌ **Активная сессия не обнаружена.**\nПривяжи свой аккаунт юзербота, отправив номер телефона.")
            return
            
        cfg = load_config(sess)
        t_st = f"каждые {cfg.get('tcard_interval')} мин." if cfg.get("tcard_enabled") else "Выкл"
        
        if cfg.get("buy_enabled") and cfg.get("buy_rarity"):
            rkey = cfg["buy_rarity"]
            rlabel = next((l for l, k, c in RARITIES if k == rkey), rkey)
            b_st = f"{rlabel} × {cfg.get('buy_count', 1)} шт."
        else:
            b_st = "Выкл"
            
        kb = [
            [InlineKeyboardButton("🃏 Настройка ТКарточки", callback_data="cfg_tcardmenu")],
            [InlineKeyboardButton("🛍 Настройка Авто-покупки", callback_data="cfg_buymenu")]
        ]
        await m.reply_text(
            f"⚙️ **Панель управления PGUB**\n\n"
            f"👤 Текущий аккаунт: `{sess}`\n"
            f"🃏 ТКарточка: **{t_st}**\n"
            f"🛍 Авто-покупка: **{b_st}**\n\n"
            f"Выбери раздел для изменения параметров:",
            reply_markup=InlineKeyboardMarkup(kb)
        )

    @bot.on_callback_query(filters.regex(r"^cfg_"))
    async def config_callbacks(c, cq: CallbackQuery):
        sess = find_session_by_user_id(cq.from_user.id)
        if not sess:
            await cq.answer("❌ Сессия не найдена. Перезапустите бота через /start", show_alert=True)
            return
            
        parts = cq.data.split("_")
        action = parts[1]
        
        if action == "main":
            cfg = load_config(sess)
            t_st = f"каждые {cfg.get('tcard_interval')} мин." if cfg.get("tcard_enabled") else "Выкл"
            if cfg.get("buy_enabled") and cfg.get("buy_rarity"):
                rkey = cfg["buy_rarity"]
                rlabel = next((l for l, k, c in RARITIES if k == rkey), rkey)
                b_st = f"{rlabel} × {cfg.get('buy_count', 1)} шт."
            else:
                b_st = "Выкл"
                
            kb = [
                [InlineKeyboardButton("🃏 Настройка ТКарточки", callback_data="cfg_tcardmenu")],
                [InlineKeyboardButton("🛍 Настройка Авто-покупки", callback_data="cfg_buymenu")]
            ]
            await cq.message.edit_text(
                f"⚙️ **Панель управления PGUB**\n\n"
                f"👤 Текущий аккаунт: `{sess}`\n"
                f"🃏 ТКарточка: **{t_st}**\n"
                f"🛍 Авто-покупка: **{b_st}**",
                reply_markup=InlineKeyboardMarkup(kb)
            )
            
        elif action == "tcardmenu":
            kb = [[InlineKeyboardButton("🚫 Выключить ТКарточку", callback_data="cfg_tcardset_0")]]
            row = []
            for m in TCARD_INTERVALS:
                row.append(InlineKeyboardButton(f"{m} мин", callback_data=f"cfg_tcardset_{m}"))
                if len(row) == 2:
                    kb.append(row)
                    row = []
            if row: kb.append(row)
            kb.append([InlineKeyboardButton("🔙 Назад", callback_data="cfg_main")])
            
            await cq.message.edit_text(
                f"🃏 **ТКарточка**\n\n"
                f"Установи интервал отправки команды (зависит от твоих улучшений).\n\n"
                f"Кулдаун команды — 180 мин., погрешность со стороны PhoneGet — 5 мин.", 
                reply_markup=InlineKeyboardMarkup(kb)
            )
            
        elif action == "tcardset":
            val = int(parts[2])
            cfg = load_config(sess)
            cfg["tcard_enabled"] = (val > 0)
            cfg["tcard_interval"] = val
            save_config(sess, cfg)
            
            st = f"каждые {val} мин." if val > 0 else "отключена"
            kb = [[InlineKeyboardButton("🔙 Главное меню", callback_data="cfg_main")]]
            await cq.message.edit_text(f"✅ Ввод ТКарточки успешно переведён в режим: **{st}**.", reply_markup=InlineKeyboardMarkup(kb))
            
        elif action == "buymenu":
            kb = [[InlineKeyboardButton("🚫 Отключить авто-покупку", callback_data="cfg_buyset_OFF_0")]]
            row = []
            for label, key, short in RARITIES:
                row.append(InlineKeyboardButton(label, callback_data=f"cfg_buyrar_{short}"))
                if len(row) == 2:
                    kb.append(row)
                    row = []
            if row: kb.append(row)
            kb.append([InlineKeyboardButton("🔙 Назад", callback_data="cfg_main")])
            
            await cq.message.edit_text(f"🛍 **Авто-покупка телефонов**\n\nКакую категорию девайсов приобретать при каждом цикле сбора фермы?", reply_markup=InlineKeyboardMarkup(kb))
            
        elif action == "buyrar":
            short_code = parts[2]
            label = next((l for l, k, c in RARITIES if c == short_code), "Неизвестно")
            
            kb = []
            row = []
            for i in range(1, 26):
                row.append(InlineKeyboardButton(str(i), callback_data=f"cfg_buyset_{short_code}_{i}"))
                if len(row) == 5:
                    kb.append(row)
                    row = []
            kb.append([InlineKeyboardButton("🔙 Назад", callback_data="cfg_buymenu")])
            
            await cq.message.edit_text(f"🛍 Выбрано: **{label}**\n\nУкажи объем закупки (количество за один раз):", reply_markup=InlineKeyboardMarkup(kb))
            
        elif action == "buyset":
            short_code = parts[2]
            qty = parts[3]
            cfg = load_config(sess)
            
            if short_code == "OFF":
                cfg["buy_enabled"] = False
                text = "✅ Авто-покупка устройств успешно деактивирована."
            else:
                cfg["buy_enabled"] = True
                real_key = next((k for l, k, c in RARITIES if c == short_code), None)
                cfg["buy_rarity"] = real_key
                cfg["buy_count"] = int(qty)
                label = next((l for l, k, c in RARITIES if c == short_code), "Неизвестно")
                text = f"✅ **Авто-покупка настроена на ура!**\n\nТеперь после сбора прибыли бот возьмет:\n📦 Редкость: **{label}**\n🔢 Количество: **{qty} шт.**"
                
            save_config(sess, cfg)
            kb = [[InlineKeyboardButton("🔙 Главное меню", callback_data="cfg_main")]]
            await cq.message.edit_text(text, reply_markup=InlineKeyboardMarkup(kb))
            
        try: await cq.answer()
        except: pass

    # === ИНТЕРАКТИВНЫЙ ВВОД КОДА АВТОРИЗАЦИИ ===
    @bot.on_message(filters.text & filters.private)
    async def process_auth(c, m):
        if m.text.startswith("/"): return
        
        chat_id = m.chat.id
        text    = m.text.strip()
        state   = user_states.get(chat_id, {"step": "IDLE"})
        step    = state.get("step")

        if step == "IDLE":
            if text.startswith("+") and len(text) > 9:
                phone        = text.replace(" ", "")
                session_name = f"user_{phone.replace('+', '')}"
                await m.reply_text("Устанавливаю соединение с Telegram… ⏳")

                client = Client(name=session_name, workdir=SESSIONS_DIR, api_id=int(API_ID), api_hash=API_HASH, in_memory=False)
                try:
                    await client.connect()
                    code_info = await client.send_code(phone)
                    user_states[chat_id] = {
                        "step":            "WAIT_CODE",
                        "phone":           phone,
                        "session_name":    session_name,
                        "client":          client,
                        "phone_code_hash": code_info.phone_code_hash,
                        "entered_code":    ""
                    }
                    await m.reply_text(
                        f"📲 Сгенерирован код для номера {phone}.\n\n"
                        f"**Ввод:** {format_code_display('')}\n\n"
                        f"Набирай цифры на кнопках ниже:",
                        reply_markup=get_pin_keyboard()
                    )
                except FloodWait as e:
                    await m.reply_text(f"⚠️ Ограничение Telegram. Подожди {e.value} сек.")
                    await client.disconnect()
                except Exception as e:
                    await m.reply_text(f"❌ Ошибка отправки запроса: {e}")
                    await client.disconnect()
            else:
                await m.reply_text("Номер должен начинаться с знака плюс, например: +79991234567")

        elif step == "WAIT_CODE":
            msg = await m.reply_text("⚠️ Вводи код кнопками выше на экране.")
            await asyncio.sleep(3)
            await msg.delete()

        elif step == "WAIT_PASSWORD":
            client       = state["client"]
            session_name = state["session_name"]
            try:
                await client.check_password(text)
                await m.reply_text("✅ Облачный пароль подтверждён. Юзербот инициализирован!")
                await client.disconnect()
                user_states[chat_id] = {"step": "IDLE"}
                asyncio.create_task(launch_userbot_instance(session_name))
            except Exception as e:
                await m.reply_text(f"❌ Сбой проверки 2FA: {e}. Перезапусти процесс через /start")
                await client.disconnect()
                user_states[chat_id] = {"step": "IDLE"}

    @bot.on_callback_query(filters.regex(r"^pin_"))
    async def pin_callback(c, cq: CallbackQuery):
        chat_id = cq.message.chat.id
        state   = user_states.get(chat_id)

        if not state or state.get("step") != "WAIT_CODE":
            await cq.answer("Сессия устарела. Нажми /start", show_alert=True)
            return

        action       = cq.data.split("_")[1]
        current_code = state.get("entered_code", "")
        client       = state["client"]

        if action == "cancel":
            await client.disconnect()
            user_states[chat_id] = {"step": "IDLE"}
            await cq.message.edit_text("🛑 Сессия авторизации прервана.")
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
            await cq.message.edit_text(f"🔐 Проверяем код: {format_code_display(current_code)} …")
            try:
                phone            = state["phone"]
                phone_code_hash  = state["phone_code_hash"]
                session_name     = state["session_name"]

                await client.sign_in(phone, phone_code_hash, current_code)
                await cq.message.edit_text("✅ Профиль успешно синхронизирован! Юзербот запущен.\n\nНабери /config чтобы управлять им.")
                await client.disconnect()
                user_states[chat_id] = {"step": "IDLE"}
                asyncio.create_task(launch_userbot_instance(session_name))

            except SessionPasswordNeeded:
                user_states[chat_id]["step"] = "WAIT_PASSWORD"
                await cq.message.edit_text("🔒 Обнаружена двухфакторная аутентификация (2FA).\n\nНапиши ответным сообщением пароль от аккаунта:")
            except (PhoneCodeInvalid, PhoneCodeExpired):
                await cq.message.edit_text("❌ Код не прошёл валидацию. Попробуй заново через /start")
                await client.disconnect()
                user_states[chat_id] = {"step": "IDLE"}
            except Exception as e:
                await cq.message.edit_text(f"❌ Критический сбой авторизации: {e}")
                await client.disconnect()
                user_states[chat_id] = {"step": "IDLE"}
        else:
            try:
                await cq.message.edit_text(
                    f"📲 Сгенерирован код для номера {state['phone']}.\n\n"
                    f"**Ввод:** {format_code_display(current_code)}\n\n"
                    f"Набирай цифры на кнопках ниже:",
                    reply_markup=get_pin_keyboard()
                )
            except Exception: pass
            await cq.answer()

# ──────────────────────────────────────────────
# Main Точка входа
# ──────────────────────────────────────────────

async def main():
    if not API_ID or not API_HASH:
        print("КРИТИЧЕСКАЯ ОШИБКА: Не заданы API_ID или API_HASH в переменных окружения!")
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

            @client.on_message(filters.me)
            async def u_handler(c, m): await handle_user_commands(c, m)

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
        except Exception as e:
            print(f"Ошибка инициализации {key}: {e}")

    asyncio.create_task(init_existing_sessions())

    if BOT_TOKEN:
        print("Запуск Сервисного Бот-Интерфейса…")
        try:
            bot_client = Client(name="master_bot", api_id=int(API_ID), api_hash=API_HASH, bot_token=BOT_TOKEN, workdir=SESSIONS_DIR)
            setup_bot_handlers(bot_client)
            await bot_client.start()
            print("Сервисный бот онлайн и готов принимать авторизации.")
        except Exception as e: 
            print(f"КРИТИЧЕСКАЯ ОШИБКА СЕРВИСНОГО БОТА: {e}")
    else:
        print("Внимание: BOT_TOKEN не задан.")

    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())