import asyncio, logging, os, re, sqlite3
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

NAME = "Brozen AI"
TOKEN = os.getenv("BOT_TOKEN", "").strip()
DB_PATH = os.getenv("DB_PATH", "brozen_ai.db")
ADMINS = {int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()}
DEFAULT_WELCOME = os.getenv("WELCOME_TEXT", "سلام {name} عزیز! 👋\n\nبه <b>Brozen AI</b> خوش اومدی 🤖✨\nاز منوی زیر پلن دلخواهت رو انتخاب کن. بعد از پرداخت، رسید توسط ادمین بررسی می‌شه.")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("brozen-ai")
if not TOKEN: raise RuntimeError("BOT_TOKEN را در Railway Variables تنظیم کن.")
if not ADMINS: raise RuntimeError("ADMIN_IDS باید شامل حداقل یک شناسه عددی تلگرام باشد.")
bot = Bot(TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

def now(): return datetime.now(timezone.utc).isoformat(timespec="seconds")
def conn():
    c = sqlite3.connect(DB_PATH, timeout=30); c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON"); return c
def init_db():
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    with conn() as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users(user_id INTEGER PRIMARY KEY, username TEXT, name TEXT, joined_at TEXT);
        CREATE TABLE IF NOT EXISTS plans(id INTEGER PRIMARY KEY AUTOINCREMENT,title TEXT NOT NULL,description TEXT DEFAULT '',price INTEGER NOT NULL,duration TEXT DEFAULT 'طبق توضیحات',enabled INTEGER DEFAULT 1,created_at TEXT);
        CREATE TABLE IF NOT EXISTS orders(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL,plan_id INTEGER,plan_title TEXT,price INTEGER,status TEXT DEFAULT 'awaiting_receipt',receipt_id TEXT,admin_id INTEGER,note TEXT,created_at TEXT,updated_at TEXT);
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL DEFAULT '');
        CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY AUTOINCREMENT,admin_id INTEGER,action TEXT,details TEXT,created_at TEXT);
        """)
        for k,v in {"card_number":os.getenv("CARD_NUMBER",""),"card_name":os.getenv("CARD_NAME",""),"support":os.getenv("SUPPORT_USERNAME","IBrOzen").lstrip("@"),"welcome":DEFAULT_WELCOME,"instructions":"مبلغ دقیق را واریز کن و عکس یا فایل تصویری رسید را همین‌جا ارسال کن.","shop":"1"}.items():
            c.execute("INSERT OR IGNORE INTO settings VALUES(?,?)",(k,v))
        if c.execute("SELECT COUNT(*) FROM plans").fetchone()[0] == 0:
            c.executemany("INSERT INTO plans(title,description,price,duration,enabled,created_at) VALUES(?,?,?,?,1,?)",[
                ("پلن شروع","پلن نمونه؛ توضیحات و قیمت را از پنل ادمین تغییر بده.",50000,"۳۰ روز",now()),
                ("پلن حرفه‌ای","پلن نمونه قابل ویرایش توسط ادمین.",150000,"۳۰ روز",now())])
def getset(k, default=""):
    with conn() as c:
        r=c.execute("SELECT value FROM settings WHERE key=?",(k,)).fetchone()
        return r["value"] if r else default
def setset(k,v):
    with conn() as c: c.execute("INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(k,v))
def audit(uid,action,details=""):
    with conn() as c: c.execute("INSERT INTO audit(admin_id,action,details,created_at) VALUES(?,?,?,?)",(uid,action,details[:500],now()))
def user_save(u):
    with conn() as c: c.execute("INSERT INTO users VALUES(?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET username=excluded.username,name=excluded.name",(u.id,u.username,u.full_name,now()))
def admin(uid): return uid in ADMINS
def cash(n): return f"{int(n):,}".replace(",", "٬")+" تومان"
def btn(text,data): return InlineKeyboardButton(text=text,callback_data=data)
def kb(rows): return InlineKeyboardMarkup(inline_keyboard=rows)
def home_kb():
    rows=[[btn("🧠 مشاهده پلن‌ها","plans")],[btn("📦 سفارش‌های من","orders"),btn("👤 حساب من","profile")],[btn("🆘 پشتیبانی","support")]]
    if admin(next(iter(ADMINS))): pass
    if getset("shop","1")!="1": rows=rows[1:]
    return kb(rows)
def admin_kb():
    return kb([[btn("🧩 مدیریت پلن‌ها","a:plans"),btn("➕ افزودن پلن","a:add")],
      [btn("🧾 رسیدهای در انتظار","a:pending")],[btn("💳 تنظیمات پرداخت","a:payment")],
      [btn("🆘 پشتیبانی","a:support"),btn("👋 پیام خوش‌آمد","a:welcome")],
      [btn("📊 آمار","a:stats"),btn("📣 پیام همگانی","a:broadcast")],
      [btn("🔛 روشن/خاموش فروشگاه","a:toggle"),btn("🏠 منوی کاربر","home")]])
def back(): return kb([[btn("🔙 بازگشت","home")]])
def plan_kb():
    with conn() as c: ps=c.execute("SELECT * FROM plans WHERE enabled=1 ORDER BY id").fetchall()
    rows=[[btn(f"✨ {p['title']} — {cash(p['price'])}",f"plan:{p['id']}")] for p in ps]
    rows.append([btn("🔙 منو","home")]); return kb(rows)
def admin_plans_kb():
    with conn() as c: ps=c.execute("SELECT * FROM plans ORDER BY id DESC").fetchall()
    rows=[[btn(("🟢 " if p["enabled"] else "⚫ ")+p["title"]+" | "+cash(p["price"]),f"a:plan:{p['id']}")] for p in ps]
    rows += [[btn("➕ افزودن پلن","a:add")],[btn("🔙 پنل","admin")]]
    return kb(rows)
def order_kb(i): return kb([[btn("✅ تأیید پرداخت",f"approve:{i}"),btn("❌ رد رسید",f"reject:{i}")]])
def order_status(s): return {"awaiting_receipt":"منتظر رسید","pending_review":"در انتظار بررسی ادمین","approved":"تأیید شده","rejected":"رد شده","cancelled":"لغو شده"}.get(s,s)
class AddPlan(StatesGroup):
    title=State(); desc=State(); price=State(); duration=State()
class EditPlan(StatesGroup): value=State()
class SettingState(StatesGroup): value=State()
class Reject(StatesGroup): note=State()
class Broadcast(StatesGroup): text=State()

@dp.message(CommandStart())
async def start(m:Message,state:FSMContext):
    await state.clear(); user_save(m.from_user)
    name=escape(m.from_user.first_name or "دوست عزیز")
    welcome=getset("welcome",DEFAULT_WELCOME).replace("{name}",name)
    await m.answer(welcome,reply_markup=home_kb())
    if admin(m.from_user.id): await m.answer("🔐 پنل مدیریت در دسترس است. /admin",reply_markup=admin_kb())
@dp.message(Command("help"))
async def help_cmd(m:Message):
    await m.answer("راهنما 🤖\n۱) پلن را انتخاب کن.\n۲) مبلغ را به کارت اعلام‌شده واریز کن.\n۳) عکس یا فایل تصویری رسید را بفرست.\n۴) پس از بررسی ادمین، وضعیت سفارش برایت ارسال می‌شود.",reply_markup=home_kb())
@dp.message(Command("cancel"))
async def cancel(m:Message,state:FSMContext):
    await state.clear(); await m.answer("عملیات لغو شد.",reply_markup=home_kb())
@dp.message(Command("admin"))
async def admin_cmd(m:Message,state:FSMContext):
    if not admin(m.from_user.id): return
    await state.clear(); await m.answer("🔐 <b>پنل مدیریت Brozen AI</b>",reply_markup=admin_kb())
@dp.callback_query(F.data=="admin")
async def admin_home(q:CallbackQuery,state:FSMContext):
    if not admin(q.from_user.id): return await q.answer("دسترسی ندارید.",show_alert=True)
    await state.clear(); await q.message.edit_text("🔐 <b>پنل مدیریت Brozen AI</b>",reply_markup=admin_kb()); await q.answer()
@dp.callback_query(F.data=="home")
async def home(q:CallbackQuery,state:FSMContext):
    await state.clear(); await q.message.edit_text("🏠 <b>Brozen AI</b> — گزینه‌ای انتخاب کن:",reply_markup=home_kb()); await q.answer()
@dp.callback_query(F.data=="plans")
async def plans(q:CallbackQuery):
    if getset("shop","1")!="1": return await q.answer("فروشگاه موقتاً غیرفعال است.",show_alert=True)
    with conn() as c: n=c.execute("SELECT COUNT(*) FROM plans WHERE enabled=1").fetchone()[0]
    await q.message.edit_text("🧠 <b>پلن‌های فعال</b>\nپلن موردنظرت را انتخاب کن:" if n else "فعلاً پلن فعالی وجود ندارد.",reply_markup=plan_kb() if n else back()); await q.answer()
@dp.callback_query(F.data.startswith("plan:"))
async def plan_detail(q:CallbackQuery):
    if getset("shop","1")!="1": return await q.answer("فروشگاه غیرفعال است.",show_alert=True)
    try: i=int(q.data.split(":")[1])
    except ValueError: return await q.answer("پلن نامعتبر است.",show_alert=True)
    with conn() as c: p=c.execute("SELECT * FROM plans WHERE id=? AND enabled=1",(i,)).fetchone()
    if not p: return await q.answer("پلن در دسترس نیست.",show_alert=True)
    text=f"✨ <b>{escape(p['title'])}</b>\n\n{escape(p['description'])}\n\n⏳ مدت: <b>{escape(p['duration'])}</b>\n💰 قیمت: <b>{cash(p['price'])}</b>"
    await q.message.edit_text(text,reply_markup=kb([[btn("🛒 خرید پلن","buy:"+str(i))],[btn("🔙 پلن‌ها","plans")]])); await q.answer()
@dp.callback_query(F.data.startswith("buy:"))
async def buy(q:CallbackQuery):
    if getset("shop","1")!="1": return await q.answer("فروشگاه غیرفعال است.",show_alert=True)
    try: i=int(q.data.split(":")[1])
    except ValueError: return await q.answer("پلن نامعتبر است.",show_alert=True)
    with conn() as c:
        p=c.execute("SELECT * FROM plans WHERE id=? AND enabled=1",(i,)).fetchone()
        if not p: return await q.answer("پلن در دسترس نیست.",show_alert=True)
        card=getset("card_number")
        if not card: return await q.answer("ادمین هنوز شماره کارت را تنظیم نکرده است.",show_alert=True)
        cur=c.execute("INSERT INTO orders(user_id,plan_id,plan_title,price,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",(q.from_user.id,i,p["title"],p["price"],"awaiting_receipt",now(),now()))
        oid=cur.lastrowid
    text=(f"🧾 <b>سفارش #{oid}</b>\nپلن: <b>{escape(p['title'])}</b>\nمبلغ: <b>{cash(p['price'])}</b>\n\n"
          f"💳 شماره کارت: <code>{escape(card)}</code>\n👤 به نام: <b>{escape(getset('card_name') or 'تنظیم نشده')}</b>\n\n"
          f"{escape(getset('instructions'))}\n\n📸 عکس یا فایل تصویری رسید را همین‌جا بفرست. پس از بررسی ادمین، نتیجه اعلام می‌شود.")
    await q.message.edit_text(text,reply_markup=kb([[btn("❌ لغو سفارش",f"cancel:{oid}")],[btn("📦 سفارش‌های من","orders")]])); await q.answer("سفارش ساخته شد.")
@dp.callback_query(F.data.startswith("cancel:"))
async def cancel_order(q:CallbackQuery):
    try: i=int(q.data.split(":")[1])
    except ValueError: return await q.answer("شماره نامعتبر است.",show_alert=True)
    with conn() as c:
        o=c.execute("SELECT * FROM orders WHERE id=? AND user_id=?",(i,q.from_user.id)).fetchone()
        if not o: return await q.answer("سفارش پیدا نشد.",show_alert=True)
        if o["status"]!="awaiting_receipt": return await q.answer("این سفارش دیگر قابل لغو نیست.",show_alert=True)
        c.execute("UPDATE orders SET status='cancelled',updated_at=? WHERE id=?",(now(),i))
    await q.message.edit_text(f"سفارش #{i} لغو شد.",reply_markup=back()); await q.answer()
async def forward_receipt(m:Message,file_id:str,kind:str):
    user_save(m.from_user)
    with conn() as c:
        o=c.execute("SELECT * FROM orders WHERE user_id=? AND status='awaiting_receipt' ORDER BY id DESC LIMIT 1",(m.from_user.id,)).fetchone()
        if not o: return await m.answer("سفارش منتظر رسید پیدا نشد. ابتدا از بخش پلن‌ها خرید را شروع کن.")
        c.execute("UPDATE orders SET status='pending_review',receipt_id=?,updated_at=? WHERE id=?",(file_id,now(),o["id"]))
    uname="@"+m.from_user.username if m.from_user.username else "ندارد"
    caption=(f"🧾 <b>رسید جدید — Brozen AI</b>\nسفارش: <b>#{o['id']}</b>\nکاربر: {escape(m.from_user.full_name)}\n"
             f"شناسه: <code>{m.from_user.id}</code>\nنام کاربری: {escape(uname)}\nپلن: <b>{escape(o['plan_title'])}</b>\nمبلغ: <b>{cash(o['price'])}</b>\n\nبررسی کن:")
    delivered=0
    for aid in ADMINS:
        try:
            if kind=="photo": await bot.send_photo(aid,file_id,caption=caption,reply_markup=order_kb(o["id"]))
            else: await bot.send_document(aid,file_id,caption=caption,reply_markup=order_kb(o["id"]))
            delivered+=1
        except TelegramAPIError as e: log.warning("receipt forward to %s failed: %s",aid,e)
    await m.answer(f"✅ رسید سفارش #{o['id']} دریافت شد. وضعیت: در انتظار بررسی ادمین.",reply_markup=home_kb())
    if not delivered: log.error("Receipt order %s could not be delivered to any admin",o["id"])
@dp.message(F.photo)
async def receipt_photo(m:Message): await forward_receipt(m,m.photo[-1].file_id,"photo")
@dp.message(F.document)
async def receipt_document(m:Message):
    if m.document and (m.document.mime_type or "").startswith("image/"): await forward_receipt(m,m.document.file_id,"document")
    else: await m.answer("رسید را به شکل عکس یا فایل تصویری ارسال کن.")
@dp.callback_query(F.data=="orders")
async def my_orders(q:CallbackQuery):
    with conn() as c: rows=c.execute("SELECT * FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 10",(q.from_user.id,)).fetchall()
    text="هنوز سفارشی نداری." if not rows else "📦 <b>سفارش‌های تو</b>\n\n"+"\n".join(f"• #{o['id']} — {escape(o['plan_title'])} — {cash(o['price'])}\n  وضعیت: {order_status(o['status'])}" for o in rows)
    await q.message.edit_text(text,reply_markup=back()); await q.answer()
@dp.callback_query(F.data=="profile")
async def profile(q:CallbackQuery):
    with conn() as c:
        total=c.execute("SELECT COUNT(*) FROM orders WHERE user_id=?",(q.from_user.id,)).fetchone()[0]
        ok=c.execute("SELECT COUNT(*) FROM orders WHERE user_id=? AND status='approved'",(q.from_user.id,)).fetchone()[0]
    await q.message.edit_text(f"👤 <b>حساب کاربری</b>\nشناسه: <code>{q.from_user.id}</code>\nنام: {escape(q.from_user.full_name)}\nتعداد سفارش: {total}\nتأییدشده: {ok}",reply_markup=back()); await q.answer()
@dp.callback_query(F.data=="support")
async def support(q:CallbackQuery):
    s=getset("support").strip().lstrip("@")
    if s: await q.message.edit_text(f"🆘 پشتیبانی: @{escape(s)}",reply_markup=kb([[InlineKeyboardButton(text="💬 پیام به پشتیبانی",url=f"https://t.me/{s}")],[btn("🔙 منو","home")]]))
    else: await q.message.edit_text("آیدی پشتیبانی تنظیم نشده است.",reply_markup=back())
    await q.answer()

# Admin handlers
@dp.callback_query(F.data=="a:plans")
async def a_plans(q:CallbackQuery):
    if not admin(q.from_user.id): return await q.answer("دسترسی ندارید.",show_alert=True)
    await q.message.edit_text("🧩 <b>مدیریت پلن‌ها</b>",reply_markup=admin_plans_kb()); await q.answer()
@dp.callback_query(F.data.startswith("a:plan:"))
async def a_plan(q:CallbackQuery):
    if not admin(q.from_user.id): return await q.answer("دسترسی ندارید.",show_alert=True)
    try: i=int(q.data.rsplit(":",1)[1])
    except ValueError: return await q.answer("شناسه نامعتبر است.",show_alert=True)
    with conn() as c: p=c.execute("SELECT * FROM plans WHERE id=?",(i,)).fetchone()
    if not p: return await q.answer("پلن پیدا نشد.",show_alert=True)
    text=f"🧩 <b>{escape(p['title'])}</b>\nشناسه: {i}\nقیمت: {cash(p['price'])}\nمدت: {escape(p['duration'])}\nوضعیت: {'فعال' if p['enabled'] else 'غیرفعال'}\n\n{escape(p['description'])}"
    await q.message.edit_text(text,reply_markup=kb([[btn("✏️ عنوان",f"edit:title:{i}"),btn("💰 قیمت",f"edit:price:{i}")],[btn("📝 توضیحات",f"edit:description:{i}"),btn("⏳ مدت",f"edit:duration:{i}")],[btn("🔛 فعال/غیرفعال",f"toggle:{i}"),btn("🗑 حذف",f"delete:{i}")],[btn("🔙 فهرست","a:plans")]])); await q.answer()
@dp.callback_query(F.data=="a:add")
async def add_start(q:CallbackQuery,state:FSMContext):
    if not admin(q.from_user.id): return await q.answer("دسترسی ندارید.",show_alert=True)
    await state.clear(); await state.set_state(AddPlan.title); await q.message.answer("عنوان پلن را بفرست. برای لغو /cancel"); await q.answer()
@dp.message(AddPlan.title)
async def add_title(m:Message,state:FSMContext):
    if not admin(m.from_user.id): return
    v=(m.text or "").strip()
    if not v or len(v)>100: return await m.answer("عنوان باید ۱ تا ۱۰۰ کاراکتر باشد.")
    await state.update_data(title=v); await state.set_state(AddPlan.desc); await m.answer("توضیحات را بفرست (برای خالی، -).")
@dp.message(AddPlan.desc)
async def add_desc(m:Message,state:FSMContext):
    if not admin(m.from_user.id): return
    v=(m.text or "").strip()
    if v=="-": v=""
    if len(v)>1500: return await m.answer("حداکثر ۱۵۰۰ کاراکتر.")
    await state.update_data(desc=v); await state.set_state(AddPlan.price); await m.answer("قیمت به تومان، فقط عدد؛ مثال 120000")
@dp.message(AddPlan.price)
async def add_price(m:Message,state:FSMContext):
    if not admin(m.from_user.id): return
    v=(m.text or "").strip().replace(",","").replace("٬","")
    if not v.isdigit() or not 1<=int(v)<=2_000_000_000: return await m.answer("قیمت نامعتبر است.")
    await state.update_data(price=int(v)); await state.set_state(AddPlan.duration); await m.answer("مدت/اعتبار پلن را بفرست؛ مثال: ۳۰ روز")
@dp.message(AddPlan.duration)
async def add_duration(m:Message,state:FSMContext):
    if not admin(m.from_user.id): return
    v=(m.text or "").strip()
    if not v or len(v)>100: return await m.answer("مدت باید ۱ تا ۱۰۰ کاراکتر باشد.")
    d=await state.get_data()
    with conn() as c: cur=c.execute("INSERT INTO plans(title,description,price,duration,enabled,created_at) VALUES(?,?,?,?,1,?)",(d["title"],d["desc"],d["price"],v,now())); i=cur.lastrowid
    audit(m.from_user.id,"add_plan",str(i)); await state.clear(); await m.answer(f"✅ پلن «{escape(d['title'])}» اضافه شد.",reply_markup=admin_kb())
@dp.callback_query(F.data.startswith("edit:"))
async def edit_start(q:CallbackQuery,state:FSMContext):
    if not admin(q.from_user.id): return await q.answer("دسترسی ندارید.",show_alert=True)
    _,field,raw=q.data.split(":")
    if field not in ("title","price","description","duration") or not raw.isdigit(): return await q.answer("درخواست نامعتبر.",show_alert=True)
    with conn() as c: p=c.execute("SELECT id,title FROM plans WHERE id=?",(int(raw),)).fetchone()
    if not p: return await q.answer("پلن پیدا نشد.",show_alert=True)
    await state.clear(); await state.set_state(EditPlan.value); await state.update_data(field=field,pid=int(raw))
    await q.message.answer(f"مقدار جدید برای {escape(p['title'])} را بفرست. برای خالی‌کردن توضیحات -؛ لغو /cancel"); await q.answer()
@dp.message(EditPlan.value)
async def edit_save(m:Message,state:FSMContext):
    if not admin(m.from_user.id): return
    d=await state.get_data(); field=d["field"]; v=(m.text or "").strip()
    if field=="price":
        v=v.replace(",","").replace("٬","")
        if not v.isdigit() or not 1<=int(v)<=2_000_000_000: return await m.answer("قیمت نامعتبر است.")
        v=int(v)
    elif field=="description":
        if v=="-": v=""
        if len(v)>1500: return await m.answer("حداکثر ۱۵۰۰ کاراکتر.")
    elif not v or len(v)>100: return await m.answer("مقدار باید ۱ تا ۱۰۰ کاراکتر باشد.")
    with conn() as c: c.execute(f"UPDATE plans SET {field}=? WHERE id=?",(v,d["pid"]))
    audit(m.from_user.id,"edit_plan",f"{d['pid']}:{field}"); await state.clear(); await m.answer("✅ پلن ویرایش شد.",reply_markup=admin_plans_kb())
@dp.callback_query(F.data.startswith("toggle:"))
async def toggle_plan(q:CallbackQuery):
    if not admin(q.from_user.id): return await q.answer("دسترسی ندارید.",show_alert=True)
    try: i=int(q.data.split(":")[1])
    except ValueError: return await q.answer("شناسه نامعتبر.",show_alert=True)
    with conn() as c:
        p=c.execute("SELECT enabled FROM plans WHERE id=?",(i,)).fetchone()
        if not p: return await q.answer("پلن پیدا نشد.",show_alert=True)
        c.execute("UPDATE plans SET enabled=? WHERE id=?",(0 if p["enabled"] else 1,i))
    await q.answer("وضعیت تغییر کرد."); await q.message.edit_text("وضعیت پلن تغییر کرد.",reply_markup=admin_plans_kb())
@dp.callback_query(F.data.startswith("delete:"))
async def delete_plan(q:CallbackQuery):
    if not admin(q.from_user.id): return await q.answer("دسترسی ندارید.",show_alert=True)
    try: i=int(q.data.split(":")[1])
    except ValueError: return await q.answer("شناسه نامعتبر.",show_alert=True)
    with conn() as c: c.execute("DELETE FROM plans WHERE id=?",(i,))
    audit(q.from_user.id,"delete_plan",str(i)); await q.answer("پلن حذف شد."); await q.message.edit_text("🗑 پلن حذف شد.",reply_markup=admin_plans_kb())
@dp.callback_query(F.data=="a:payment")
async def payment_menu(q:CallbackQuery):
    if not admin(q.from_user.id): return await q.answer("دسترسی ندارید.",show_alert=True)
    text=f"💳 <b>تنظیمات پرداخت</b>\nشماره کارت: <code>{escape(getset('card_number') or 'تنظیم نشده')}</code>\nنام: <b>{escape(getset('card_name') or 'تنظیم نشده')}</b>\nراهنما: {escape(getset('instructions'))}"
    await q.message.edit_text(text,reply_markup=kb([[btn("تغییر شماره کارت","set:card_number"),btn("تغییر نام کارت","set:card_name")],[btn("تغییر راهنما","set:instructions")],[btn("🔙 پنل","admin")]])); await q.answer()
@dp.callback_query(F.data.startswith("set:"))
async def setting_start(q:CallbackQuery,state:FSMContext):
    if not admin(q.from_user.id): return await q.answer("دسترسی ندارید.",show_alert=True)
    field=q.data.split(":")[1]
    if field not in ("card_number","card_name","instructions"): return await q.answer("گزینه نامعتبر.",show_alert=True)
    await state.clear(); await state.set_state(SettingState.value); await state.update_data(field=field)
    await q.message.answer({"card_number":"شماره کارت را بفرست.","card_name":"نام صاحب کارت را بفرست.","instructions":"راهنمای پرداخت را بفرست."}[field]); await q.answer()
@dp.message(SettingState.value)
async def setting_save(m:Message,state:FSMContext):
    if not admin(m.from_user.id): return
    d=await state.get_data(); v=(m.text or "").strip(); field=d["field"]
    if not v or len(v)>1000: return await m.answer("مقدار نامعتبر است.")
    if field=="card_number" and len(re.sub(r"\\s","",v))<12: return await m.answer("شماره کارت خیلی کوتاه است؛ دوباره بررسی کن.")
    if field=="support":
        v=v.lstrip("@")
        if v=="-": v=""
        if v and not re.fullmatch(r"[A-Za-z0-9_]{5,32}",v): return await m.answer("آیدی نامعتبر است؛ فقط حروف انگلیسی، عدد و _.")
    if field=="welcome" and len(v)>3000: return await m.answer("پیام خوش‌آمد حداکثر ۳۰۰۰ کاراکتر باشد.")
    setset(field,v); audit(m.from_user.id,"setting",field); await state.clear(); await m.answer("✅ تنظیم ذخیره شد.",reply_markup=admin_kb())
@dp.callback_query(F.data=="a:support")
async def support_admin(q:CallbackQuery):
    if not admin(q.from_user.id): return await q.answer("دسترسی ندارید.",show_alert=True)
    await q.message.edit_text(f"آیدی پشتیبانی فعلی: @{escape(getset('support')) or 'تنظیم نشده'}",reply_markup=kb([[btn("✏️ تغییر آیدی","set:support")],[btn("🔙 پنل","admin")]])); await q.answer()
@dp.callback_query(F.data=="set:support")
async def support_start(q:CallbackQuery,state:FSMContext):
    if not admin(q.from_user.id): return await q.answer("دسترسی ندارید.",show_alert=True)
    await state.clear(); await state.set_state(SettingState.value); await state.update_data(field="support"); await q.message.answer("آیدی پشتیبانی را بدون @ بفرست؛ برای حذف -"); await q.answer()
@dp.callback_query(F.data=="a:welcome")
async def welcome_start(q:CallbackQuery,state:FSMContext):
    if not admin(q.from_user.id): return await q.answer("دسترسی ندارید.",show_alert=True)
    await state.clear(); await state.set_state(SettingState.value); await state.update_data(field="welcome"); await q.message.answer("متن خوش‌آمدگویی را بفرست. برای نام کاربر از {name} استفاده کن."); await q.answer()

@dp.callback_query(F.data=="a:pending")
async def pending(q:CallbackQuery):
    if not admin(q.from_user.id): return await q.answer("دسترسی ندارید.",show_alert=True)
    with conn() as c: rows=c.execute("SELECT * FROM orders WHERE status='pending_review' ORDER BY id LIMIT 25").fetchall()
    if not rows: text="🎉 سفارش در انتظار بررسی وجود ندارد."; markup=admin_kb()
    else:
        text="🧾 <b>سفارش‌های منتظر بررسی</b>"
        markup=kb([[btn(f"#{o['id']} | {o['plan_title']} | {cash(o['price'])}",f"a:order:{o['id']}")] for o in rows]+[[btn("🔙 پنل","admin")]])
    await q.message.edit_text(text,reply_markup=markup); await q.answer()
@dp.callback_query(F.data.startswith("a:order:"))
async def order_detail(q:CallbackQuery):
    if not admin(q.from_user.id): return await q.answer("دسترسی ندارید.",show_alert=True)
    try: i=int(q.data.rsplit(":",1)[1])
    except ValueError: return await q.answer("شناسه نامعتبر.",show_alert=True)
    with conn() as c:
        o=c.execute("SELECT * FROM orders WHERE id=?",(i,)).fetchone()
        u=c.execute("SELECT * FROM users WHERE user_id=?",(o["user_id"],)).fetchone() if o else None
    if not o: return await q.answer("سفارش پیدا نشد.",show_alert=True)
    text=f"🧾 <b>سفارش #{i}</b>\nکاربر: {escape(u['name'] if u else 'نامشخص')}\nشناسه: <code>{o['user_id']}</code>\nپلن: <b>{escape(o['plan_title'])}</b>\nمبلغ: <b>{cash(o['price'])}</b>\nوضعیت: {order_status(o['status'])}\nتاریخ: {escape(o['created_at'])}"
    markup=order_kb(i) if o["status"]=="pending_review" else back()
    await q.message.edit_text(text,reply_markup=markup)
    if o["receipt_id"]:
        try: await bot.send_document(q.from_user.id,o["receipt_id"],caption=f"رسید سفارش #{i}")
        except TelegramAPIError:
            try: await bot.send_photo(q.from_user.id,o["receipt_id"],caption=f"رسید سفارش #{i}")
            except TelegramAPIError: pass
    await q.answer()
@dp.callback_query(F.data.startswith("approve:"))
async def approve(q:CallbackQuery):
    if not admin(q.from_user.id): return await q.answer("دسترسی ندارید.",show_alert=True)
    try: i=int(q.data.split(":")[1])
    except ValueError: return await q.answer("شناسه نامعتبر.",show_alert=True)
    with conn() as c:
        o=c.execute("SELECT * FROM orders WHERE id=?",(i,)).fetchone()
        if not o: return await q.answer("سفارش پیدا نشد.",show_alert=True)
        if o["status"]!="pending_review": return await q.answer("این سفارش قبلاً بررسی شده.",show_alert=True)
        c.execute("UPDATE orders SET status='approved',admin_id=?,updated_at=? WHERE id=?",(q.from_user.id,now(),i))
    audit(q.from_user.id,"approve",str(i))
    try: await bot.send_message(o["user_id"],f"✅ پرداخت سفارش <b>#{i}</b> تأیید شد.\nپلن: <b>{escape(o['plan_title'])}</b>\nبرای فعال‌سازی خدمت در صورت نیاز با پشتیبانی هماهنگ کن.")
    except TelegramAPIError as e: log.warning("User notify failed: %s",e)
    try:
        if q.message.photo or q.message.document: await q.message.edit_caption((q.message.caption or f"سفارش #{i}")+"\n\n✅ <b>تأیید شد</b>",reply_markup=None)
        else: await q.message.edit_text((q.message.text or f"سفارش #{i}")+"\n\n✅ <b>تأیید شد</b>",reply_markup=None)
    except TelegramAPIError: pass
    await q.answer("پرداخت تأیید شد.")
@dp.callback_query(F.data.startswith("reject:"))
async def reject_start(q:CallbackQuery,state:FSMContext):
    if not admin(q.from_user.id): return await q.answer("دسترسی ندارید.",show_alert=True)
    try: i=int(q.data.split(":")[1])
    except ValueError: return await q.answer("شناسه نامعتبر.",show_alert=True)
    with conn() as c: o=c.execute("SELECT * FROM orders WHERE id=?",(i,)).fetchone()
    if not o or o["status"]!="pending_review": return await q.answer("سفارش در انتظار بررسی نیست.",show_alert=True)
    await state.clear(); await state.set_state(Reject.note); await state.update_data(oid=i)
    await q.message.answer(f"دلیل رد رسید سفارش #{i} را بفرست؛ برای بدون توضیح -"); await q.answer()
@dp.message(Reject.note)
async def reject_finish(m:Message,state:FSMContext):
    if not admin(m.from_user.id): return
    d=await state.get_data(); note=(m.text or "").strip()
    if note=="-": note=""
    if len(note)>800: return await m.answer("حداکثر ۸۰۰ کاراکتر.")
    with conn() as c:
        o=c.execute("SELECT * FROM orders WHERE id=?",(d["oid"],)).fetchone()
        if not o or o["status"]!="pending_review":
            await state.clear(); return await m.answer("این سفارش دیگر در انتظار بررسی نیست.")
        c.execute("UPDATE orders SET status='rejected',admin_id=?,note=?,updated_at=? WHERE id=?",(m.from_user.id,note,now(),d["oid"]))
    audit(m.from_user.id,"reject",f"{d['oid']}:{note}")
    try: await bot.send_message(o["user_id"],f"❌ رسید سفارش <b>#{d['oid']}</b> تأیید نشد."+(f"\nدلیل: {escape(note)}" if note else "")+"\nدر صورت نیاز با پشتیبانی تماس بگیر.")
    except TelegramAPIError: pass
    await state.clear(); await m.answer(f"رد رسید سفارش #{d['oid']} ثبت شد.",reply_markup=admin_kb())
@dp.callback_query(F.data=="a:stats")
async def stats(q:CallbackQuery):
    if not admin(q.from_user.id): return await q.answer("دسترسی ندارید.",show_alert=True)
    with conn() as c:
        users=c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        plans=c.execute("SELECT COUNT(*) FROM plans").fetchone()[0]
        orders=c.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
        pending=c.execute("SELECT COUNT(*) FROM orders WHERE status='pending_review'").fetchone()[0]
        approved=c.execute("SELECT COUNT(*) FROM orders WHERE status='approved'").fetchone()[0]
        revenue=c.execute("SELECT COALESCE(SUM(price),0) FROM orders WHERE status='approved'").fetchone()[0]
    await q.message.edit_text(f"📊 <b>آمار ربات</b>\nکاربران: {users}\nپلن‌ها: {plans}\nکل سفارش‌ها: {orders}\nدر انتظار: {pending}\nتأییدشده: {approved}\nمجموع پرداخت تأییدشده: {cash(revenue)}",reply_markup=back()); await q.answer()
@dp.callback_query(F.data=="a:toggle")
async def toggle_shop(q:CallbackQuery):
    if not admin(q.from_user.id): return await q.answer("دسترسی ندارید.",show_alert=True)
    v="0" if getset("shop","1")=="1" else "1"; setset("shop",v); audit(q.from_user.id,"shop",v)
    await q.answer("فروشگاه روشن شد." if v=="1" else "فروشگاه خاموش شد.",show_alert=True)
    await q.message.edit_text("🔐 <b>پنل مدیریت Brozen AI</b>",reply_markup=admin_kb())
@dp.callback_query(F.data=="a:broadcast")
async def broadcast_start(q:CallbackQuery,state:FSMContext):
    if not admin(q.from_user.id): return await q.answer("دسترسی ندارید.",show_alert=True)
    await state.clear(); await state.set_state(Broadcast.text); await q.message.answer("متن پیام همگانی را بفرست. فقط به کاربران ثبت‌شده ارسال می‌شود."); await q.answer()
@dp.message(Broadcast.text)
async def broadcast_finish(m:Message,state:FSMContext):
    if not admin(m.from_user.id): return
    text=(m.text or "").strip()
    if not text or len(text)>3500: return await m.answer("متن باید بین ۱ تا ۳۵۰۰ کاراکتر باشد.")
    with conn() as c: ids=[r["user_id"] for r in c.execute("SELECT user_id FROM users").fetchall()]
    await m.answer("📣 ارسال پیام شروع شد...")
    ok=fail=0
    for uid in ids:
        try: await bot.send_message(uid,text); ok+=1
        except TelegramAPIError: fail+=1
        await asyncio.sleep(.04)
    audit(m.from_user.id,"broadcast",f"ok={ok}; fail={fail}")
    await state.clear(); await m.answer(f"تمام شد.\nموفق: {ok}\nناموفق: {fail}",reply_markup=admin_kb())
@dp.message()
async def unknown(m:Message):
    if m.text and m.text.startswith("/"): await m.answer("این دستور شناخته‌شده نیست. /start را بزن.")
    else: await m.answer("از دکمه‌های منو استفاده کن. برای سفارش فعال، رسید را به شکل عکس یا فایل تصویری ارسال کن.")
async def main():
    init_db()
    log.info("Starting %s; admin count=%d; database=%s",NAME,len(ADMINS),DB_PATH)
    try:
        await bot.delete_webhook(drop_pending_updates=False)
        await dp.start_polling(bot,allowed_updates=dp.resolve_used_update_types())
    finally: await bot.session.close()
if __name__=="__main__":
    try: asyncio.run(main())
    except (KeyboardInterrupt,SystemExit): log.info("Stopped.")
