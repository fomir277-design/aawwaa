# Telegram Userbot Automation Project

Полный комплект файлов для развертывания на Railway с поддержкой переменных окружения и автоматическим игнорированием конфиденциальных данных.

## Настройка в Railway Variables:
1. `API_ID` — Твой API ID с сайта my.telegram.org
2. `API_HASH` — Твой API HASH с сайта my.telegram.org

## Папки в Railway Volume (должен быть смонтирован в `/data`):
* `/data/sessions/` — сюда загружать файлы сессий аккаунтов (`.session`).
* `/data/configs/` — здесь бот будет автоматически хранить изолированные настройки пользователей.
