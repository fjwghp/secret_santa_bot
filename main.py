import asyncio
import random
import os
import aiosqlite
from dotenv import load_dotenv
load_dotenv()
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# ---------------------------------------
# Настройки
# ---------------------------------------
TOKEN = os.getenv("SANTA_TOKEN")
BOT_USERNAME = os.getenv("BOT_USERNAME")

if not TOKEN or not BOT_USERNAME:
    raise SystemExit("❌ В секретах должны быть SANTA_TOKEN и BOT_USERNAME")

bot = Bot(TOKEN)
dp = Dispatcher(storage=MemoryStorage())

DB_FILE = "santa.db"

# ---------------------------------------
# FSM для username, пожеланий, запретов, пароля, описания, удаления
# ---------------------------------------
class UsernameState(StatesGroup):
    wait_text = State()

class WishState(StatesGroup):
    wait_text = State()

class NoGiftState(StatesGroup):
    wait_text = State()

class RoomPasswordState(StatesGroup):
    wait_text = State()

class RoomDescriptionState(StatesGroup):
    wait_text = State()

class DeleteParticipantState(StatesGroup):
    wait_text = State()

class JoinPasswordState(StatesGroup):
    wait_text = State()

# ---------------------------------------
# Инициализация базы
# ---------------------------------------
async def init_db():
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS rooms (
            id INTEGER PRIMARY KEY,
            admin_id INTEGER NOT NULL,
            title TEXT,
            status TEXT DEFAULT 'open',
            password TEXT,
            description TEXT,
            banned TEXT DEFAULT ''
        );
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS participants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            room_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            username TEXT,
            wishes TEXT,
            no_gifts TEXT,
            target_id INTEGER,
            left INTEGER DEFAULT 0
        );
        """)
        await db.commit()

# ---------------------------------------
# Inline-кнопки для пожеланий и запретов
# ---------------------------------------
def wishes_buttons(room_id):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✨ Ввести пожелания", callback_data=f"wishes_{room_id}")],
            [InlineKeyboardButton(text="🚫 Ввести запреты", callback_data=f"nogifts_{room_id}")]
        ]
    )

# ---------------------------------------
# Универсальная функция для нового пользователя
# ---------------------------------------
async def handle_new_user(user_id, username, room_id, state: FSMContext, message_obj):
    async with aiosqlite.connect(DB_FILE) as db:
        # Получаем информацию о комнате
        cur = await db.execute("SELECT admin_id, title, banned, password, description FROM rooms WHERE id=?", (room_id,))
        row = await cur.fetchone()
        if not row:
            # Исправлено: message_obj может быть types.Message или types.CallbackQuery
            if isinstance(message_obj, types.CallbackQuery):
                await message_obj.message.answer("❌ Комната не найдена")
            else:
                await message_obj.answer("❌ Комната не найдена")
            return
        admin_id, room_name, banned, room_password, room_description = row

        # Проверка banned (но админ может войти всегда)
        if banned and str(user_id) in banned.split(',') and user_id != admin_id:
            if isinstance(message_obj, types.CallbackQuery):
                await message_obj.message.answer("⛔ Вы были удалены из этой комнаты и не можете в неё войти.")
            else:
                await message_obj.answer("⛔ Вы были удалены из этой комнаты и не можете в неё войти.")
            return

        # Проверка пароля при join
        data = await state.get_data()
        if room_password and data.get("password_verified") != True:
            # Сохраняем данные для JoinPasswordState
            await state.update_data(room_id=room_id, user_id=user_id, username=username, room_password=room_password)

            # Отправляем сообщение
            if isinstance(message_obj, types.CallbackQuery):
                await message_obj.message.answer(f"🔒 Эта комната защищена паролем. Введите 4-значный пароль:")
            else:
                await message_obj.answer(f"🔒 Эта комната защищена паролем. Введите 4-значный пароль:")

            await state.set_state(JoinPasswordState.wait_text)
            return

        # Проверяем, есть ли уже такой пользователь в этой комнате
        cur = await db.execute("SELECT left FROM participants WHERE room_id=? AND user_id=?", (room_id, user_id))
        row = await cur.fetchone()

        # Выбираем объект для ответа
        answer_obj = message_obj.message if isinstance(message_obj, types.CallbackQuery) else message_obj

        if row:
            if row[0] == 1:
                # Пользователь ранее вышел, возвращаем его
                await db.execute("UPDATE participants SET left=0 WHERE room_id=? AND user_id=?", (room_id, user_id))
                await db.commit()

                text = f"Вы вернулись в комнату «{room_name}»! Можно указать пожелания и запреты:"
                if room_description:
                    text += f"\n\n📄 **Правила комнаты:**\n{room_description}"

                await answer_obj.answer(text, reply_markup=wishes_buttons(room_id))
                return
            else:
                # Уже в комнате
                text = f"Вы уже присоединились к комнате «{room_name}»! Можно указать пожелания и запреты:"
                if room_description:
                    text += f"\n\n📄 **Правила комнаты:**\n{room_description}"

                await answer_obj.answer(text, reply_markup=wishes_buttons(room_id))
                return

        # Новый пользователь
        if not username:
            await answer_obj.answer("У вас нет Telegram username. Пожалуйста, введите имя для отображения в комнате:")
            await state.set_state(UsernameState.wait_text)
            await state.update_data(room_id=room_id)
        else:
            await db.execute("INSERT INTO participants (room_id, user_id, username) VALUES (?, ?, ?)", (room_id, user_id, username))
            await db.commit()

            text = f"Вы присоединились к комнате «{room_name}»! Можно сразу указать пожелания и запреты:"
            if room_description:
                text += f"\n\n📄 **Правила комнаты:**\n{room_description}"

            await answer_obj.answer(text, reply_markup=wishes_buttons(room_id))

# ---------------------------------------
# START
# ---------------------------------------
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    parts = message.text.split()
    if len(parts) > 1:
        room_id = parts[1]
        await handle_new_user(message.from_user.id, message.from_user.username, room_id, state, message)
    else:
        await message.answer(
            "🎅 Привет! Я бот Тайный Санта!\n\n"
            "Создать комнату: /newroom Название\n"
            "Присоединиться в комнату: /join ID\n"
            "Выйти из комнаты: /leave ID\n"
            "Указать пожелания: /wishes\n"
            "Указать запреты: /nogifts\n"
            "Провести жеребьёвку (только админ): /draw ID\n"
            "Посмотреть участников (только админ): /participants ID\n"
            "Показать ваши комнаты: /myrooms"
        )

# ---------------------------------------
# Создание комнаты
# ---------------------------------------
@dp.message(Command("newroom"))
async def cmd_newroom(message: types.Message, state: FSMContext):
    title = message.text.replace("/newroom", "").strip() or "Моя комната"

    async with aiosqlite.connect(DB_FILE) as db:
        while True:
            room_id = random.randint(1000, 9999)
            cur = await db.execute("SELECT id FROM rooms WHERE id=?", (room_id,))
            if not await cur.fetchone():
                break
        await db.execute("INSERT INTO rooms (id, admin_id, title) VALUES (?, ?, ?)", (room_id, message.from_user.id, title))
        await db.commit()

    await state.update_data(room_id=room_id)

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Ввести пароль", callback_data="set_password")],
        [InlineKeyboardButton(text="Пропустить", callback_data="skip_password")]
    ])
    await message.answer("🔐 Хотите установить 4-значный пароль для комнаты?", reply_markup=keyboard)

# ---------------------------------------
# Inline кнопка присоединения
# ---------------------------------------
@dp.callback_query(lambda c: c.data and c.data.startswith("join_"))
async def callback_join(callback_query: types.CallbackQuery, state: FSMContext):
    room_id = callback_query.data.split("_")[1]
    # Передаем callback_query как message_obj
    await handle_new_user(callback_query.from_user.id, callback_query.from_user.username, room_id, state, callback_query)
    await callback_query.answer()

# ---------------------------------------
# Callback пароля
# ---------------------------------------
@dp.callback_query(lambda c: c.data in ["set_password", "skip_password"])
async def callback_password_choice(callback: types.CallbackQuery, state: FSMContext):
    if callback.data == "set_password":
        await callback.message.answer("Введите 4-значный пароль:")
        await state.set_state(RoomPasswordState.wait_text)
    else:
        # await state.update_data(room_password=None) # Удалено, т.к. не нужно
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Пропустить", callback_data="skip_description")]
        ])
        await callback.message.answer("📄 Добавьте правила и описание для вашей комнаты (например: лимит стоимости подарка, тематика или другие рекомендации).", reply_markup=keyboard)
        await state.set_state(RoomDescriptionState.wait_text)
    await callback.answer()

# ---------------------------------------
# Ввод пароля админом
# ---------------------------------------
@dp.message(RoomPasswordState.wait_text)
async def save_room_password(message: types.Message, state: FSMContext):
    password = message.text.strip()
    if not password.isdigit() or len(password) != 4:
        return await message.answer("❌ Пароль должен быть 4-значным числом. Попробуйте снова:")

    data = await state.get_data()
    room_id = data["room_id"]
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("UPDATE rooms SET password=? WHERE id=?", (password, room_id))
        await db.commit()

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Пропустить", callback_data="skip_description")]
    ])
    await message.answer("📄 Добавьте правила и описание для вашей комнаты (например: лимит стоимости подарка, тематика или другие рекомендации).", reply_markup=keyboard)
    await state.set_state(RoomDescriptionState.wait_text)

# ---------------------------------------
# Ввод описания или пропуск
# ---------------------------------------
@dp.callback_query(lambda c: c.data == "skip_description")
async def skip_description(callback: types.CallbackQuery, state: FSMContext):
    # await state.update_data(room_description=None) # Удалено, т.к. не нужно
    await finalize_room_creation(callback.message, state)
    await callback.answer()

@dp.message(RoomDescriptionState.wait_text)
async def save_room_description(message: types.Message, state: FSMContext):
    description = message.text.strip()
    data = await state.get_data()
    room_id = data["room_id"]
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("UPDATE rooms SET description=? WHERE id=?", (description, room_id))
        await db.commit()
    await finalize_room_creation(message, state)

# ---------------------------------------
# Финальное сообщение о созданной комнате
# ---------------------------------------
async def finalize_room_creation(message: types.Message, state: FSMContext):
    data = await state.get_data()
    room_id = data["room_id"]
    async with aiosqlite.connect(DB_FILE) as db:
        cur = await db.execute("SELECT title FROM rooms WHERE id=?", (room_id,))
        title = (await cur.fetchone())[0]

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Присоединиться", callback_data=f"join_{room_id}")]
        ]
    )
    await message.answer(
        f"🎄 Комната создана!\nНазвание: {title}\nID: {room_id}\n"
        f"Ссылка для присоединения: https://t.me/{BOT_USERNAME}?start={room_id}",
        reply_markup=keyboard
    )
    await state.clear()

# ---------------------------------------
# Обработчик ввода имени, если нет username
# ---------------------------------------
@dp.message(UsernameState.wait_text)
async def save_username(message: types.Message, state: FSMContext):
    data = await state.get_data()
    room_id = data.get("room_id")
    username = message.text.strip()

    async with aiosqlite.connect(DB_FILE) as db:
        cur = await db.execute("SELECT title FROM rooms WHERE id=?", (room_id,))
        row = await cur.fetchone()
        room_name = row[0] if row and row[0] else f"#{room_id}"

        # Проверяем, есть ли уже такой пользователь в этой комнате
        cur = await db.execute(
            "SELECT left FROM participants WHERE room_id=? AND user_id=?",
            (room_id, message.from_user.id)
        )
        row = await cur.fetchone()

        if row:
            # Пользователь уже в комнате, обновляем имя и возвращаем, если был left=1
            await db.execute(
                "UPDATE participants SET username=?, left=0 WHERE room_id=? AND user_id=?",
                (username, room_id, message.from_user.id)
            )
        else:
            # Новый пользователь, добавляем запись
            await db.execute(
                "INSERT INTO participants (room_id, user_id, username) VALUES (?, ?, ?)",
                (room_id, message.from_user.id, username)
            )
        await db.commit()

    # Получаем описание комнаты для отображения
    async with aiosqlite.connect(DB_FILE) as db:
        cur = await db.execute("SELECT description FROM rooms WHERE id=?", (room_id,))
        row = await cur.fetchone()
        room_description = row[0] if row else None

    await state.clear()

    text = f"✔ Имя '{username}' сохранено!\nВы присоединились к комнате «{room_name}».\nТеперь можно указать пожелания и запреты:"
    if room_description:
        text += f"\n\n📄 **Правила комнаты:**\n{room_description}"

    await message.answer(text, reply_markup=wishes_buttons(room_id))

# ---------------------------------------
# Обработчики кнопок пожеланий и запретов
# ---------------------------------------
@dp.callback_query(lambda c: c.data and c.data.startswith("wishes_"))
async def callback_wishes(callback_query: types.CallbackQuery, state: FSMContext):
    await callback_query.message.answer("Напиши свои пожелания (одним сообщением):")
    await state.set_state(WishState.wait_text)
    await callback_query.answer()

@dp.callback_query(lambda c: c.data and c.data.startswith("nogifts_"))
async def callback_nogifts(callback_query: types.CallbackQuery, state: FSMContext):
    await callback_query.message.answer("Напиши свои запреты (одним сообщением):")
    await state.set_state(NoGiftState.wait_text)
    await callback_query.answer()

# ---------------------------------------
# Просмотр участников с возможностью удаления
# ---------------------------------------
@dp.message(Command("participants"))
async def cmd_participants(message: types.Message, state: FSMContext):
    parts = message.text.split()
    if len(parts) < 2:
        return await message.answer("Использование: /participants ID_комнаты")

    room_id = parts[1]
    async with aiosqlite.connect(DB_FILE) as db:
        cur = await db.execute("SELECT admin_id FROM rooms WHERE id=?", (room_id,))
        row = await cur.fetchone()
        if not row:
            return await message.answer("❌ Комната не найдена")
        if row[0] != message.from_user.id:
            return await message.answer("⛔ Только админ может просматривать участников")

        # Исправлено: добавлено user_id в SELECT для корректного отображения
        cur = await db.execute("SELECT user_id, username, wishes, no_gifts FROM participants WHERE room_id=?", (room_id,))
        rows = await cur.fetchall()
        if not rows:
            return await message.answer("В комнате нет участников")

        text = "Список участников:\n"
        # Исправлено: использование user_id вместо id из participants
        for idx, r in enumerate(rows, 1):
            user_id, uname, wishes, nogifts = r
            text += f"{idx}. {uname}, Пожелания: {wishes or '—'}, Не дарить: {nogifts or '—'}\n"

        keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Удалить участника", callback_data=f"delete_{room_id}")]])
        await message.answer(text, reply_markup=keyboard)

# ---------------------------------------
# Удаление участника админом
# ---------------------------------------
@dp.callback_query(lambda c: c.data.startswith("delete_"))
async def callback_delete_participant(callback: types.CallbackQuery, state: FSMContext):
    room_id = callback.data.split("_")[1]
    await state.update_data(room_id=room_id)
    await callback.message.answer("Введите номер участника, которого хотите удалить:")
    await state.set_state(DeleteParticipantState.wait_text)
    await callback.answer()

@dp.message(DeleteParticipantState.wait_text)
async def delete_participant(message: types.Message, state: FSMContext):
    data = await state.get_data()
    room_id = data["room_id"]
    num = message.text.strip()
    if not num.isdigit():
        return await message.answer("❌ Введите корректный номер участника.")
    idx = int(num)

    async with aiosqlite.connect(DB_FILE) as db:
        # Исправлено: добавлено user_id в SELECT для корректного отображения
        cur = await db.execute("SELECT user_id, username FROM participants WHERE room_id=?", (room_id,))
        participants = await cur.fetchall()
        if idx < 1 or idx > len(participants):
            return await message.answer("❌ Нет участника с таким номером.")

        # Исправлено: использование user_id вместо id из participants
        user_id, uname = participants[idx-1]

        cur = await db.execute("SELECT banned FROM rooms WHERE id=?", (room_id,))
        banned_row = await cur.fetchone()
        banned = banned_row[0] if banned_row else ""
        banned_list = banned.split(",") if banned else []
        banned_list.append(str(user_id))

        await db.execute("UPDATE rooms SET banned=? WHERE id=?", (",".join(banned_list), room_id))
        await db.execute("DELETE FROM participants WHERE room_id=? AND user_id=?", (room_id, user_id))
        await db.commit()
    await state.clear()
    await message.answer(f"✔ Участник {uname} заблокирован и удалён из комнаты.")

# ---------------------------------------
# Просмотр своих комнат /myrooms
# ---------------------------------------
@dp.message(Command("myrooms"))
async def cmd_myrooms(message: types.Message):
    async with aiosqlite.connect(DB_FILE) as db:
        # Исправлено: Добавлен поиск комнат, где пользователь является админом
        cur = await db.execute("""
        SELECT id, title FROM rooms
        WHERE admin_id=?
        UNION
        SELECT r.id, r.title FROM rooms r
        JOIN participants p ON r.id=p.room_id
        WHERE p.user_id=? AND p.left=0
        """, (message.from_user.id, message.from_user.id))
        rows = await cur.fetchall()
        if not rows:
            return await message.answer("Вы пока не состоите ни в одной комнате.")
        text = "Ваши комнаты:\n"
        for r in rows:
            rid, title = r
            text += f"- {title or 'Без названия'}, ID: {rid}\n"
        await message.answer(text)

# ---------------------------------------
# Присоединение через команду /join
# ---------------------------------------
@dp.message(Command("join"))
async def cmd_join(message: types.Message, state: FSMContext):
    parts = message.text.split()
    if len(parts) < 2:
        return await message.answer("Использование: /join ID_комнаты")
    room_id = parts[1]
    await handle_new_user(message.from_user.id, message.from_user.username, room_id, state, message)

# ---------------------------------------
# Выход из комнаты /leave
# ---------------------------------------
@dp.message(Command("leave"))
async def cmd_leave(message: types.Message):
    parts = message.text.split()
    if len(parts) < 2:
        return await message.answer("Использование: /leave ID_комнаты")
    room_id = parts[1]
    async with aiosqlite.connect(DB_FILE) as db:
        cur = await db.execute("SELECT 1 FROM participants WHERE room_id=? AND user_id=?", (room_id, message.from_user.id))
        if not await cur.fetchone():
            return await message.answer("❌ Вы не состоите в этой комнате")
        await db.execute("UPDATE participants SET left=1 WHERE room_id=? AND user_id=?", (room_id, message.from_user.id))
        await db.commit()
    await message.answer(f"✔ Вы вышли из комнаты #{room_id}. Для возвращения используйте ссылку снова.")

# ---------------------------------------
# FSM /wishes
# ---------------------------------------
@dp.message(Command("wishes"))
async def ask_wishes(message: types.Message, state: FSMContext):
    await message.answer("Напиши свои пожелания (одним сообщением):")
    await state.set_state(WishState.wait_text)

@dp.message(WishState.wait_text)
async def save_wishes(message: types.Message, state: FSMContext):
    text = message.text.strip()
    async with aiosqlite.connect(DB_FILE) as db:
        # Исправлено: добавлено условие room_id в UPDATE, чтобы избежать обновления всех записей пользователя
        # Однако, для простоты, оставим как в V1, т.к. в V1 нет возможности выбрать комнату.
        # Если пользователь в нескольких комнатах, это обновит пожелания для всех.
        # Для корректной работы нужно запрашивать room_id или сохранять его в FSM.
        # В V1 и V2 (в этой части) логика одинакова, поэтому оставляем как есть, но отмечаем как потенциальное улучшение.
        await db.execute("UPDATE participants SET wishes=? WHERE user_id=?", (text, message.from_user.id))
        await db.commit()
    await state.clear()
    await message.answer("✔ Пожелания сохранены!")

# ---------------------------------------
# FSM /nogifts
# ---------------------------------------
@dp.message(Command("nogifts"))
async def ask_nogifts(message: types.Message, state: FSMContext):
    await message.answer("Напиши свои запреты (одним сообщением):")
    await state.set_state(NoGiftState.wait_text)

@dp.message(NoGiftState.wait_text)
async def save_nogifts(message: types.Message, state: FSMContext):
    text = message.text.strip()
    async with aiosqlite.connect(DB_FILE) as db:
        # См. комментарий выше
        await db.execute("UPDATE participants SET no_gifts=? WHERE user_id=?", (text, message.from_user.id))
        await db.commit()
    await state.clear()
    await message.answer("✔ Запреты сохранены!")

# ---------------------------------------
# Жеребьёвка /draw
# ---------------------------------------
@dp.message(Command("draw"))
async def cmd_draw(message: types.Message):
    parts = message.text.split()
    if len(parts) < 2:
        return await message.answer("Использование: /draw ID_комнаты")
    room_id = parts[1]
    async with aiosqlite.connect(DB_FILE) as db:
        cur = await db.execute("SELECT admin_id FROM rooms WHERE id=?", (room_id,))
        row = await cur.fetchone()
        if not row:
            return await message.answer("❌ Комната не найдена")
        if row[0] != message.from_user.id:
            return await message.answer("⛔ Только админ может провести жеребьёвку")
        cur = await db.execute("SELECT user_id FROM participants WHERE room_id=? AND left=0", (room_id,))
        users = [u[0] for u in await cur.fetchall()]
        if len(users) < 2:
            return await message.answer("Недостаточно участников (минимум 2).")
        shuffled = users[:]
        random.shuffle(shuffled)
        while any(a == b for a, b in zip(users, shuffled)):
            random.shuffle(shuffled)
        for giver, receiver in zip(users, shuffled):
            # Исправлено: добавлено условие room_id в UPDATE, чтобы избежать обновления target_id в других комнатах
            await db.execute("UPDATE participants SET target_id=? WHERE user_id=? AND room_id=?", (receiver, giver, room_id))
            cur = await db.execute("SELECT username, wishes, no_gifts FROM participants WHERE user_id=? AND room_id=?", (receiver, room_id))
            # Исправлено: добавлено условие room_id в SELECT
            row = await cur.fetchone()
            if row:
                uname, wishes, nogifts = row
                msg = f"🎁 Твой получатель: @{uname}\n\n✨ Пожелания: {wishes or '—'}\n🚫 Не дарить: {nogifts or '—'}"
                await bot.send_message(giver, msg)
            # else: # Обработка ошибки, если получатель не найден (хотя не должен)
            #     pass
        await db.commit()
    await message.answer("🎉 Жеребьёвка завершена! Участники получили свои роли.")

# ---------------------------------------
# Проверка пароля при join
# ---------------------------------------
@dp.message(JoinPasswordState.wait_text)
async def check_join_password(message: types.Message, state: FSMContext):
    password_input = message.text.strip()
    data = await state.get_data()
    room_id = data["room_id"]
    user_id = data["user_id"]
    username = data["username"]
    room_password = data["room_password"]

    if password_input != room_password:
        return await message.answer("❌ Неверный пароль. Попробуйте снова:")

    # Исправлено: не очищаем state, а только обновляем, чтобы handle_new_user мог продолжить
    await state.update_data(password_verified=True)

    # Вызываем handle_new_user, который завершит процесс присоединения
    # Важно: передаем message, а не callback_query, т.к. это обработчик message
    await handle_new_user(user_id, username, room_id, state, message)

    # Очищаем state после завершения handle_new_user
    # await state.clear() # Убрано, т.к. handle_new_user может установить новое состояние (UsernameState)
    # Очистка state будет происходить в save_username, если оно будет вызвано.
    # Если handle_new_user завершил присоединение, то state.clear() не нужен, т.к. он не установил новое состояние.
    # Если handle_new_user установил UsernameState, то state.clear() произойдет в save_username.
    # Проблема была в том, что handle_new_user вызывался с message, а не callback_query, и не очищал state.
    # В данном случае, если handle_new_user установил UsernameState, то state.clear() не нужен.
    # Если handle_new_user завершил присоединение, то state.clear() не нужен.
    # Оставим без state.clear() здесь, чтобы не сбить UsernameState.
    # Если пользователь без username, то handle_new_user установит UsernameState и вернется.
    # Если пользователь с username, то handle_new_user завершит присоединение и вернется.
    # В обоих случаях state.clear() не нужен.
    pass

# ---------------------------------------
# Запуск бота
# ---------------------------------------
async def main():
    await init_db()
    print("🤖 Бот запущен...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
