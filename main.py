import asyncio
import csv
import io
import os
import random
import sys
import logging
from aiohttp import web
from telethon import TelegramClient, events
from telethon.errors import SessionPasswordNeededError, RPCError, PhoneCodeExpiredError, PhoneCodeInvalidError
from telethon.tl.custom import Button

# ---------- НАСТРОЙКА ЛОГИРОВАНИЯ ----------
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ---------- ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ ----------
API_ID_RAW = os.getenv('API_ID')
API_HASH = os.getenv('API_HASH')
BOT_TOKEN = os.getenv('BOT_TOKEN')
PORT = int(os.getenv('PORT', 8080))

if not API_ID_RAW or not API_HASH or not BOT_TOKEN:
    logger.error("Не все переменные окружения заданы!")
    sys.exit(1)

try:
    API_ID = int(API_ID_RAW)
except ValueError:
    logger.error(f"API_ID должен быть числом, получено: {API_ID_RAW}")
    sys.exit(1)

logger.info(f"Загружены настройки: API_ID={API_ID}")

# ---------- КЛИЕНТЫ ----------
bot_client = TelegramClient('bot_session', API_ID, API_HASH)
user_client = TelegramClient('user_session', API_ID, API_HASH)

# Глобальные хранилища
user_data = {}
spam_tasks = {}
user_authorized = False

# ---------- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ----------
async def ensure_user_authorized():
    global user_authorized
    if user_authorized:
        return True
    try:
        if not user_client.is_connected():
            await user_client.connect()
        if await user_client.is_user_authorized():
            user_authorized = True
            return True
        return False
    except Exception as e:
        logger.error(f"Ошибка при проверке авторизации: {e}", exc_info=True)
        return False

def logout_user(chat_id):
    """Выход из аккаунта: удаление сессии и сброс данных"""
    global user_authorized
    try:
        # Отключаем клиент
        if user_client.is_connected():
            asyncio.create_task(user_client.disconnect())
        
        # Удаляем файл сессии
        session_file = 'user_session.session'
        if os.path.exists(session_file):
            os.remove(session_file)
            logger.info(f"Файл сессии {session_file} удалён")
        
        # Сбрасываем флаг авторизации
        user_authorized = False
        
        # Очищаем данные пользователя
        if chat_id in user_data:
            user_data[chat_id] = {}
        
        logger.info(f"Пользователь {chat_id} вышел из аккаунта")
        return True
    except Exception as e:
        logger.error(f"Ошибка при выходе: {e}", exc_info=True)
        return False

# ---------- СОЗДАНИЕ КЛАВИАТУРЫ ДЛЯ КОДА ----------
def get_code_keyboard():
    """Создаёт цифровую клавиатуру для ввода кода"""
    buttons = [
        [Button.inline("1", b'code_1'), Button.inline("2", b'code_2'), Button.inline("3", b'code_3')],
        [Button.inline("4", b'code_4'), Button.inline("5", b'code_5'), Button.inline("6", b'code_6')],
        [Button.inline("7", b'code_7'), Button.inline("8", b'code_8'), Button.inline("9", b'code_9')],
        [Button.inline("0", b'code_0'), Button.inline("⌫", b'code_backspace'), Button.inline("✓", b'code_confirm')]
    ]
    return buttons

def get_main_menu():
    """Создаёт главное меню с кнопками"""
    buttons = [
        [Button.inline("📂 Загрузить CSV", b'add_users')],
        [Button.inline("✏️ Настроить шаблоны", b'templates')],
        [Button.inline("🚀 Запустить рассылку", b'start_spam')],
        [Button.inline("⏹ Остановить рассылку", b'stop_spam')],
        [Button.inline("🚪 Выйти из аккаунта", b'logout')]
    ]
    return buttons

# ---------- ОБРАБОТЧИКИ БОТА ----------
@bot_client.on(events.NewMessage(pattern='/start'))
async def start(event):
    chat_id = event.chat_id
    user_data.setdefault(chat_id, {})
    
    # Если уже есть активная сессия – сразу показываем меню
    if await ensure_user_authorized():
        await event.reply("✅ Вы уже авторизованы! Выберите действие:", buttons=get_main_menu())
        return

    await event.reply(
        "👋 Привет! Для отправки сообщений сотрудникам нужно авторизовать пользовательский аккаунт.\n\n"
        "⚠️ **Важно:** Используйте номер телефона, который НЕ является владельцем этого бота.\n"
        "Например, корпоративный аккаунт или другой ваш аккаунт.\n\n"
        "Введите номер телефона (в международном формате, например +79991234567):"
    )
    user_data[chat_id]['step'] = 'phone'

@bot_client.on(events.NewMessage(func=lambda e: e.chat_id in user_data and user_data[e.chat_id].get('step') == 'phone'))
async def phone_input(event):
    if event.message.text.startswith('/'):
        return
    chat_id = event.chat_id
    phone = event.message.text.strip()
    logger.info(f"Получен номер телефона от {chat_id}: '{phone}'")
    if not phone:
        await event.reply("❌ Номер не может быть пустым. Введите номер ещё раз:")
        return
    try:
        if not user_client.is_connected():
            await user_client.connect()
        
        # Отправляем запрос кода
        sent_code = await user_client.send_code_request(phone)
        user_data[chat_id].update({
            'phone': phone,
            'phone_code_hash': sent_code.phone_code_hash,
            'step': 'code',
            'code_input': ''  # для накопления вводимого кода
        })
        
        # Отправляем сообщение с клавиатурой для ввода кода
        await event.reply(
            "📱 Код подтверждения отправлен.\n\n"
            "Введите код с помощью кнопок ниже (6 цифр):\n"
            f"Код: `{''}`",
            buttons=get_code_keyboard()
        )
    except Exception as e:
        logger.error(f"Ошибка при отправке кода: {e}", exc_info=True)
        await event.reply(f"❌ Ошибка: {str(e)}. Попробуйте ещё раз ввести номер.")
        user_data[chat_id].pop('step', None)

# ---------- ОБРАБОТКА КНОПОК ДЛЯ КОДА ----------
@bot_client.on(events.CallbackQuery(data=b'code_0'))
async def code_0(event):
    await handle_code_input(event, '0')

@bot_client.on(events.CallbackQuery(data=b'code_1'))
async def code_1(event):
    await handle_code_input(event, '1')

@bot_client.on(events.CallbackQuery(data=b'code_2'))
async def code_2(event):
    await handle_code_input(event, '2')

@bot_client.on(events.CallbackQuery(data=b'code_3'))
async def code_3(event):
    await handle_code_input(event, '3')

@bot_client.on(events.CallbackQuery(data=b'code_4'))
async def code_4(event):
    await handle_code_input(event, '4')

@bot_client.on(events.CallbackQuery(data=b'code_5'))
async def code_5(event):
    await handle_code_input(event, '5')

@bot_client.on(events.CallbackQuery(data=b'code_6'))
async def code_6(event):
    await handle_code_input(event, '6')

@bot_client.on(events.CallbackQuery(data=b'code_7'))
async def code_7(event):
    await handle_code_input(event, '7')

@bot_client.on(events.CallbackQuery(data=b'code_8'))
async def code_8(event):
    await handle_code_input(event, '8')

@bot_client.on(events.CallbackQuery(data=b'code_9'))
async def code_9(event):
    await handle_code_input(event, '9')

@bot_client.on(events.CallbackQuery(data=b'code_backspace'))
async def code_backspace(event):
    chat_id = event.chat_id
    data = user_data.get(chat_id, {})
    if data.get('step') != 'code':
        await event.answer("Сейчас нет активного запроса кода", alert=True)
        return
    
    current = data.get('code_input', '')
    if current:
        data['code_input'] = current[:-1]
    
    await event.answer()
    await event.edit(
        f"📱 Код подтверждения отправлен.\n\n"
        f"Введите код с помощью кнопок ниже (6 цифр):\n"
        f"Код: `{data['code_input']}`",
        buttons=get_code_keyboard()
    )

@bot_client.on(events.CallbackQuery(data=b'code_confirm'))
async def code_confirm(event):
    chat_id = event.chat_id
    data = user_data.get(chat_id, {})
    if data.get('step') != 'code':
        await event.answer("Сейчас нет активного запроса кода", alert=True)
        return
    
    code = data.get('code_input', '')
    if len(code) < 4:
        await event.answer("Код должен содержать минимум 4 цифры", alert=True)
        return
    
    await event.answer()
    await process_code_input(event, code)

async def handle_code_input(event, digit):
    chat_id = event.chat_id
    data = user_data.get(chat_id, {})
    if data.get('step') != 'code':
        await event.answer("Сейчас нет активного запроса кода", alert=True)
        return
    
    current = data.get('code_input', '')
    if len(current) >= 6:
        await event.answer("Код уже содержит 6 цифр. Нажмите ✓ для подтверждения или ⌫ для удаления", alert=True)
        return
    
    data['code_input'] = current + digit
    
    await event.answer()
    await event.edit(
        f"📱 Код подтверждения отправлен.\n\n"
        f"Введите код с помощью кнопок ниже (6 цифр):\n"
        f"Код: `{data['code_input']}`",
        buttons=get_code_keyboard()
    )

async def process_code_input(event, code):
    chat_id = event.chat_id
    data = user_data.get(chat_id, {})
    phone = data.get('phone')
    phone_code_hash = data.get('phone_code_hash')
    
    try:
        await user_client.sign_in(phone, code, phone_code_hash=phone_code_hash)
        # Успешно
        data.pop('step', None)
        data.pop('code_input', None)
        global user_authorized
        user_authorized = True
        
        await event.edit(
            "✅ Пользовательский аккаунт успешно авторизован! Выберите действие:",
            buttons=get_main_menu()
        )
    except PhoneCodeExpiredError:
        await event.edit(
            "❌ Срок действия кода истёк.\n\n"
            "Нажмите /resend для получения нового кода."
        )
    except PhoneCodeInvalidError:
        data['code_input'] = ''
        await event.edit(
            f"❌ Неверный код. Попробуйте ещё раз.\n\n"
            f"Введите код с помощью кнопок ниже (6 цифр):\n"
            f"Код: `{''}`",
            buttons=get_code_keyboard()
        )
    except SessionPasswordNeededError:
        data['step'] = 'password'
        await event.edit(
            "🔐 Включена двухфакторная аутентификация.\n"
            "Введите пароль (отправьте текстом):"
        )
    except Exception as e:
        logger.error(f"Ошибка при входе с кодом: {e}", exc_info=True)
        data['code_input'] = ''
        await event.edit(
            f"❌ Ошибка: {str(e)}\n\n"
            f"Попробуйте ещё раз или используйте /resend для нового кода.\n"
            f"Введите код с помощью кнопок ниже (6 цифр):\n"
            f"Код: `{''}`",
            buttons=get_code_keyboard()
        )

# ---------- КНОПКА ВЫХОДА ----------
@bot_client.on(events.CallbackQuery(data=b'logout'))
async def logout(event):
    await event.answer()
    chat_id = event.chat_id
    
    # Если есть активная рассылка - останавливаем
    if chat_id in spam_tasks and not spam_tasks[chat_id].done():
        spam_tasks[chat_id].cancel()
        del spam_tasks[chat_id]
    
    # Выполняем выход
    if logout_user(chat_id):
        await event.edit(
            "🚪 Вы успешно вышли из аккаунта.\n\n"
            "Для повторной авторизации используйте /start"
        )
    else:
        await event.edit(
            "❌ Ошибка при выходе из аккаунта.\n\n"
            "Попробуйте перезапустить бота командой /start"
        )

# ---------- ОСТАЛЬНЫЕ ОБРАБОТЧИКИ ----------

# Команда для повторной отправки кода
@bot_client.on(events.NewMessage(pattern='/resend'))
async def resend_code(event):
    chat_id = event.chat_id
    data = user_data.get(chat_id, {})
    if data.get('step') not in ('code', 'phone'):
        await event.reply("❌ Сейчас нет активного запроса кода. Начните с /start.")
        return
    phone = data.get('phone')
    if not phone:
        await event.reply("❌ Номер телефона не найден. Начните с /start.")
        return
    try:
        sent_code = await user_client.send_code_request(phone)
        data.update({
            'phone_code_hash': sent_code.phone_code_hash,
            'step': 'code',
            'code_input': ''
        })
        await event.reply(
            "📱 Новый код отправлен.\n\n"
            "Введите код с помощью кнопок ниже (6 цифр):\n"
            f"Код: `{''}`",
            buttons=get_code_keyboard()
        )
    except Exception as e:
        await event.reply(f"❌ Не удалось отправить код: {str(e)}")

@bot_client.on(events.NewMessage(func=lambda e: e.chat_id in user_data and user_data[e.chat_id].get('step') == 'password'))
async def password_input(event):
    if event.message.text.startswith('/'):
        return
    chat_id = event.chat_id
    password = event.message.text.strip()
    if not password:
        await event.reply("❌ Пароль не может быть пустым. Введите пароль ещё раз:")
        return
    try:
        await user_client.sign_in(password=password)
        user_data[chat_id].pop('step', None)
        global user_authorized
        user_authorized = True
        
        await event.reply(
            "✅ Пользовательский аккаунт успешно авторизован! Выберите действие:",
            buttons=get_main_menu()
        )
    except Exception as e:
        logger.error(f"Ошибка при входе с паролем: {e}", exc_info=True)
        await event.reply(f"❌ Ошибка: {str(e)}. Повторите ввод пароля.")

# ---------- КНОПКИ МЕНЮ ----------
@bot_client.on(events.CallbackQuery(data=b'add_users'))
async def add_users(event):
    await event.answer()
    await event.edit(
        "📎 Отправьте мне **CSV-файл** (с разделителем запятая).\n"
        "Ожидаются колонки: `username`, `user_id` (или одна из них).\n"
        "Если `username` пуст, буду использовать `user_id`.\n\n"
        "После загрузки файла используйте /start для возврата в меню."
    )

@bot_client.on(events.CallbackQuery(data=b'templates'))
async def setup_templates(event):
    await event.answer()
    await event.edit(
        "✏️ Чтобы добавить шаблон, отправьте команду:\n"
        "`/template Название Текст сообщения`\n\n"
        "Пример:\n"
        "`/template Приветствие Привет, коллега!`\n\n"
        "Все добавленные шаблоны будут показаны после добавления.\n\n"
        "После настройки используйте /start для возврата в меню."
    )

@bot_client.on(events.CallbackQuery(data=b'start_spam'))
async def start_spam_callback(event):
    await event.answer()
    if not await ensure_user_authorized():
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
    await event.reply(f"✅ Шаблон '{name}' добавлен!\n\n📋 Текущие шаблоны:\n{list_templates}\n\nИспользуйте /start для возврата в меню.")

# ---------- ОБРАБОТКА CSV-ФАЙЛА ----------
@bot_client.on(events.NewMessage(func=lambda e: e.chat_id in user_data and e.document))
async def process_csv(event):
    chat_id = event.chat_id
    if user_data[chat_id].get('step'):
        return

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
            f"Теперь добавьте шаблоны через /template.\n\n"
            f"Используйте /start для возврата в меню."
        )
    except Exception as e:
        await event.reply(f"❌ Ошибка при обработке CSV: {str(e)}")

# ---------- ЗАПУСК РАССЫЛКИ ----------
@bot_client.on(events.NewMessage(pattern='/start_spam'))
async def start_spam(event):
    chat_id = event.chat_id
    if not await ensure_user_authorized():
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
                if recipient.isdigit():
                    await user_client.send_message(int(recipient), template)
                else:
                    await user_client.send_message(recipient, template)
                delay = random.uniform(10, 250)
                await asyncio.sleep(delay)
            except RPCError as e:
                logger.error(f"Ошибка отправки для {recipient}: {e}")
        if chat_id in spam_tasks:
            del spam_tasks[chat_id]
        await bot_client.send_message(chat_id, "✅ Рассылка завершена.")
    except asyncio.CancelledError:
        if chat_id in spam_tasks:
            del spam_tasks[chat_id]
        await bot_client.send_message(chat_id, "⏹ Рассылка остановлена.")

# ---------- ОСТАНОВКА РАССЫЛКИ ----------
@bot_client.on(events.NewMessage(pattern='/stop_spam'))
async def stop_spam(event):
    chat_id = event.chat_id
    if chat_id in spam_tasks and not spam_tasks[chat_id].done():
        spam_tasks[chat_id].cancel()
        await event.reply("⏹ Рассылка остановлена.")
    else:
        await event.reply("⚠️ Активная рассылка не найдена.")

# ---------- ВЕБ-СЕРВЕР ДЛЯ RENDER ----------
async def health_check(request):
    return web.Response(text="OK")

async def run_web_server():
    app = web.Application()
    app.router.add_get('/', health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host='0.0.0.0', port=PORT)
    await site.start()
    logger.info(f"Веб-сервер запущен на порту {PORT}")
    await asyncio.Event().wait()

# ---------- ГЛАВНАЯ ФУНКЦИЯ ----------
async def main():
    await bot_client.start(bot_token=BOT_TOKEN)
    await user_client.connect()
    global user_authorized
    if await user_client.is_user_authorized():
        user_authorized = True
        logger.info("Пользовательский клиент уже авторизован (сессия восстановлена).")
    else:
        logger.info("Пользовательский клиент не авторизован – ожидаем ввода номера через /start.")

    await asyncio.gather(
        run_web_server(),
        bot_client.run_until_disconnected()
    )

if __name__ == '__main__':
    asyncio.run(main())
