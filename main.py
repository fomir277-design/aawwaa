import os
import json
import asyncio
import random
from datetime import datetime, timedelta
import pytz
from pyrogram import Client, filters
from pyrogram.types import Message

DATA_DIR = "/data"
SESSIONS_DIR = os.path.join(DATA_DIR, "sessions")
CONFIGS_DIR = os.path.join(DATA_DIR, "configs")

os.makedirs(SESSIONS_DIR, exist_ok=True)
os.makedirs(CONFIGS_DIR, exist_ok=True)

GAME_BOT = "phonegetcardsbot"

# Get global API credentials from Railway Environment Variables
API_ID = os.environ.get("API_ID")
API_HASH = os.environ.get("API_HASH")

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
                        asyncio.create_task(delayed_payment(client, session_name))
                    except Exception as e:
                        print(f"[{session_name}] Ошибка клика farm_claim: {e}")
                
                elif config.get("eday_enabled") and ("confirm_daily_claim" in str(button.callback_data) or "Забрать" in str(button.text)):
                    try:
                        await message.click(button.callback_data)
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
            print(f"Ошибка tcard_worker ({session_name}): {e}")
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
            print(f"Ошибка daily_and_mining_worker ({session_name}): {e}")
        await asyncio.sleep(60)

async def main():
    session_files = [f for f in os.listdir(SESSIONS_DIR) if f.endswith(".session")]
    if not session_files:
        print(f"Файлы сессий не найдены в {SESSIONS_DIR}. Ожидание загрузки файлов...")
        while True:
            await asyncio.sleep(3600)
            
    drivers = []
    for s_file in session_files:
        s_name = s_file.replace(".session", "")
        
        client_kwargs = {
            "name": s_name,
            "workdir": SESSIONS_DIR,
            "plugins": None
        }
        if API_ID:
            client_kwargs["api_id"] = int(API_ID) if API_ID.isdigit() else API_ID
        if API_HASH:
            client_kwargs["api_hash"] = API_HASH
            
        client = Client(**client_kwargs)
        
        @client.on_message(filters.me)
        async def u_handler(c, m):
            await handle_user_commands(c, m)
            
        @client.on_message(filters.chat(GAME_BOT))
        async def b_handler(c, m):
            await handle_bot_message(c, m)
            
        drivers.append((client, s_name))
        
    for client, s_name in drivers:
        print(f"Запуск сессии: {s_name}")
        await client.start()
        asyncio.create_task(tcard_worker(client, s_name))
        asyncio.create_task(daily_and_mining_worker(client, s_name))
        
    print("Все активные юзерботы запущены.")
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
