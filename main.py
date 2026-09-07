from telethon import TelegramClient, events
from telethon.errors import SessionPasswordNeededError, RPCError
from telethon.tl.custom import Button
import asyncio
import random
import csv
import io
import os

# Переменные окружения
API_ID = int(os.getenv('API_ID', 'YOUR_API_ID'))
API_HASH = os.getenv('API_HASH', 'YOUR_API_HASH')
BOT_TOKEN = os.getenv('BOT_TOKEN', 'YOUR_BOT_TOKEN')

# Бот-клиент (для команд)
bot_client = TelegramClient('bot_session', API_ID, API_HASH).start(bot_token=BOT_TOKEN)

# Пользовательский клиент (для отправки сообщений) – создаём, но не авторизуем сразу
user_client = TelegramClient('user_session', API_ID, API_HASH)

# Хранилище данных по каждому чату (администратору)
user_data = {}
# Активные задачи рассылки
spam_tasks = {}

# Флаг авторизации пользовательского клиента
user_authorized = False

# ---------- ХЕЛПЕРЫ ДЛЯ РАБОТЫ С ПОЛЬЗОВАТЕЛЬСКИМ КЛИЕНТОМ ----------
async def ensure_user_authorized(phone=None, code=None, password=None):
    """Проверяет, авторизован ли user_client; если нет – пробует войти с переданными данными."""
    global user_authorized
    if user_authorized:
        return True
    try:
        # Если ещё не авторизованы, пробуем войти
        if phone and code:
            await user_client.sign_in(phone, code)
            user_authorized = True
            return True
        elif password:
            await user_client.sign_in(password=password)
            user_authorized = True
            return True
        else:
            # Возможно, уже есть сохранённая сессия – попробуем просто подключиться
            await user_client.connect()
            if await user_client.is_user_authorized():
                user_authorized = True
                return True
            return False
    except Exception as e:
        # Если сессия невалидна, сбрасываем флаг
        user_authorized = False
        raise e

# ---------- ОБРАБОТЧИКИ БОТА ----------
@bot_client.on(events.NewMessage(pattern='/start'))
async def start(event):
    chat_id = event.chat_id
    user_data.setdefault(chat_id, {})
    await event.reply("👋 Привет! Для отправки сообщений сотрудникам нужно авторизовать мой пользовательский аккаунт.\n"
                      "Введите ваш номер телефона (в международном формате, например +79991234567):")
    user_data[chat_id]['step'] = 'phone'

@bot_client.on(events.NewMessage(func=lambda e: e.chat_id in user_data and user_data[e.chat_id].get('step') == 'phone'))
async def phone_input(event):
    chat_id = event.chat_id
    phone = event.message.text.strip()
    try:
        # Отправляем запрос кода через пользовательского клиента
        await user_client.connect()
        await user_client.send_code_request(phone)
        user_data[chat_id].update({'phone': phone, 'step': 'code'})
        await event.reply("📱 Код подтверждения отправлен. Введите код (только цифры):")
    except Exception as e:
        await event.reply(f"❌ Ошибка: {str(e)}. Попробуйте ещё раз ввести номер.")
        # Если ошибка, сбрасываем шаг
        user_data[chat_id].pop('step', None)

@bot_client.on(events.NewMessage(func=lambda e: e.chat_id in user_data and user_data[e.chat_id].get('step') == 'code'))
async def code_input(event):
    chat_id = event.chat_id
    code = event.message.text.strip()
    phone = user_data[chat_id].get('phone')
    try:
        await user_client.sign_in(phone, code)
        # Успешно
        user_data[chat_id].pop('step', None)
        user_authorized = True
        buttons = [
            [Button.inline("📂 Загрузить CSV", b'add_users')],
            [Button.inline("✏️ Настроить шаблоны", b'templates')],
            [Button.inline("🚀 Запустить рассылку", b'start_spam')],
            [Button.inline("⏹ Остановить рассылку", b'stop_spam')]
        ]
        await event.reply("✅ Пользовательский аккаунт успешно авторизован! Теперь выберите действие:", buttons=buttons)
    except SessionPasswordNeededError:
        user_data[chat_id]['step'] = 'password'
        await event.reply("🔐 Включена двухфакторная аутентификация. Введите пароль:")
    except Exception as e:
        await event.reply(f"❌ Ошибка: {str(e)}. Повторите ввод кода.")

@bot_client.on(events.NewMessage(func=lambda e: e.chat_id in user_data and user_data[e.chat_id].get('step') == 'password'))
async def password_input(event):
    chat_id = event.chat_id
    password = event.message.text.strip()
    try:
        await user_client.sign_in(password=password)
        user_data[chat_id].pop('step', None)
        user_authorized = True
        buttons = [
            [Button.inline("📂 Загрузить CSV", b'add_users')],
            [Button.inline("✏️ Настроить шаблоны", b'templates')],
            [Button.inline("🚀 Запустить рассылку", b'start_spam')],
            [Button.inline("⏹ Остановить рассылку", b'stop_spam')]
        ]
        await event.reply("✅ Пользовательский аккаунт успешно авторизован! Выберите действие:", buttons=buttons)
    except Exception as e:
        await event.reply(f"❌ Ошибка: {str(e)}. Повторите ввод пароля.")

# ---------- КНОПКИ ----------
@bot_client.on(events.CallbackQuery(data=b'add_users'))
async def add_users(event):
    await event.answer()
    await event.edit("📎 Отправьте мне **CSV-файл** (с разделителем запятая).\n"
                     "Ожидаются колонки: `username`, `user_id` (или одна из них).\n"
                     "Если `username` пуст, буду использовать `user_id`.")

@bot_client.on(events.CallbackQuery(data=b'templates'))
async def setup_templates(event):
    await event.answer()
    await event.edit(
        "✏️ Чтобы добавить шаблон, отправьте команду:\n"
        "`/template Название Текст сообщения`\n\n"
        "Пример:\n"
        "`/template Приветствие Привет, коллега!`\n\n"
        "Все добавленные шаблоны будут показаны после добавления."
    )

@bot_client.on(events.CallbackQuery(data=b'start_spam'))
async def start_spam_callback(event):
    await event.answer()
    # Проверяем, авторизован ли user_client
    if not user_authorized:
        await event.reply("❌ Сначала авторизуйте пользовательский аккаунт через /start.")
        return
    await start_spam(event)

@bot_client.on(events.CallbackQuery(data=b'stop_spam'))
async def stop_spam_callback(event):
    await event.answer()
    await stop_spam(event)

# ---------- ДОБАВЛЕНИЕ ШАБЛОНОВ ----------
@bot_client.on(events.NewMessage(pattern='/template'))
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
    list_templates = "\n".join([f"• {n}: {t}" for n, t in templates.items()]) if templates else "пока нет"
    await event.reply(f"✅ Шаблон '{name}' добавлен!\n\n📋 Текущие шаблоны:\n{list_templates}")

# ---------- ОБРАБОТКА CSV-ФАЙЛА ----------
@bot_client.on(events.NewMessage(func=lambda e: e.chat_id in user_data and e.document))
async def process_csv(event):
    chat_id = event.chat_id
    if user_data[chat_id].get('step'):
        return  # игнорируем, если идёт авторизация

    file_name = event.message.file.name if event.message.file else ''
    if not file_name.lower().endswith('.csv'):
        await event.reply("❌ Пожалуйста, отправьте файл с расширением `.csv`.")
        return

    try:
        file_content = await bot_client.download_media(event.message, file=bytes)
        csv_reader = csv.DictReader(io.StringIO(file_content.decode('utf-8')))
        recipients = []
        missing_username_count = 0
        for row in csv_reader:
            username = row.get('username', '').strip()
            user_id = row.get('user_id', '').strip()
            if username:
                recipients.append(username)
            elif user_id:
                recipients.append(user_id)
                missing_username_count += 1
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
@bot_client.on(events.NewMessage(pattern='/start_spam'))
async def start_spam(event):
    chat_id = event.chat_id
    if not user_authorized:
        await event.reply("❌ Пользовательский аккаунт не авторизован. Сначала выполните /start и введите номер/код.")
        return
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
                # Отправляем через пользовательский клиент
                if recipient.isdigit():
                    await user_client.send_message(int(recipient), template)
                else:
                    await user_client.send_message(recipient, template)
                delay = random.uniform(10, 250)
                await asyncio.sleep(delay)
            except RPCError as e:
                print(f"Ошибка отправки для {recipient}: {e}")
                # Можно также уведомить администратора, но не прерываем
        if chat_id in spam_tasks:
            del spam_tasks[chat_id]
        await bot_client.send_message(chat_id, "✅ Рассылка завершена.")
    except asyncio.CancelledError:
        if chat_id in spam_tasks:
            del spam_tasks[chat_id]
        await bot_client.send_message(chat_id, "⏹ Рассылка остановлена.")

# ---------- ОСТАНОВКА ----------
@bot_client.on(events.NewMessage(pattern='/stop_spam'))
async def stop_spam(event):
    chat_id = event.chat_id
    if chat_id in spam_tasks and not spam_tasks[chat_id].done():
        spam_tasks[chat_id].cancel()
        await event.reply("⏹ Рассылка остановлена.")
    else:
        await event.reply("⚠️ Активная рассылка не найдена.")

# ---------- ЗАПУСК ----------
async def main():
    # Подключаем бота (уже запущен)
    await bot_client.start()
    # Подключаем пользовательского клиента (попытка использовать сохранённую сессию)
    await user_client.connect()
    global user_authorized
    if await user_client.is_user_authorized():
        user_authorized = True
        print("Пользовательский клиент уже авторизован (сессия восстановлена).")
    else:
        print("Пользовательский клиент не авторизован – ожидаем ввода номера через /start.")

    # Запускаем бота до отключения
    await bot_client.run_until_disconnected()

if __name__ == '__main__':
    asyncio.run(main())

