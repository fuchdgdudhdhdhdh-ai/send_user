from telethon import TelegramClient, events
import asyncio
import random
import json
import os

# Конфигурация Render.com (замените на свои данные)
API_ID = '35981014'
API_HASH = '4e788ed1a686308838891734a4173c48'
BOT_TOKEN = '8735415736:AAE72qIhErgje2egt2_1DSlpOszhDCootXY'

client = TelegramClient('session_name', API_ID, API_HASH).start(bot_token=BOT_TOKEN)

user_data = {}  # Хранение данных пользователей (номера, юзерреймы и т.д.)

@client.on(events.NewMessage(pattern='/start'))
async def start(event):
    await event.reply("Введите ваш номер телефона:")
    user_data[event.chat_id] = {'step': 'phone'}

@client.on(events.NewMessage(func=lambda e: e.chat_id in user_data and user_data[e.chat_id]['step'] == 'phone'))
async def phone_input(event):
    phone = event.message.text.strip()
    
    try:
        await client.send_code_request(phone)
        user_data[event.chat_id].update({'phone': phone, 'step': 'code'})
        await event.reply("Код подтверждения получен. Введите код:")
        
    except Exception as e:
        await event.reply(f"Ошибка: {str(e)}. Повторите ввод номера.")

@client.on(events.NewMessage(func=lambda e: e.chat_id in user_data and user_data[e.chat_id]['step'] == 'code'))
async def code_input(event):
    code = event.message.text.strip()
    
    try:
        await client.sign_in(user_data[event.chat_id]['phone'], code)
        del user_data[event.chat_id]['step']
        
        keyboard = [
            [Button.inline("Добавить юзерреймы", b'add_users')],
            [Button.inline("Настроить шаблоны", b'templates')]
        ]
        await event.reply("Успешная авторизация!", buttons=keyboard)
        
    except SessionPasswordNeededError:
        user_data[event.chat_id]['step'] = 'password'
        await event.reply("Включена 2FA. Введите пароль:")
    
    except Exception as e:
        await event.reply(f"Ошибка: {str(e)}. Повторите ввод кода.")

@client.on(events.CallbackQuery(data=b'add_users'))
async def add_users(event):
    await event.answer()
    await event.edit("Отправьте файл с юзерреймами (каждый в новой строке):")
    
@client.on(events.NewMessage(func=lambda e: e.chat_id in user_data and 'step' not in user_data[e.chat_id]))
async def process_file(event):
    if event.document:
        file = await client.download_media(event.message, f"{event.chat.id}_users.txt")
        
        with open(file, 'r') as f:
            users = [line.strip() for line in f.readlines()]
            
        user_data[event.chat_id]['users'] = users
        os.remove(file)
        
        await event.reply("Юзерреймы загружены. Теперь настройте шаблоны.")

@client.on(events.CallbackQuery(data=b'templates'))
async def setup_templates(event):
    await event.answer()
    
    message = "Добавьте шаблоны через /template [название] [текст]. Пример:\n/template Приветствие 'Привет, как дела?'"
    await event.edit(message)

@client.on(events.NewMessage(pattern='/template'))
async def add_template(event):
    args = event.message.text.split(' ', 2)
    
    if len(args) < 3:
        await event.reply("Неверный формат. Используйте: /template [название] [текст]")
        return
    
    name, text = args[1], args[2]
    user_data[event.chat_id].setdefault('templates', {})[name] = text
    await event.reply(f"Шаблон '{name}' добавлен!")

@client.on(events.NewMessage(pattern='/start_spam'))
async def start_spam(event):
    if not all(k in user_data.get(event.chat_id, {}) for k in ['users', 'templates']):
        await event.reply("Сначала загрузите юзерреймы и настройте шаблоны!")
        return
    
    async def send_messages():
        while True:
            for user in user_data[event.chat_id]['users']:
                template = random.choice(list(user_data[event.chat_id]['templates'].values()))
                
                try:
                    await client.send_message(user, template)
                    delay = random.uniform(10, 250)  # Рандомная задержка от 10 до 250 сек
                    await asyncio.sleep(delay)
                    
                except Exception as e:
                    await event.reply(f"Ошибка отправки для {user}: {str(e)}")
    
    asyncio.create_task(send_messages())
    await event.reply("Рассылка запущена!")

async def main():
    await client.start()
    await client.run_until_disconnected()

if name == '__main__':
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
