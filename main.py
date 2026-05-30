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

# Редкости: (метка UI, ключ в callback-данных игры)
RARITIES = [
    ("Ширпотрёб",     "Ширпотреб"),
    ("Необычный",     "Необычный"),
    ("Редкий",        "Редкий"),
    ("Мистический",   "Мистический"),
    ("Хроматический", "Хроматический"),
    ("Аркана",        "Аркана"),
    ("Платиновый",    "Платиновый"),
]

# Доступные интервалы ТКарточки (мин.)
TCARD_INTERVALS = [185, 175, 165, 155, 145, 135, 125, 65]

# ──────────────────────────────────────────────
# Конфиг
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

# ──────────────────────────────────────────────
# Вспомогательные корутины
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
    """Открывает магазин телефонов — первый шаг авто-покупки."""
    await asyncio.sleep(5)
    try:
        await client.send_message(GAME_BOT, "Магазин телефонов")
        print(f"[{session_name}] 🛍 Открываем магазин для авто-покупки…")
    except Exception as e:
        print(f"[{session_name}] Ошибка открытия магазина: {e}")
        buy_states.pop(session_name, None)

# ──────────────────────────────────────────────
# Обработка сообщений от игрового бота
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

    # ── Стандартные кнопки ─────────────────────────────────────────────────
    for row in message.reply_markup.inline_keyboard:
        for btn in row:
            cbs = cb_str(btn)

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
                    print(f"[{session_name}] Важная ошибка клика фермы: {e}")

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
                    print(f"[{session_name}] ✅ Ежедневная награда забрана")
                except Exception as e:
                    print(f"[{session_name}] Важная ошибка сбора ежедневки: {e}")

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
                    print(f"[{session_name}] Важная ошибка обработки перевода: {e}")

# ──────────────────────────────────────────────
# Колбэки инлайн-кнопок юзербота
# ──────────────────────────────────────────────

async def handle_userbot_callback(client: Client, cq: CallbackQuery):
    data         = cq.data
    session_name = client.name
    config       = load_config(session_name)

    try:
        if data == "ub_tcard_off":
            config["tcard_enabled"]  = False
            config["tcard_interval"] = 0
            save_config(session_name, config)
            await cq.message.edit_text(
                "🃏 **Авто-ТКарточка отключена.**\n\n"
                "Введи `.ткарточка`, чтобы включить снова."
            )

        elif data.startswith("ub_tcard_"):
            minutes = int(data[len("ub_tcard_"):])
            config["tcard_enabled"]  = True
            config["tcard_interval"] = minutes
            save_config(session_name, config)
            await cq.message.edit_text(
                f"🃏 **Авто-ТКарточка включена!**\n\n"
                f"⏱ Интервал: каждые **{minutes} мин.**\n\n"
                f"_Кулдаун команды — 180 мин., допустимая погрешность со стороны PhoneGet — 5 мин._"
            )

        elif data == "ub_buy_off":
            config["buy_enabled"] = False
            save_config(session_name, config)
            await cq.message.edit_text(
                "🛍 **Авто-покупка отключена.**\n\n"
                "Введи `.купить`, чтобы настроить снова."
            )

        elif data.startswith("ub_buy_rarity_"):
            rarity_key   = data[len("ub_buy_rarity_"):]
            rarity_label = next((l for l, k in RARITIES if k == rarity_key), rarity_key)

            count_buttons = [
                InlineKeyboardButton(str(i), callback_data=f"ub_buy_count_{rarity_key}_{i}")
                for i in range(1, 26)
            ]
            rows = [count_buttons[i:i+5] for i in range(0, 25, 5)]

            await cq.message.edit_text(
                f"🛍 Редкость: **{rarity_label}**\n\n"
                f"Сколько телефонов купить?",
                reply_markup=InlineKeyboardMarkup(rows)
            )

        elif data.startswith("ub_buy_count_"):
            rest       = data[len("ub_buy_count_"):]
            sep        = rest.rfind("_")
            rarity_key = rest[:sep]
            count      = int(rest[sep + 1:])
            rarity_label = next((l for l, k in RARITIES if k == rarity_key), rarity_key)

            config["buy_enabled"] = True
            config["buy_rarity"]  = rarity_key
            config["buy_count"]   = count
            save_config(session_name, config)

            await cq.message.edit_text(
                f"✅ **Авто-покупка настроена!**\n\n"
                f"📦 Редкость: **{rarity_label}**\n"
                f"🔢 Количество: **{count} шт.**\n\n"
                f"При каждом сборе фермы будет автоматически открываться магазин "
                f"и совершаться покупка."
            )

    except Exception as e:
        print(f"[{session_name}] Ошибка обработки колбэка '{data}': {e}")

    try:
        await cq.answer()
    except Exception:
        pass

# ──────────────────────────────────────────────
# Команды юзербота
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
        await message.edit_text("✅ **Юзербот включён** и готов к работе.")

    elif command in [".off", ".выкл"]:
        config["enabled"] = False
        save_config(session_name, config)
        await message.edit_text("❌ **Юзербот выключен.**")

    elif command in [".target", ".цель"]:
        if len(parts) >= 3:
            target_user = parts[1]
            try:
                target_amount = int(parts[2])
                config["target_user"]   = target_user
                config["target_amount"] = target_amount

                msg = await message.edit_text("⏳ Сохраняем и проверяем ID цели…")
                try:
                    t_user_obj = await client.get_users(target_user)
                    config["target_user_id"] = t_user_obj.id
                    save_config(session_name, config)
                    await msg.edit_text(
                        f"✅ **Настройки перевода сохранены:**\n"
                        f"🎯 Цель: {target_user} (ID: `{t_user_obj.id}`)\n"
                        f"💰 Сумма: {target_amount} ТОчек"
                    )
                except Exception:
                    save_config(session_name, config)
                    await msg.edit_text(
                        f"✅ **Настройки сохранены** _(ID получим при первом переводе)_\n"
                        f"🎯 Цель: {target_user}\n"
                        f"💰 Сумма: {target_amount} ТОчек"
                    )
            except ValueError:
                await message.edit_text(
                    "❌ Сумма должна быть числом.\n"
                    "Пример: `.цель @username 500`"
                )
        else:
            await message.edit_text("⚠️ Формат: `.цель @username <сумма>`")

    elif command in [".tcard", ".ткарточка"]:
        keyboard = [
            [InlineKeyboardButton("🚫 Выключить ТКарточку", callback_data="ub_tcard_off")],
            [InlineKeyboardButton("каждые 185 минут", callback_data="ub_tcard_185"), InlineKeyboardButton("каждые 175 минут", callback_data="ub_tcard_175")],
            [InlineKeyboardButton("каждые 165 минут", callback_data="ub_tcard_165"), InlineKeyboardButton("каждые 155 минут", callback_data="ub_tcard_155")],
            [InlineKeyboardButton("каждые 145 минут", callback_data="ub_tcard_145"), InlineKeyboardButton("каждые 135 минут", callback_data="ub_tcard_135")],
            [InlineKeyboardButton("каждые 125 минут", callback_data="ub_tcard_125"), InlineKeyboardButton("каждые 65 минут", callback_data="ub_tcard_65")]
        ]
        await message.edit_text(
            "🃏 **Авто-ТКарточка**\n\n"
            "Раз в сколько минут вводить ТКарточку? (зависит от уровня прокачки)\n\n"
            "_Кулдаун команды — 180 мин., допустимая погрешность со стороны PhoneGet — 5 мин._",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif command in [".buy", ".купить"]:
        keyboard = [
            [InlineKeyboardButton("🚫 Отключить авто-покупку", callback_data="ub_buy_off")],
            [InlineKeyboardButton("Ширпотрёб", callback_data="ub_buy_rarity_Ширпотреб"), InlineKeyboardButton("Необычный", callback_data="ub_buy_rarity_Необычный")],
            [InlineKeyboardButton("Редкий", callback_data="ub_buy_rarity_Редкий"), InlineKeyboardButton("Мистический", callback_data="ub_buy_rarity_Мистический")],
            [InlineKeyboardButton("Хроматический", callback_data="ub_buy_rarity_Хроматический"), InlineKeyboardButton("Аркана", callback_data="ub_buy_rarity_Аркана")],
            [InlineKeyboardButton("Платиновый", callback_data="ub_buy_rarity_Платиновый")]
        ]
        await message.edit_text(
            "🛍 **Авто-покупка телефонов**\n\n"
            "Что вы хотите покупать при каждом сборе фермы?",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif command in [".eday", ".ежедн"]:
        config["eday_enabled"] = not config.get("eday_enabled", False)
        save_config(session_name, config)
        status = "✅ включён" if config["eday_enabled"] else "❌ выключен"
        await message.edit_text(f"🎁 Автосбор ежедневной награды: {status}.")

    elif command in [".debug", ".дебаг"]:
        req_start = time.time()
        await message.edit_text("⏳ Собираем данные…")

        ping_ms  = round((time.time() - req_start) * 1000)
        uptime   = int(time.time() - START_TIME)
        u_h = uptime // 3600
        u_m = (uptime % 3600) // 60
        u_s = uptime % 60

        target = config.get("target_user")  or "❌ не задана"
        amount = config.get("target_amount") or "❌ не задана"

        if config.get("tcard_enabled") and config.get("tcard_interval"):
            tcard_st = f"✅ каждые {config['tcard_interval']} мин."
        else:
            tcard_st = "❌ выкл"

        eday_st = "✅ вкл" if config.get("eday_enabled") else "❌ выкл"
        bot_st  = "✅ включён"  if config.get("enabled")   else "❌ выключен"

        if config.get("buy_enabled") and config.get("buy_rarity"):
            rkey  = config["buy_rarity"]
            rlabel = next((l for l, k in RARITIES if k == rkey), rkey)
            buy_st = f"✅ {rlabel} × {config.get('buy_count', 1)}"
        else:
            buy_st = "❌ выкл"

        sess_list = "\n".join([f"• `{n}`" for n in active_clients.keys()]) or "• Нет активных сессий"

        await message.edit_text(
            f"🛠 **PGUB Debug Info**\n"
            f"───────────────────\n"
            f"⏱️ **Аптайм:** {u_h}ч {u_m}м {u_s}с\n"
            f"📡 **Ping:** {ping_ms} мс\n"
            f"🐍 **Python:** {sys.version.split()[0]}\n"
            f"───────────────────\n"
            f"⚙️ **Настройки ({session_name}):**\n"
            f"  🤖 Статус: {bot_st}\n"
            f"  🎯 Цель перевода: {target}\n"
            f"  💰 Сумма перевода: {amount}\n"
            f"  🃏 ТКарточка: {tcard_st}\n"
            f"  🎁 Ежедн. награда: {eday_st}\n"
            f"  🛍 Авто-покупка: {buy_st}\n"
            f"───────────────────\n"
            f"**Активные сессии:**\n{sess_list}"
        )

    elif command in [".help", ".помощь", ".справка", ".хелп"]:
        await message.edit_text(
            "**👾 Справка по командам Юзербота**\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "🔠 **Латиница:**\n"
            "`•` `.on` — включить бота\n"
            "`•` `.off` — выключить бота\n"
            "`•` `.tcard` — авто-ТКарточка: выбор интервала или отключение\n"
            "`•` `.buy` — авто-покупка телефонов при каждом сборе фермы\n"
            "`•` `.eday` — авто-сбор ежедневной награды параллельно с фермой\n"
            "`•` `.target <@user> <amount>` — цель и сумма перевода\n"
            "`•` `.debug` — текущие настройки и пинг\n"
            "`•` `.session` — список активных сессий\n"
            "`•` `.delsession <имя>` — удалить сессию навсегда\n"
            "`•` `.help` — эта справка\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "🔤 **Кириллица:**\n"
            "`•` `.вкл` — включить бота\n"
            "`•` `.выкл` — выключить бота\n"
            "`•` `.ткарточка` — авто-ТКарточка: выбор интервала или отключение\n"
            "`•` `.купить` — авто-покупка телефонов при каждом сборе фермы\n"
            "`•` `.ежедн` — авто-сбор ежедневной награды параллельно с фермой\n"
            "`•` `.цель <@user> <сумма>` — цель и сумма перевода\n"
            "`•` `.дебаг` — текущие настройки и пинг\n"
            "`•` `.сессии` — список активных сессий\n"
            "`•` `.удалитьсессию <имя>` — удалить сессию навсегда\n"
            "`•` `.хелп` — эта справка"
        )

    elif command in [".sessions", ".сессии", ".session"]:
        sess_list = "\n".join([f"• `{n}`" for n in active_clients.keys()]) or "• Нет активных сессий"
        await message.edit_text(f"📁 **Активные сессии:**\n{sess_list}")

    elif command in [".delsession", ".удалитьсессию"]:
        if len(parts) < 2:
            await message.edit_text(
                "❌ Укажи имя сессии.\n"
                "Пример: `.удалитьсессию user_79991234567`"
            )
            return

        target_sess = parts[1]

        if target_sess == session_name:
            await message.edit_text("❌ Нельзя удалить собственную активную сессию с этого же аккаунта.")
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
            await message.edit_text(f"✅ Сессия `{target_sess}` остановлена и удалена с сервера.")
        else:
            await message.edit_text(f"⚠️ Сессия `{target_sess}` не найдена.")

# ──────────────────────────────────────────────
# Воркеры
# ──────────────────────────────────────────────

async def tcard_worker(client: Client, session_name: str):
    last_sent = 0.0
    while True:
        await asyncio.sleep(30)
        try:
            config       = load_config(session_name)
            interval_min = config.get("tcard_interval", 0)
            if config.get("enabled") and config.get("tcard_enabled") and interval_min > 0:
                if time.time() - last_sent >= interval_min * 60:
                    await client.send_message(GAME_BOT, "ткарточка")
                    last_sent = time.time()
                    print(f"[{session_name}] 🃏 ТКарточка отправлена (интервал: {interval_min} мин.)")
        except Exception as e:
            print(f"[{session_name}] Важная ошибка отправки ткарточки: {e}")

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
                    next_run = msk.localize(datetime(
                        tomorrow.year, tomorrow.month, tomorrow.day,
                        random.randint(1, 4), random.randint(0, 59), random.randint(0, 59)
                    ))
                else:
                    if now.hour < 5:
                        if now.hour >= 1:
                            next_run = now + timedelta(seconds=15)
                        else:
                            next_run = msk.localize(datetime(
                                now.year, now.month, now.day,
                                random.randint(1, 4), random.randint(0, 59), random.randint(0, 59)
                            ))
                    else:
                        tomorrow = now + timedelta(days=1)
                        next_run = msk.localize(datetime(
                            tomorrow.year, tomorrow.month, tomorrow.day,
                            random.randint(1, 4), random.randint(0, 59), random.randint(0, 59)
                        ))

                sleep_sec = (next_run - now).total_seconds()
                if sleep_sec > 0:
                    await asyncio.sleep(sleep_sec)

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
            print(f"[{session_name}] Важная ошибка воркера майнинга: {e}")
        await asyncio.sleep(60)

# ──────────────────────────────────────────────
# Запуск юзербота (сессия из файла)
# ──────────────────────────────────────────────

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

        @client.on_callback_query()
        async def cb_handler(c, cq):
            await handle_userbot_callback(c, cq)

        await client.start()
        try:
            async for _ in client.get_dialogs(limit=20):
                pass
        except Exception:
            pass

        active_clients[session_name] = client
        asyncio.create_task(tcard_worker(client, session_name))
        asyncio.create_task(daily_and_mining_worker(client, session_name))
        print(f"Юзербот {session_name} успешно запущен!")
    except Exception as e:
        print(f"Критическая ошибка при старте юзербота {session_name}: {e}")

async def init_existing_sessions():
    await asyncio.sleep(2)
    files = [f for f in os.listdir(SESSIONS_DIR) if f.endswith(".session")]
    for f in files:
        s_name = f.replace(".session", "")
        if s_name in ["auth_manager_bot", "master_bot"]:
            continue
        print(f"Найдена существующая сессия: {s_name}. Запуск…")
        asyncio.create_task(launch_userbot_instance(s_name))

# ──────────────────────────────────────────────
# Сервисный бот (авторизация)
# ──────────────────────────────────────────────

def get_pin_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("1", callback_data="pin_1"),
         InlineKeyboardButton("2", callback_data="pin_2"),
         InlineKeyboardButton("3", callback_data="pin_3")],
        [InlineKeyboardButton("4", callback_data="pin_4"),
         InlineKeyboardButton("5", callback_data="pin_5"),
         InlineKeyboardButton("6", callback_data="pin_6")],
        [InlineKeyboardButton("7", callback_data="pin_7"),
         InlineKeyboardButton("8", callback_data="pin_8"),
         InlineKeyboardButton("9", callback_data="pin_9")],
        [InlineKeyboardButton("⬅️", callback_data="pin_del"),
         InlineKeyboardButton("0",  callback_data="pin_0"),
         InlineKeyboardButton("🗑", callback_data="pin_clear")],
        [InlineKeyboardButton("❌ Отмена", callback_data="pin_cancel")],
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
        await m.reply_text(
            "Привет! Отправь мне номер телефона аккаунта для авторизации юзербота "
            "в международном формате (например, +79991234567)."
        )

    @bot.on_message(filters.text & filters.private)
    async def process_auth(c, m):
        chat_id = m.chat.id
        text    = m.text.strip()
        state   = user_states.get(chat_id, {"step": "IDLE"})
        step    = state.get("step")

        if step == "IDLE":
            if text.startswith("+") and len(text) > 9:
                phone        = text.replace(" ", "")
                session_name = f"user_{phone.replace('+', '')}"

                await m.reply_text("Связываюсь с серверами Telegram, ожидайте… ⏳")

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
                        "step":            "WAIT_CODE",
                        "phone":           phone,
                        "session_name":    session_name,
                        "client":          client,
                        "phone_code_hash": code_info.phone_code_hash,
                        "entered_code":    ""
                    }
                    await m.reply_text(
                        f"📲 Telegram отправил код на номер {phone}.\n\n"
                        f"**Код:** {format_code_display('')}\n\n"
                        f"Используй клавиатуру ниже для ввода:",
                        reply_markup=get_pin_keyboard()
                    )
                except FloodWait as e:
                    await m.reply_text(f"⚠️ Превышен лимит запросов. Подожди {e.value} секунд.")
                    await client.disconnect()
                except Exception as e:
                    await m.reply_text(f"❌ Ошибка при отправке кода: {e}")
                    await client.disconnect()
            else:
                await m.reply_text("Неверный формат. Отправь номер телефона, начиная с `+`")

        elif step == "WAIT_CODE":
            msg = await m.reply_text("⚠️ Используй кнопки выше для безопасного ввода кода.")
            await asyncio.sleep(3)
            await msg.delete()

        elif step == "WAIT_PASSWORD":
            client       = state["client"]
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
        state   = user_states.get(chat_id)

        if not state or state.get("step") != "WAIT_CODE":
            await cq.answer("Код больше не ожидается. Начни заново через /start", show_alert=True)
            return

        action       = cq.data.split("_")[1]
        current_code = state.get("entered_code", "")
        client       = state["client"]

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
            await cq.message.edit_text(f"🔐 Проверка кода: {format_code_display(current_code)} …")
            try:
                phone            = state["phone"]
                phone_code_hash  = state["phone_code_hash"]
                session_name     = state["session_name"]

                await client.sign_in(phone, phone_code_hash, current_code)
                await cq.message.edit_text("✅ Авторизация успешна! Юзербот запущен в работу.")
                await client.disconnect()
                user_states[chat_id] = {"step": "IDLE"}
                asyncio.create_task(launch_userbot_instance(session_name))

            except SessionPasswordNeeded:
                user_states[chat_id]["step"] = "WAIT_PASSWORD"
                await cq.message.edit_text(
                    "🔒 На аккаунте включён облачный пароль (2FA).\n\n"
                    "Отправь свой пароль сообщением в этот чат:"
                )
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

# ──────────────────────────────────────────────
# Точка входа
# ──────────────────────────────────────────────

async def main():
    if not API_ID or not API_HASH:
        print("КРИТИЧЕСКАЯ ОШИБКА: Не указаны API_ID и/или API_HASH!")
        return

    loop = asyncio.get_event_loop()
    loop.set_exception_handler(custom_exception_handler)

    # Запуск сессий из переменных окружения
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

            @client.on_callback_query()
            async def cb_handler(c, cq):
                await handle_userbot_callback(c, cq)

            await client.start()
            try:
                async for _ in client.get_dialogs(limit=20):
                    pass
            except Exception:
                pass

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
        print("Внимание: BOT_TOKEN не задан. Авторизация через чат отключена.")

    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())