"""
EduConnect UZ — Telegram Bot
Stack: Python 3.11+ | aiogram 3.x | Supabase
"""

import asyncio
import logging
import random
import os
from datetime import datetime, timedelta
from typing import Optional

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from supabase import create_client, Client

# ─── CONFIG ───────────────────────────────────────────────────────────────────

BOT_TOKEN    = os.environ["BOT_TOKEN"]       # from @BotFather
CHANNEL_ID   = os.environ["CHANNEL_ID"]      # e.g. @educonnect_uz
ADMIN_ID     = int(os.environ["ADMIN_ID"])   # your Telegram user ID
SUPABASE_URL = os.environ["SUPABASE_URL"]    # from Supabase project settings
SUPABASE_KEY = os.environ["SUPABASE_KEY"]    # anon/public key

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# ─── GLOBALS (initialized in main) ────────────────────────────────────────────

supabase: Client = None
bot: Bot = None

# ─── ANONYMOUS NICKNAMES ──────────────────────────────────────────────────────

ANIMALS = [
    "Burgut", "Sher", "Bo'ri", "Tulki", "Ot", "Bars", "Lochin", "Qoplan",
    "Fil", "Kiyik", "Jayron", "Qush", "Ilbiz", "Panda", "Delfin"
]

def generate_nickname() -> str:
    return f"{random.choice(ANIMALS)} #{random.randint(10, 99)}"

# ─── SUBJECTS ─────────────────────────────────────────────────────────────────

SUBJECTS = {
    "math":      "📐 Matematika",
    "english":   "🔤 Ingliz tili",
    "chemistry": "⚗️ Kimyo",
    "biology":   "🧬 Biologiya",
    "physics":   "⚡ Fizika",
    "history":   "📖 Tarix",
    "it":        "💻 IT / Dasturlash",
    "geography": "🌍 Geografiya",
    "other":     "📌 Boshqa",
}

# ─── DB HELPERS ───────────────────────────────────────────────────────────────

def get_user(user_id: int) -> Optional[dict]:
    res = supabase.table("users").select("*").eq("user_id", user_id).execute()
    return res.data[0] if res.data else None

def create_user(user_id: int, role: str, subjects: list = []) -> str:
    nickname = generate_nickname()
    supabase.table("users").upsert({
        "user_id":    user_id,
        "role":       role,
        "nickname":   nickname,
        "subjects":   subjects,
        "reputation": 0,
        "star_badge": False,
    }).execute()
    return nickname

def get_question(question_id: int) -> Optional[dict]:
    res = supabase.table("questions").select("*").eq("id", question_id).execute()
    return res.data[0] if res.data else None

def get_teachers_for_subject(subject: str) -> list:
    res = (
        supabase.table("users")
        .select("user_id")
        .eq("role", "teacher")
        .contains("subjects", [subject])
        .execute()
    )
    return [r["user_id"] for r in res.data]

def update_reputation(teacher_id: int):
    user = get_user(teacher_id)
    if not user:
        return
    new_rep = user["reputation"] + 1
    update  = {"reputation": new_rep}
    if new_rep >= 10:
        update["star_badge"] = True
    supabase.table("users").update(update).eq("user_id", teacher_id).execute()

def insert_question(student_id: int, subject: str, text: str,
                    photo_file_id: Optional[str], nickname: str) -> int:
    res = supabase.table("questions").insert({
        "student_id":    student_id,
        "subject":       subject,
        "text":          text,
        "photo_file_id": photo_file_id,
        "nickname":      nickname,
        "status":        "open",
    }).execute()
    return res.data[0]["id"]

def insert_answer(question_id: int, teacher_id: int, text: str) -> int:
    res = supabase.table("answers").insert({
        "question_id": question_id,
        "teacher_id":  teacher_id,
        "text":        text,
    }).execute()
    return res.data[0]["id"]

def get_answer(answer_id: int) -> Optional[dict]:
    res = supabase.table("answers").select("*").eq("id", answer_id).execute()
    return res.data[0] if res.data else None

def update_question(question_id: int, data: dict):
    supabase.table("questions").update(data).eq("id", question_id).execute()

def update_answer(answer_id: int, data: dict):
    supabase.table("answers").update(data).eq("id", answer_id).execute()

def insert_report(reporter_id: int, question_id: int):
    supabase.table("reports").insert({
        "reporter_id": reporter_id,
        "question_id": question_id,
    }).execute()

# ─── FSM STATES ───────────────────────────────────────────────────────────────

class RegisterTeacher(StatesGroup):
    subjects = State()

class PostQuestion(StatesGroup):
    subject = State()
    text    = State()
    photo   = State()
    confirm = State()

class PostAnswer(StatesGroup):
    text = State()

# ─── KEYBOARDS ────────────────────────────────────────────────────────────────

def role_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎓 Men o'quvchiman",    callback_data="role_student")],
        [InlineKeyboardButton(text="👨‍🏫 Men o'qituvchiman", callback_data="role_teacher")],
    ])

def subjects_kb(selected: list = []):
    buttons = [
        [InlineKeyboardButton(
            text=f"{'✅ ' if k in selected else ''}{v}",
            callback_data=f"subj_{k}"
        )]
        for k, v in SUBJECTS.items()
    ]
    buttons.append([InlineKeyboardButton(text="✔️ Tayyor", callback_data="subj_done")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def subject_select_kb():
    buttons, row = [], []
    for i, (k, v) in enumerate(SUBJECTS.items()):
        row.append(InlineKeyboardButton(text=v, callback_data=f"qsubj_{k}"))
        if len(row) == 2 or i == len(SUBJECTS) - 1:
            buttons.append(row); row = []
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def skip_photo_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏭ Rasmisiz davom etish", callback_data="skip_photo")]
    ])

def confirm_question_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Yuborish",      callback_data="confirm_yes"),
         InlineKeyboardButton(text="❌ Bekor qilish", callback_data="confirm_no")]
    ])

def feedback_kb(question_id: int, answer_id: int):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Foydali",     callback_data=f"fb_foydali_{question_id}_{answer_id}"),
         InlineKeyboardButton(text="🤔 Tushunmadim", callback_data=f"fb_tushunmadim_{question_id}_{answer_id}")],
        [InlineKeyboardButton(text="📝 To'liq emas", callback_data=f"fb_toliq_{question_id}_{answer_id}"),
         InlineKeyboardButton(text="❌ Noto'g'ri",   callback_data=f"fb_notogri_{question_id}_{answer_id}")],
    ])

def channel_question_kb(question_id: int):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📝 Javob berish", callback_data=f"answer_{question_id}"),
         InlineKeyboardButton(text="⚠️ Xabar berish", callback_data=f"report_{question_id}")]
    ])

def main_menu_kb(role: str):
    if role == "student":
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❓ Yangi savol yuborish", callback_data="new_question")],
            [InlineKeyboardButton(text="👤 Profilim",             callback_data="my_profile")],
        ])
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Ochiq savollar", callback_data="open_questions")],
        [InlineKeyboardButton(text="👤 Profilim",       callback_data="my_profile")],
    ])

# ─── ROUTER ───────────────────────────────────────────────────────────────────

router = Router()

# ─── /start ───────────────────────────────────────────────────────────────────

@router.message(CommandStart())
async def cmd_start(msg: Message, state: FSMContext):
    await state.clear()
    user = get_user(msg.from_user.id)
    if user:
        role_label = "O'quvchi 🎓" if user["role"] == "student" else "O'qituvchi 👨‍🏫"
        await msg.answer(
            f"Xush kelibsiz, <b>{user['nickname']}</b>! 👋\n"
            f"Rolingiz: {role_label}\n\nNima qilmoqchisiz?",
            parse_mode="HTML", reply_markup=main_menu_kb(user["role"])
        )
    else:
        await msg.answer(
            "🎓 <b>EduConnect UZ</b> ga xush kelibsiz!\n\n"
            "Bu bot orqali o'quvchilar savollarini yuboradilar, "
            "o'qituvchilar esa topib javob beradilar.\n\nSiz kimsiz?",
            parse_mode="HTML", reply_markup=role_kb()
        )

# ─── REGISTRATION ─────────────────────────────────────────────────────────────

@router.callback_query(F.data == "role_student")
async def reg_student(cb: CallbackQuery):
    if get_user(cb.from_user.id):
        await cb.answer("Siz allaqachon ro'yxatdan o'tgansiz!"); return
    nickname = create_user(cb.from_user.id, "student")
    await cb.message.edit_text(
        f"✅ O'quvchi sifatida ro'yxatdan o'tdingiz!\n\n"
        f"Taxallusingiz: <b>{nickname}</b> 🎭\n\nEndi savol yuborishingiz mumkin!",
        parse_mode="HTML", reply_markup=main_menu_kb("student")
    )
    await cb.answer()

@router.callback_query(F.data == "role_teacher")
async def reg_teacher_start(cb: CallbackQuery, state: FSMContext):
    if get_user(cb.from_user.id):
        await cb.answer("Siz allaqachon ro'yxatdan o'tgansiz!"); return
    await state.update_data(selected_subjects=[])
    await cb.message.edit_text(
        "👨‍🏫 Qaysi fanlardan dars berasiz?\n(Bir yoki bir nechta tanlang)",
        reply_markup=subjects_kb([])
    )
    await state.set_state(RegisterTeacher.subjects)
    await cb.answer()

@router.callback_query(RegisterTeacher.subjects, F.data.startswith("subj_"))
async def toggle_subject(cb: CallbackQuery, state: FSMContext):
    key = cb.data.replace("subj_", "")
    if key == "done":
        data     = await state.get_data()
        selected = data.get("selected_subjects", [])
        if not selected:
            await cb.answer("Kamida bitta fan tanlang!", show_alert=True); return
        nickname = create_user(cb.from_user.id, "teacher", selected)
        labels   = [SUBJECTS[s] for s in selected]
        await cb.message.edit_text(
            f"✅ O'qituvchi sifatida ro'yxatdan o'tdingiz!\n\n"
            f"Taxallusingiz: <b>{nickname}</b> 🎭\n"
            f"Fanlaringiz: {', '.join(labels)}\n\nYangi savollar kelganda xabar olasiz!",
            parse_mode="HTML", reply_markup=main_menu_kb("teacher")
        )
        await state.clear()
    else:
        data     = await state.get_data()
        selected = data.get("selected_subjects", [])
        selected = [s for s in selected if s != key] if key in selected else selected + [key]
        await state.update_data(selected_subjects=selected)
        await cb.message.edit_reply_markup(reply_markup=subjects_kb(selected))
    await cb.answer()

# ─── PROFILE ──────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "my_profile")
async def show_profile(cb: CallbackQuery):
    user = get_user(cb.from_user.id)
    if not user:
        await cb.answer("Avval ro'yxatdan o'ting!", show_alert=True); return
    star       = " ⭐" if user["star_badge"] else ""
    role_label = "O'quvchi 🎓" if user["role"] == "student" else "O'qituvchi 👨‍🏫"
    subj_text  = ""
    if user["role"] == "teacher" and user.get("subjects"):
        subj_text = "\n📚 Fanlar: " + ", ".join(SUBJECTS.get(s, s) for s in user["subjects"])
    rep_text = f"\n🏆 Obro': {user['reputation']} ta foydali javob" if user["role"] == "teacher" else ""
    await cb.message.edit_text(
        f"👤 <b>Profilingiz</b>\n\n"
        f"Taxallusingiz: <b>{user['nickname']}{star}</b>\n"
        f"Rolingiz: {role_label}{subj_text}{rep_text}",
        parse_mode="HTML", reply_markup=main_menu_kb(user["role"])
    )
    await cb.answer()

# ─── POST QUESTION ─────────────────────────────────────────────────────────────

@router.callback_query(F.data == "new_question")
async def new_question_start(cb: CallbackQuery, state: FSMContext):
    user = get_user(cb.from_user.id)
    if not user or user["role"] != "student":
        await cb.answer("Bu funksiya faqat o'quvchilar uchun!", show_alert=True); return
    await cb.message.edit_text("📚 Qaysi fandan savolingiz bor?", reply_markup=subject_select_kb())
    await state.set_state(PostQuestion.subject)
    await cb.answer()

@router.callback_query(PostQuestion.subject, F.data.startswith("qsubj_"))
async def question_subject(cb: CallbackQuery, state: FSMContext):
    await state.update_data(subject=cb.data.replace("qsubj_", ""))
    await cb.message.edit_text(
        "✏️ Savolingizni yozing:\n\n"
        "<i>Imkon qadar batafsil yozing — bu o'qituvchiga tezroq javob berishga yordam beradi.</i>",
        parse_mode="HTML"
    )
    await state.set_state(PostQuestion.text)
    await cb.answer()

@router.message(PostQuestion.text)
async def question_text(msg: Message, state: FSMContext):
    await state.update_data(text=msg.text, photo_file_id=None)
    await msg.answer(
        "📷 Rasm qo'shmoqchimisiz? (darslik yoki daftar sahifasi)\nRasm yuboring yoki o'tkazib yuboring:",
        reply_markup=skip_photo_kb()
    )
    await state.set_state(PostQuestion.photo)

@router.message(PostQuestion.photo, F.photo)
async def question_photo(msg: Message, state: FSMContext):
    await state.update_data(photo_file_id=msg.photo[-1].file_id)
    await show_question_preview(msg, state)

@router.callback_query(PostQuestion.photo, F.data == "skip_photo")
async def skip_photo(cb: CallbackQuery, state: FSMContext):
    await show_question_preview(cb.message, state, edit=True)
    await cb.answer()

async def show_question_preview(msg: Message, state: FSMContext, edit=False):
    data          = await state.get_data()
    subject_label = SUBJECTS.get(data["subject"], data["subject"])
    text = (
        f"📋 <b>Savol ko'rinishi:</b>\n\n"
        f"Fan: {subject_label}\n"
        f"Savol: {data['text']}\n"
        f"{'📷 Rasm: bor' if data.get('photo_file_id') else '📷 Rasm: yoq'}\n\n"
        f"Yuborasizmi?"
    )
    if edit:
        await msg.edit_text(text, parse_mode="HTML", reply_markup=confirm_question_kb())
    else:
        await msg.answer(text, parse_mode="HTML", reply_markup=confirm_question_kb())
    await state.set_state(PostQuestion.confirm)

@router.callback_query(PostQuestion.confirm, F.data == "confirm_no")
async def cancel_question(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    user = get_user(cb.from_user.id)
    await cb.message.edit_text("Bekor qilindi.", reply_markup=main_menu_kb(user["role"]))
    await cb.answer()

@router.callback_query(PostQuestion.confirm, F.data == "confirm_yes")
async def confirm_question(cb: CallbackQuery, state: FSMContext):
    data        = await state.get_data()
    user        = get_user(cb.from_user.id)
    question_id = insert_question(
        cb.from_user.id, data["subject"], data["text"],
        data.get("photo_file_id"), user["nickname"]
    )
    await state.clear()
    await cb.message.edit_text(
        "✅ Savolingiz yuborildi! O'qituvchilar javob berishini kuting.\nJavob kelganda xabar olasiz. 🔔",
        reply_markup=main_menu_kb("student")
    )
    await cb.answer()
    await post_question_to_channel(question_id)

async def post_question_to_channel(question_id: int):
    q = get_question(question_id)
    if not q:
        return
    subject_label = SUBJECTS.get(q["subject"], q["subject"])
    text = f"{subject_label}\n\n❓ <b>{q['nickname']}</b> ning savoli:\n\n{q['text']}"
    try:
        if q["photo_file_id"]:
            sent = await bot.send_photo(
                CHANNEL_ID, photo=q["photo_file_id"], caption=text,
                parse_mode="HTML", reply_markup=channel_question_kb(question_id)
            )
        else:
            sent = await bot.send_message(
                CHANNEL_ID, text, parse_mode="HTML",
                reply_markup=channel_question_kb(question_id)
            )
        update_question(question_id, {"channel_msg_id": sent.message_id})
        for teacher_id in get_teachers_for_subject(q["subject"]):
            try:
                await bot.send_message(
                    teacher_id,
                    f"🔔 Yangi savol: {subject_label}\n\n<i>{q['text'][:200]}</i>\n\nKanalda ko'ring!",
                    parse_mode="HTML"
                )
            except Exception:
                pass
    except Exception as e:
        log.error(f"Channel post error: {e}")

# ─── ANSWER FLOW ───────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("answer_"))
async def start_answer(cb: CallbackQuery, state: FSMContext):
    question_id = int(cb.data.replace("answer_", ""))
    user        = get_user(cb.from_user.id)
    if not user:
        await cb.answer("Avval ro'yxatdan o'ting!", show_alert=True); return
    if user["role"] != "teacher":
        await cb.answer("Faqat o'qituvchilar javob bera oladi!", show_alert=True); return
    q = get_question(question_id)
    if not q or q["status"] == "solved":
        await cb.answer("Bu savol allaqachon hal qilingan!", show_alert=True); return
    await state.update_data(question_id=question_id)
    subject_label = SUBJECTS.get(q["subject"], q["subject"])
    await bot.send_message(
        cb.from_user.id,
        f"📝 <b>Javob yozing:</b>\n\nFan: {subject_label}\nSavol: <i>{q['text']}</i>\n\nJavobingizni yozing:",
        parse_mode="HTML"
    )
    await state.set_state(PostAnswer.text)
    await cb.answer("Javob yozish uchun botga o'ting!")

@router.message(PostAnswer.text)
async def receive_answer(msg: Message, state: FSMContext):
    data        = await state.get_data()
    question_id = data["question_id"]
    answer_id   = insert_answer(question_id, msg.from_user.id, msg.text)
    await state.clear()
    await msg.answer("✅ Javobingiz o'quvchiga yuborildi!")
    q       = get_question(question_id)
    teacher = get_user(msg.from_user.id)
    star    = " ⭐" if teacher and teacher["star_badge"] else ""
    try:
        await bot.send_message(
            q["student_id"],
            f"📩 <b>Savolingizga javob keldi!</b>\n\n"
            f"<i>O'qituvchi{star} javobi:</i>\n\n{msg.text}\n\nBu javob siz uchun foydali bo'ldimi?",
            parse_mode="HTML", reply_markup=feedback_kb(question_id, answer_id)
        )
    except Exception as e:
        log.error(f"Answer delivery error: {e}")

# ─── FEEDBACK ─────────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("fb_"))
async def handle_feedback(cb: CallbackQuery):
    parts         = cb.data.split("_")
    feedback_type = parts[1]
    question_id   = int(parts[2])
    answer_id     = int(parts[3])
    answer        = get_answer(answer_id)
    if not answer:
        await cb.answer("Javob topilmadi!"); return

    if feedback_type == "foydali":
        update_answer(answer_id, {"feedback": "foydali"})
        update_question(question_id, {"status": "solved", "solved_at": datetime.now().isoformat()})
        update_reputation(answer["teacher_id"])
        q             = get_question(question_id)
        teacher       = get_user(answer["teacher_id"])
        star          = " ⭐" if teacher and teacher["star_badge"] else ""
        subject_label = SUBJECTS.get(q["subject"], q["subject"])
        try:
            await bot.send_message(
                CHANNEL_ID,
                f"✅ <b>Savol hal qilindi!</b>\n\n{subject_label}\n❓ {q['text']}\n\n"
                f"💡 <b>Eng yaxshi javob (O'qituvchi{star}):</b>\n{answer['text']}",
                parse_mode="HTML"
            )
        except Exception as e:
            log.error(f"Best answer post error: {e}")
        try:
            await bot.send_message(
                answer["teacher_id"],
                "🎉 O'quvchi sizning javobingizni <b>foydali</b> deb baholadi!\n+1 obro' qo'shildi. 🏆",
                parse_mode="HTML"
            )
        except Exception:
            pass
        await cb.message.edit_text("✅ Rahmat! Savol hal qilindi va eng yaxshi javob kanalda e'lon qilindi.")

    elif feedback_type == "tushunmadim":
        update_answer(answer_id, {"feedback": "tushunmadim"})
        try:
            await bot.send_message(
                answer["teacher_id"],
                "🤔 O'quvchi sizning javobingizni <b>tushunmadim</b> dedi.\nIltimos, oddiyroq tushuntiring:",
                parse_mode="HTML"
            )
        except Exception:
            pass
        await cb.message.edit_text("🤔 O'qituvchiga soddaroq tushuntirish so'rovi yuborildi. Biroz kuting!")

    elif feedback_type == "toliq":
        update_answer(answer_id, {"feedback": "toliq_emas"})
        try:
            await bot.send_message(
                answer["teacher_id"],
                "📝 O'quvchi javobingiz <b>to'liq emas</b> dedi.\nIltimos, qo'shimcha ma'lumot bering:",
                parse_mode="HTML"
            )
        except Exception:
            pass
        await cb.message.edit_text("📝 O'qituvchiga to'liq javob so'rovi yuborildi. Kutib turing!")

    elif feedback_type == "notogri":
        update_answer(answer_id, {"feedback": "notogri"})
        update_question(question_id, {"status": "open"})
        await cb.message.edit_text("Tushunildi. Savol boshqa o'qituvchilarga qayta yuborildi.")
        q             = get_question(question_id)
        subject_label = SUBJECTS.get(q["subject"], q["subject"])
        for teacher_id in get_teachers_for_subject(q["subject"]):
            if teacher_id == answer["teacher_id"]:
                continue
            try:
                await bot.send_message(
                    teacher_id,
                    f"🔁 Savol hali javobsiz: {subject_label}\n\n<i>{q['text'][:200]}</i>\n\nJavob berishga harakat qiling!",
                    parse_mode="HTML"
                )
            except Exception:
                pass

    await cb.answer()

# ─── REPORT ───────────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("report_"))
async def handle_report(cb: CallbackQuery):
    question_id = int(cb.data.replace("report_", ""))
    insert_report(cb.from_user.id, question_id)
    try:
        await bot.send_message(
            ADMIN_ID,
            f"⚠️ <b>Shikoyat!</b>\n\nSavol ID: {question_id}\nYuboruvchi: {cb.from_user.id}",
            parse_mode="HTML"
        )
    except Exception:
        pass
    await cb.answer("Shikoyat adminga yuborildi. Rahmat!", show_alert=True)

# ─── OPEN QUESTIONS (teacher) ──────────────────────────────────────────────────

@router.callback_query(F.data == "open_questions")
async def show_open_questions(cb: CallbackQuery):
    user = get_user(cb.from_user.id)
    if not user or user["role"] != "teacher":
        await cb.answer("Bu funksiya faqat o'qituvchilar uchun!", show_alert=True); return
    subjects = user.get("subjects") or []
    if not subjects:
        await cb.message.edit_text("Fanlaringiz tanlanmagan.", reply_markup=main_menu_kb("teacher"))
        return
    res = (
        supabase.table("questions")
        .select("*")
        .eq("status", "open")
        .in_("subject", subjects)
        .order("posted_at", desc=True)
        .limit(5)
        .execute()
    )
    if not res.data:
        await cb.message.edit_text(
            "📭 Hozircha sizning fanlaringizda ochiq savollar yo'q.\nYangi savollar kanalda e'lon qilinadi!",
            reply_markup=main_menu_kb("teacher")
        )
        return
    text = "📋 <b>Ochiq savollar:</b>\n\n"
    for q in res.data:
        subject_label = SUBJECTS.get(q["subject"], q["subject"])
        text += f"{subject_label} — <b>{q['nickname']}</b>\n<i>{q['text'][:100]}...</i>\n\n"
    await cb.message.edit_text(text, parse_mode="HTML", reply_markup=main_menu_kb("teacher"))
    await cb.answer()

# ─── SCHEDULED JOBS ───────────────────────────────────────────────────────────

async def daily_teacher_digest():
    res = supabase.table("users").select("*").eq("role", "teacher").execute()
    for teacher in res.data:
        subjects = teacher.get("subjects") or []
        if not subjects:
            continue
        count = (
            supabase.table("questions")
            .select("id", count="exact")
            .eq("status", "open")
            .in_("subject", subjects)
            .execute()
        ).count or 0
        if count > 0:
            try:
                await bot.send_message(
                    teacher["user_id"],
                    f"☀️ Xayrli tong!\n\nBugun sizning fanlaringizda <b>{count} ta</b> javobsiz savol bor.\n"
                    f"Ko'rish uchun /start buyrug'ini bosing.",
                    parse_mode="HTML"
                )
            except Exception:
                pass

async def nudge_unanswered_students():
    threshold = (datetime.now() - timedelta(hours=24)).isoformat()
    res = (
        supabase.table("questions")
        .select("*")
        .eq("status", "open")
        .lt("posted_at", threshold)
        .execute()
    )
    for q in res.data:
        try:
            await bot.send_message(
                q["student_id"],
                f"🔔 Savolingiz hali javobsiz.\n\n<i>{q['text'][:100]}</i>\n\nO'qituvchilar tez orada javob berishadi!",
                parse_mode="HTML"
            )
        except Exception:
            pass

async def mark_unsolved_questions():
    threshold = (datetime.now() - timedelta(hours=48)).isoformat()
    res = (
        supabase.table("questions")
        .select("*")
        .eq("status", "open")
        .lt("posted_at", threshold)
        .not_.is_("channel_msg_id", "null")
        .execute()
    )
    for q in res.data:
        try:
            await bot.edit_message_reply_markup(
                CHANNEL_ID, q["channel_msg_id"],
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔴 Javobsiz | 📝 Javob berish", callback_data=f"answer_{q['id']}")],
                    [InlineKeyboardButton(text="⚠️ Xabar berish", callback_data=f"report_{q['id']}")]
                ])
            )
        except Exception:
            pass

# ─── ADMIN ────────────────────────────────────────────────────────────────────

@router.message(Command("stats"))
async def admin_stats(msg: Message):
    if msg.from_user.id != ADMIN_ID:
        return
    students  = supabase.table("users").select("user_id", count="exact").eq("role", "student").execute().count
    teachers  = supabase.table("users").select("user_id", count="exact").eq("role", "teacher").execute().count
    questions = supabase.table("questions").select("id", count="exact").execute().count
    solved    = supabase.table("questions").select("id", count="exact").eq("status", "solved").execute().count
    answers   = supabase.table("answers").select("id", count="exact").execute().count
    await msg.answer(
        f"📊 <b>EduConnect Statistika</b>\n\n"
        f"👨‍🎓 O'quvchilar: {students}\n"
        f"👨‍🏫 O'qituvchilar: {teachers}\n"
        f"❓ Jami savollar: {questions}\n"
        f"✅ Hal qilingan: {solved}\n"
        f"💬 Jami javoblar: {answers}",
        parse_mode="HTML"
    )

# ─── MAIN ─────────────────────────────────────────────────────────────────────

async def main():
    global bot, supabase
    bot      = Bot(token=BOT_TOKEN)
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    scheduler = AsyncIOScheduler()
    scheduler.add_job(daily_teacher_digest,      "cron",     hour=8, minute=0)
    scheduler.add_job(nudge_unanswered_students, "interval", hours=6)
    scheduler.add_job(mark_unsolved_questions,   "interval", hours=12)
    scheduler.start()

    log.info("EduConnect UZ bot started with Supabase!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
