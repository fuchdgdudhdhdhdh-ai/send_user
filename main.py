from telethon import TelegramClient, events
from telethon.errors import SessionPasswordNeededError
from telethon.tl.custom import Button
import asyncio
import random
import csv
import os
import io

# Переменные из окружения Render
API_ID = int(os.getenv('API_ID', 'YOUR_API_ID'))
API_HASH = os.getenv('API_HASH', 'YOUR_API_HASH')
BOT_TOKEN = os.getenv('BOT_TOKEN', 'YOUR_BOT_TOKEN')

client = TelegramClient('session_name', API_ID, API_HASH).start(bot_token=BOT_TOKEN)

# Хранилище данных пользователей (ключ – chat_id)
user_data = {}
# Активные задачи рассылки (чтобы можно было остановить)
spam_tasks = {}

@client.on(events.NewMessage(pattern='/start'))
async def start(event):
    chat_id = event.chat_id
    user_data.setdefault(chat_id, {})
    await event.reply("Введите ваш номер телефона (в международном формате, например +79991234567):")
    user_data[chat_id]['step'] = 'phone'

@client.on(events.NewMessage(func=lambda e: e.chat_id in user_data and user_data[e.chat_id].get('step') == 'phone'))
async def phone_input(event):
    chat_id = event.chat_id
    phone = event.message.text.strip()
    try:
        await client.send_code_request(phone)
        user_data[chat_id].update({'phone': phone, 'step': 'code'})
        await event.reply("Код подтверждения отправлен. Введите код (только цифры):")
    except Exception as e:
        await event.reply(f"Ошибка: {str(e)}. Попробуйте ещё раз ввести номер.")

@client.on(events.NewMessage(func=lambda e: e.chat_id in user_data and user_data[e.chat_id].get('step') == 'code'))
async def code_input(event):
    chat_id = event.chat_id
    code = event.message.text.strip()
    phone = user_data[chat_id].get('phone')
    try:
        await client.sign_in(phone, code)
        user_data[chat_id].pop('step', None)
        buttons = [
            [Button.inline("📂 Загрузить CSV", b'add_users')],
            [Button.inline("✏️ Настроить шаблоны", b'templates')],
            [Button.inline("🚀 Запустить рассылку", b'start_spam')],
            [Button.inline("⏹ Остановить рассылку", b'stop_spam')]
        ]
        await event.reply("✅ Успешная авторизация! Выберите действие:", buttons=buttons)
    except SessionPasswordNeededError:
        user_data[chat_id]['step'] = 'password'
        await event.reply("🔐 Включена двухфакторная аутентификация. Введите пароль:")
    except Exception as e:
        await event.reply(f"Ошибка: {str(e)}. Повторите ввод кода.")

@client.on(events.NewMessage(func=lambda e: e.chat_id in user_data and user_data[e.ch_id].get('step') == 'password'))
async def password_input(event):
    chat_id = event.chat_id
    password = event.message.text.strip()
    try:
        await client.sign_in(password=password)
        user_data[chat_id].pop('step', None)
        buttons = [
            [Button.inline("📂 Загрузить CSV", b'add_users')],
            [Button.inline("✏️ Настроить шаблоны", b'templates')],
            [Button.inline("🚀 Запустить рассылку", b'start_spam')],
            [Button.inline("⏹ Остановить рассылку", b'stop_spam')]
        ]
        await event.reply("✅ Успешная авторизация! Выберите действие:", buttons=buttons)
    except Exception as e:
        await event.reply(f"Ошибка: {str(e)}. Повторите ввод пароля.")

# ---------- КНОПКИ ----------
@client.on(events.CallbackQuery(data=b'add_users'))
async def add_users(event):
    await event.answer()
    await event.edit("📎 Отправьте мне **CSV-файл** (с разделителем запятая).\n"
                     "Ожидаются колонки: `username`, `user_id` (или одна из них).\n"
                     "Если `username` пуст, буду использовать `user_id`.")

@client.on(events.CallbackQuery(data=b'templates'))
async def setup_templates(event):
    await event.answer()
    await event.edit(
        "✏️ Чтобы добавить шаблон, отправьте команду:\n"
        "`/template Название Текст сообщения`\n\n"
        "Пример:\n"
        "`/template Приветствие Привет, коллега!`\n\n"
        "Все добавленные шаблоны будут показаны после добавления."
    )

@client.on(events.CallbackQuery(data=b'start_spam'))
async def start_spam_callback(event):
    await event.answer()
    await start_spam(event)

@client.on(events.CallbackQuery(data=b'stop_spam'))
async def stop_spam_callback(event):
    await event.answer()
    await stop_spam(event)

# ---------- ДОБАВЛЕНИЕ ШАБЛОНОВ ----------
@client.on(events.NewMessage(pattern='/template'))
async def add_template(event):
    chat_id = event.chat_id
    args = event.message.text.split(maxsplit=2)
    if len(args) < 3:
        await event.reply("❌ Неверный формат. Используйте:\n`/template Название Текст`")
        return
    name, text = args[1], args[2]
    user_data.setdefault(chat_id, {})
    user_data[chat_id].setdefault('templates', {})[name] = text
    templates = user_data[chat_id]['templates']
    if templates:
        list_templates = "\n".join([f"• {n}: {t}" for n, t in templates.items()])
        await event.reply(f"✅ Шаблон '{name}' добавлен!\n\n📋 Текущие шаблоны:\n{list_templates}")
    else:
        await event.reply(f"✅ Шаблон '{name}' добавлен!")

# ---------- ОБРАБОТКА CSV-ФАЙЛА ----------
@client.on(events.NewMessage(func=lambda e: e.chat_id in user_data and e.document))
async def process_csv(event):
    chat_id = event.chat_id
    # Игнорируем, если идёт авторизация
    if user_data[chat_id].get('step'):
        return

    # Проверяем, что файл имеет расширение .csv (или просто пытаемся прочитать как CSV)
    file_name = event.message.file.name if event.message.file else ''
    if not file_name.lower().endswith('.csv'):
        await event.reply("❌ Пожалуйста, отправьте файл с расширением `.csv`.")
        return

    try:
        # Скачиваем файл в память
        file_content = await client.download_media(event.message, file=bytes)
        # Парсим CSV
        csv_reader = csv.DictReader(io.StringIO(file_content.decode('utf-8')))
        recipients = []
        missing_username_count = 0
        for row in csv_reader:
            # Ищем username, если есть и не пустой
            username = row.get('username', '').strip()
            user_id = row.get('user_id', '').strip()
            if username:
                recipients.append(username)
            elif user_id:
                recipients.append(user_id)  # числовой ID как строка
                missing_username_count += 1
            # иначе пропускаем строку

        if not recipients:
            await event.reply("❌ Не найдено ни одного адресата (нет username и user_id).")
            return

        user_data.setdefault(chat_id, {})['users'] = recipients
        await event.reply(
            f"✅ Загружено {len(recipients)} адресатов.\n"
            f"Из них {missing_username_count} – по user_id (без username).\n"
            f"Теперь добавьте шаблоны через /template."
        )
    except Exception as e:
        await event.reply(f"❌ Ошибка при обработке CSV: {str(e)}")

# ---------- ЗАПУСК РАССЫЛКИ ----------
@client.on(events.NewMessage(pattern='/start_spam'))
async def start_spam(event):
    chat_id = event.chat_id
    data = user_data.get(chat_id, {})
    users = data.get('users')
    templates = data.get('templates')
    if not users:
        await event.reply("❌ Сначала загрузите CSV-файл через кнопку.")
        return
    if not templates:
        await event.reply("❌ Добавьте хотя бы один шаблон через /template.")
        return
    if chat_id in spam_tasks and not spam_tasks[chat_id].done():
        await event.reply("⚠️ Рассылка уже запущена. Используйте /stop_spam для остановки.")
        return

    task = asyncio.create_task(spam_loop(chat_id, users, templates))
    spam_tasks[chat_id] = task
    await event.reply(f"🚀 Рассылка запущена! Будет отправлено {len(users)} сообщений (случайный шаблон из {len(templates)}).")

async def spam_loop(chat_id, users, templates):
    template_list = list(templates.values())
    try:
        for recipient in users:
            if spam_tasks.get(chat_id) and spam_tasks[chat_id].cancelled():
                break
            template = random.choice(template_list)
            try:
                # Если recipient – строка, содержащая только цифры, можно отправить как int
                # Но Telethon принимает и строки с юзернеймом, и целые числа.
                # Для числовых ID преобразуем в int, чтобы избежать отправки как username.
                if recipient.isdigit():
                    await client.send_message(int(recipient), template)
                else:
                    await client.send_message(recipient, template)
                delay = random.uniform(10, 250)
                await asyncio.sleep(delay)
            except Exception as e:
                print(f"Ошибка отправки для {recipient}: {e}")
        if chat_id in spam_tasks:
            del spam_tasks[chat_id]
        await client.send_message(chat_id, "✅ Рассылка завершена.")
    except asyncio.CancelledError:
        if chat_id in spam_tasks:
            del spam_tasks[chat_id]
        await client.send_message(chat_id, "⏹ Рассылка остановлена.")

# ---------- ОСТАНОВКА ----------
@client.on(events.NewMessage(pattern='/stop_spam'))
async def stop_spam(event):
    chat_id = event.chat_id
    if chat_id in spam_tasks and not spam_tasks[chat_id].done():
        spam_tasks[chat_id].cancel()
        await event.reply("⏹ Рассылка остановлена.")
    else:
        await event.reply("⚠️ Активная рассылка не найдена.")

# ---------- ЗАПУСК ----------
async def main():
    await client.run_until_disconnected()

if __name__ == '__main__':
    asyncio.run(main())
