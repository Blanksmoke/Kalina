import os
import logging
import json
import asyncio
from typing import Dict, List, Optional, Set
from dataclasses import dataclass, asdict
from datetime import datetime
from contextlib import asynccontextmanager

# Для веб-сервера
from starlette.applications import Starlette
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route
import uvicorn

# Библиотеки бота
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import Update

# ======================= НАСТРОЙКИ =======================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8033687629:AAGjFBEHEG9qnfSSx2yYfYCnNQrk-N2rKRg")
YOUR_TELEGRAM_ID = int(os.environ.get("OWNER_ID", "8104914597"))
OPERATOR_LINK = os.environ.get("OPERATOR_LINK", "https://t.me/operator_bot")
RENDER_EXTERNAL_URL = os.environ.get("RENDER_EXTERNAL_URL", "https://your-service.onrender.com")
PORT = int(os.environ.get("PORT", 8000))

# ======================= ЛОГИРОВАНИЕ =======================
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ======================= ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ =======================
bot = None
dp = None
storage_db = None
admin_manager = None

# ======================= КЛАССЫ ДАННЫХ =======================
@dataclass
class Product:
    id: int
    name: str
    description: str
    price: float
    category_id: Optional[int] = None
    city_id: Optional[int] = None
    photo_url: Optional[str] = None
    is_active: bool = True

@dataclass
class Category:
    id: int
    name: str
    city_id: int
    is_active: bool = True

@dataclass
class City:
    id: int
    name: str
    order: int = 999
    is_active: bool = True

@dataclass
class Order:
    id: int
    user_id: int
    username: str
    product_id: int
    product_name: str
    price: float
    payment_method: str
    payment_proof: str
    status: str = "pending"
    timestamp: str = ""

@dataclass
class PaymentDetails:
    card_number: str = "2200 1234 5678 9012"
    card_holder: str = "Иван Иванов"
    crypto_wallet: str = "TXYZ1234567890abcdef"
    crypto_network: str = "TRC20 (TRON)"
    crypto_coin: str = "USDT"

@dataclass
class OperatorSettings:
    operator_link: str = "https://t.me/operator_bot"
    operator_enabled: bool = False
    operator_button_text: str = "👨‍💼 Связаться с оператором"

# ======================= АДМИН МЕНЕДЖЕР =======================
class AdminManager:
    def __init__(self, filename: str = 'admins.json'):
        self.filename = filename
        self.admins: Set[int] = self.load_admins()
        
        if YOUR_TELEGRAM_ID and YOUR_TELEGRAM_ID not in self.admins:
            self.admins.add(YOUR_TELEGRAM_ID)
            self.save_admins()
            print(f"✅ Владелец добавлен как администратор: {YOUR_TELEGRAM_ID}")
    
    def load_admins(self) -> Set[int]:
        try:
            if os.path.exists(self.filename):
                with open(self.filename, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        return set(data)
                    elif isinstance(data, dict):
                        admins_list = data.get('admins', [])
                        if isinstance(admins_list, list):
                            return set(admins_list)
            return set()
        except Exception as e:
            print(f"⚠️ Ошибка при загрузке админов: {e}")
            self.save_admins()
            return set()
    
    def save_admins(self):
        try:
            data = {'admins': list(self.admins)}
            with open(self.filename, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"⚠️ Ошибка при сохранении админов: {e}")
    
    def is_admin(self, user_id: int) -> bool:
        return user_id in self.admins
    
    def add_admin(self, user_id: int, requester_id: int) -> bool:
        if not self.is_admin(requester_id):
            return False
        self.admins.add(user_id)
        self.save_admins()
        return True
    
    def remove_admin(self, user_id: int, requester_id: int) -> bool:
        if not self.is_admin(requester_id):
            return False
        if user_id == requester_id:
            return False
        if user_id in self.admins:
            self.admins.remove(user_id)
            self.save_admins()
            return True
        return False
    
    def get_admins_list(self) -> List[int]:
        return list(self.admins)

# ======================= ХРАНИЛИЩЕ ДАННЫХ =======================
class DataStorage:
    def __init__(self):
        self.products_file = 'products.json'
        self.categories_file = 'categories.json'
        self.cities_file = 'cities.json'
        self.orders_file = 'orders.json'
        self.users_file = 'users.json'
        self.payment_file = 'payment.json'
        self.operator_file = 'operator.json'
        
        self.ensure_files_exist()
        
        self.products = self.load_data(self.products_file, Product)
        self.categories = self.load_data(self.categories_file, Category)
        self.cities = self.load_data(self.cities_file, City)
        self.orders = self.load_data(self.orders_file, Order)
        self.users = self.load_users()
        self.payment_details = self.load_payment_details()
        self.operator_settings = self.load_operator_settings()
        
        self.next_product_id = max([p.id for p in self.products.values()] + [0]) + 1
        self.next_category_id = max([c.id for c in self.categories.values()] + [0]) + 1
        self.next_city_id = max([c.id for c in self.cities.values()] + [0]) + 1
        self.next_order_id = max([o.id for o in self.orders.values()] + [0]) + 1
        
        if not self.cities:
            self.create_test_data()
    
    def ensure_files_exist(self):
        files = [
            self.products_file, self.categories_file, self.cities_file,
            self.orders_file, self.users_file, self.payment_file, self.operator_file
        ]
        
        for file in files:
            if not os.path.exists(file):
                if file == self.users_file:
                    with open(file, 'w', encoding='utf-8') as f:
                        json.dump([], f, ensure_ascii=False, indent=2)
                elif file == self.payment_file:
                    with open(file, 'w', encoding='utf-8') as f:
                        payment = PaymentDetails()
                        json.dump(asdict(payment), f, ensure_ascii=False, indent=2)
                elif file == self.operator_file:
                    with open(file, 'w', encoding='utf-8') as f:
                        operator = OperatorSettings()
                        json.dump(asdict(operator), f, ensure_ascii=False, indent=2)
                else:
                    with open(file, 'w', encoding='utf-8') as f:
                        json.dump({}, f, ensure_ascii=False, indent=2)
                print(f"📁 Создан файл: {file}")
    
    def load_data(self, filename: str, data_class):
        try:
            with open(filename, 'r', encoding='utf-8') as f:
                data = json.load(f)
                result = {}
                for key, value in data.items():
                    result[int(key)] = data_class(**value)
                return result
        except (FileNotFoundError, json.JSONDecodeError) as e:
            print(f"⚠️ Ошибка при загрузке {filename}: {e}")
            return {}
    
    def load_payment_details(self):
        try:
            with open(self.payment_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return PaymentDetails(**data)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            print(f"⚠️ Ошибка при загрузке payment.json: {e}")
            return PaymentDetails()
    
    def load_operator_settings(self):
        try:
            with open(self.operator_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return OperatorSettings(**data)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            print(f"⚠️ Ошибка при загрузке operator.json: {e}")
            return OperatorSettings()
    
    def load_users(self):
        try:
            with open(self.users_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            print(f"⚠️ Ошибка при загрузке users.json: {e}")
            return []
    
    def save_data(self, filename: str, data):
        try:
            serializable = {key: asdict(value) for key, value in data.items()}
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(serializable, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"⚠️ Ошибка при сохранении {filename}: {e}")
    
    def save_payment_details(self):
        try:
            with open(self.payment_file, 'w', encoding='utf-8') as f:
                json.dump(asdict(self.payment_details), f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"⚠️ Ошибка при сохранении payment.json: {e}")
    
    def save_operator_settings(self):
        try:
            with open(self.operator_file, 'w', encoding='utf-8') as f:
                json.dump(asdict(self.operator_settings), f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"⚠️ Ошибка при сохранении operator.json: {e}")
    
    def save_users(self):
        try:
            with open(self.users_file, 'w', encoding='utf-8') as f:
                json.dump(self.users, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"⚠️ Ошибка при сохранении users.json: {e}")
    
    def save_all(self):
        self.save_data(self.products_file, self.products)
        self.save_data(self.categories_file, self.categories)
        self.save_data(self.cities_file, self.cities)
        self.save_data(self.orders_file, self.orders)
        self.save_users()
        self.save_payment_details()
        self.save_operator_settings()
    
    def add_user(self, user_id: int, username: str = ""):
        try:
            user_data = {'id': user_id, 'username': username, 'date': datetime.now().isoformat()}
            for user in self.users:
                if user['id'] == user_id:
                    return
            self.users.append(user_data)
            self.save_users()
        except Exception as e:
            print(f"⚠️ Ошибка при добавлении пользователя: {e}")
    
    def add_product(self, product: Product) -> int:
        product.id = self.next_product_id
        self.products[product.id] = product
        self.next_product_id += 1
        self.save_data(self.products_file, self.products)
        return product.id
    
    def add_category(self, category: Category) -> int:
        category.id = self.next_category_id
        self.categories[category.id] = category
        self.next_category_id += 1
        self.save_data(self.categories_file, self.categories)
        return category.id
    
    def add_city(self, city: City) -> int:
        city.id = self.next_city_id
        self.cities[city.id] = city
        self.next_city_id += 1
        self.save_data(self.cities_file, self.cities)
        return city.id
    
    def add_order(self, order: Order) -> int:
        order.id = self.next_order_id
        self.orders[order.id] = order
        self.next_order_id += 1
        self.save_data(self.orders_file, self.orders)
        return order.id
    
    def update_payment_details(self, **kwargs):
        try:
            for key, value in kwargs.items():
                if hasattr(self.payment_details, key):
                    setattr(self.payment_details, key, value)
            self.save_payment_details()
        except Exception as e:
            print(f"⚠️ Ошибка при обновлении реквизитов: {e}")
    
    def update_operator_settings(self, **kwargs):
        try:
            for key, value in kwargs.items():
                if hasattr(self.operator_settings, key):
                    setattr(self.operator_settings, key, value)
            self.save_operator_settings()
        except Exception as e:
            print(f"⚠️ Ошибка при обновлении настроек оператора: {e}")
    
    def update_city_order(self, city_id: int, order: int):
        if city_id in self.cities:
            self.cities[city_id].order = order
            self.save_data(self.cities_file, self.cities)
            return True
        return False
    
    def bulk_update_city_orders(self, order_mapping: Dict[int, int]):
        try:
            for city_id, order in order_mapping.items():
                if city_id in self.cities:
                    self.cities[city_id].order = order
            self.save_data(self.cities_file, self.cities)
            return True
        except Exception as e:
            print(f"⚠️ Ошибка при обновлении порядка городов: {e}")
            return False
    
    def delete_city(self, city_id: int) -> bool:
        if city_id in self.cities:
            del self.cities[city_id]
            self.save_data(self.cities_file, self.cities)
            categories_to_delete = [cat_id for cat_id, cat in self.categories.items() if cat.city_id == city_id]
            for cat_id in categories_to_delete:
                self.delete_category(cat_id)
            return True
        return False
    
    def delete_category(self, category_id: int) -> bool:
        if category_id in self.categories:
            del self.categories[category_id]
            self.save_data(self.categories_file, self.categories)
            products_to_delete = [prod_id for prod_id, prod in self.products.items() if prod.category_id == category_id]
            for prod_id in products_to_delete:
                self.delete_product(prod_id)
            return True
        return False
    
    def delete_product(self, product_id: int) -> bool:
        if product_id in self.products:
            del self.products[product_id]
            self.save_data(self.products_file, self.products)
            return True
        return False
    
    def create_test_data(self):
        print("📝 Создаю тестовые данные...")
        
        try:
            city_order = {
                "🌍Калининград🌍": 1, "Гурьевск": 2, "Зеленоградск": 3, "Гвардейск": 4,
                "Советск": 5, "Полесск": 6, "Балтийск": 7, "Светлогорск": 8,
                "Гусев": 9, "Черняховск": 10, "Светлый": 11, "Пионерский": 12,
                "Багратионовск": 13, "Янтарный": 14, "Мамоново": 15, "Неман": 16,
                "Краснознаменск": 17, "Нестеров": 18, "Знаменск": 19, "Правдинск": 20, "Приморск": 21
            }
            
            current_id = 1
            for city_name, order in city_order.items():
                city = City(id=current_id, name=city_name, order=order, is_active=True)
                self.cities[current_id] = city
                current_id += 1
            
            category1 = Category(id=1, name="Электроника", city_id=1, is_active=True)
            category2 = Category(id=2, name="Одежда", city_id=1, is_active=True)
            self.categories[1] = category1
            self.categories[2] = category2
            
            product1 = Product(
                id=1, name="iPhone 15 Pro",
                description="Новый iPhone с улучшенной камерой",
                price=99999.99, category_id=1, is_active=True
            )
            product2 = Product(
                id=2, name="Футболка Premium",
                description="Качественная хлопковая футболка",
                price=2999.99, category_id=2, is_active=True
            )
            self.products[1] = product1
            self.products[2] = product2
            
            self.save_all()
            print("✅ Тестовые данные созданы")
        except Exception as e:
            print(f"❌ Ошибка при создании тестовых данных: {e}")
    
    def get_city_products(self, city_id: int) -> List[Product]:
        return [p for p in self.products.values() if p.city_id == city_id and p.category_id is None and p.is_active]

# ======================= СОСТОЯНИЯ FSM =======================
class UserState(StatesGroup):
    waiting_for_payment_proof = State()
    waiting_for_city_name = State()
    waiting_for_category_name = State()
    waiting_for_product_name = State()
    waiting_for_product_description = State()
    waiting_for_product_price = State()
    waiting_for_product_category = State()
    waiting_for_product_photo = State()
    waiting_for_broadcast_message = State()
    waiting_for_new_admin_id = State()
    waiting_for_remove_admin_id = State()
    waiting_for_delete_city = State()
    waiting_for_delete_category = State()
    waiting_for_delete_product = State()
    waiting_for_card_number = State()
    waiting_for_card_holder = State()
    waiting_for_crypto_wallet = State()
    waiting_for_crypto_network = State()
    waiting_for_crypto_coin = State()
    waiting_for_multiple_products_category = State()
    waiting_for_multiple_products_data = State()
    waiting_for_multiple_products_descriptions = State()
    waiting_for_multiple_products_prices = State()
    waiting_for_multiple_products_photos = State()
    waiting_for_operator_link = State()
    waiting_for_operator_button_text = State()
    waiting_for_city_order = State()
    waiting_for_city_for_direct_product = State()
    waiting_for_product_name_direct = State()
    waiting_for_product_description_direct = State()
    waiting_for_product_price_direct = State()
    waiting_for_product_photo_direct = State()

# ======================= ФУНКЦИИ КЛАВИАТУР =======================
def get_admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить город", callback_data="admin_add_city"),
         InlineKeyboardButton(text="🗑️ Удалить город", callback_data="admin_delete_city")],
        [InlineKeyboardButton(text="🔄 Порядок городов", callback_data="admin_city_order")],
        [InlineKeyboardButton(text="➕ Добавить категорию", callback_data="admin_add_category"),
         InlineKeyboardButton(text="🗑️ Удалить категорию", callback_data="admin_delete_category")],
        [InlineKeyboardButton(text="➕ Добавить товар", callback_data="admin_add_product"),
         InlineKeyboardButton(text="➕📦 Много товаров", callback_data="admin_add_multiple_products")],
        [InlineKeyboardButton(text="➕📍 Товар в город", callback_data="admin_add_product_to_city")],
        [InlineKeyboardButton(text="🗑️ Удалить товар", callback_data="admin_delete_product")],
        [InlineKeyboardButton(text="💳 Настройка оплаты", callback_data="admin_payment_settings"),
         InlineKeyboardButton(text="👨‍💼 Настройка оператора", callback_data="admin_operator_settings")],
        [InlineKeyboardButton(text="👑 Управление админами", callback_data="admin_manage_admins")],
        [InlineKeyboardButton(text="📢 Рассылка", callback_data="admin_broadcast")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")]
    ])

def get_back_admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад в админ-панель", callback_data="admin_back")]
    ])

def get_payment_settings_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Настроить карту", callback_data="admin_set_card")],
        [InlineKeyboardButton(text="₿ Настроить крипту", callback_data="admin_set_crypto")],
        [InlineKeyboardButton(text="📋 Показать реквизиты", callback_data="admin_show_payment")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_back")]
    ])

def get_operator_settings_keyboard() -> InlineKeyboardMarkup:
    settings = storage_db.operator_settings
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{'✅' if settings.operator_enabled else '❌'} Включить/выключить оператора", 
                            callback_data="admin_toggle_operator")],
        [InlineKeyboardButton(text="🔗 Изменить ссылку", callback_data="admin_set_operator_link")],
        [InlineKeyboardButton(text="📝 Изменить текст кнопки", callback_data="admin_set_operator_text")],
        [InlineKeyboardButton(text="📋 Показать настройки", callback_data="admin_show_operator")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_back")]
    ])

def get_cities_keyboard_two_columns() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    
    cities = storage_db.cities
    active_cities = [(city_id, city) for city_id, city in cities.items() if city.is_active]
    active_cities.sort(key=lambda x: (x[1].order, x[1].name))
    
    for city_id, city in active_cities:
        builder.add(InlineKeyboardButton(text=city.name, callback_data=f"city_{city_id}"))
    
    settings = storage_db.operator_settings
    if settings.operator_enabled:
        builder.add(InlineKeyboardButton(text=settings.operator_button_text, url=settings.operator_link))
        builder.adjust(2, 1)
    else:
        builder.adjust(2)
    
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="main_menu"))
    
    return builder.as_markup()

# ======================= ФУНКЦИЯ ДЛЯ РЕГИСТРАЦИИ ХЭНДЛЕРОВ =======================
def register_handlers(dp: Dispatcher):
    """Регистрация всех обработчиков"""
    
    # ======================= ОБРАБОТЧИКИ КОМАНД =======================
    @dp.message(CommandStart())
    async def cmd_start(message: Message):
        storage_db.add_user(message.from_user.id, message.from_user.username or "")
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🏪 Главное меню", callback_data="main_menu")]
        ])
        await message.answer(
            "👋 Добро пожаловать в наш магазин!\n"
            "Нажмите кнопку ниже, чтобы начать покупки:",
            reply_markup=keyboard
        )

    @dp.message(Command("id"))
    async def cmd_id(message: Message):
        await message.answer(
            f"🆔 Ваш ID: `{message.from_user.id}`\n\n"
            "Отправьте этот ID администратору для добавления в админы.",
            parse_mode="Markdown"
        )

    @dp.message(Command("help"))
    async def cmd_help(message: Message):
        help_text = (
            "📖 <b>Помощь по боту:</b>\n\n"
            "<b>Основные команды:</b>\n"
            "/start - Начать работу с ботом\n"
            "/id - Узнать свой ID\n"
            "/help - Показать это сообщение\n\n"
            "<b>Для администраторов:</b>\n"
            "/admin - Панель управления\n\n"
            "<b>Навигация:</b>\n"
            "1. Нажмите 'Главное меню'\n"
            "2. Выберите город\n"
            "3. Выберите категорию или товар\n"
            "4. Выберите товар\n"
            "5. Оплатите и отправьте чек\n"
        )
        await message.answer(help_text, parse_mode="HTML")

    # ======================= ОСНОВНАЯ НАВИГАЦИЯ =======================
    @dp.callback_query(F.data == "main_menu")
    async def main_menu(callback: CallbackQuery):
        try:
            cities = storage_db.cities
            active_cities = {k: v for k, v in cities.items() if v.is_active}
            
            if not active_cities:
                await callback.message.edit_text("⚠️ Города пока не добавлены")
                return
            
            keyboard = get_cities_keyboard_two_columns()
            await callback.message.edit_text("📍 Выберите город:", reply_markup=keyboard)
        except Exception as e:
            logger.error(f"Ошибка в main_menu: {e}")
            await callback.message.edit_text("⚠️ Произошла ошибка при загрузке городов")

    @dp.callback_query(F.data.startswith("city_"))
    async def show_categories_and_products(callback: CallbackQuery):
        try:
            city_id = int(callback.data.split("_")[1])
            city = storage_db.cities.get(city_id)
            
            if not city or not city.is_active:
                await callback.message.edit_text("⚠️ Город не найден")
                return
            
            active_categories = {k: v for k, v in storage_db.categories.items() if v.is_active}
            city_categories = [cat for cat in active_categories.values() if cat.city_id == city_id]
            city_products = storage_db.get_city_products(city_id)
            
            if not city_categories and not city_products:
                await callback.message.edit_text(f"⚠️ В городе {city.name} пока нет категорий и товаров")
                return
            
            keyboard_buttons = []
            
            for category in city_categories:
                keyboard_buttons.append(
                    [InlineKeyboardButton(text=f"📁 {category.name}", callback_data=f"category_{category.id}")]
                )
            
            for product in city_products:
                keyboard_buttons.append(
                    [InlineKeyboardButton(
                        text=f"📦 {product.name} - {product.price:.2f}₽", 
                        callback_data=f"product_{product.id}"
                    )]
                )
            
            settings = storage_db.operator_settings
            if settings.operator_enabled:
                keyboard_buttons.append(
                    [InlineKeyboardButton(text=settings.operator_button_text, url=settings.operator_link)]
                )
            
            keyboard_buttons.append(
                [InlineKeyboardButton(text="🔙 Назад", callback_data="main_menu")]
            )
            
            keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
            
            message_text = f"📍 Город: <b>{city.name}</b>\n\n"
            if city_categories:
                message_text += f"📂 Категории: <b>{len(city_categories)}</b>\n"
            if city_products:
                message_text += f"📦 Товары в городе: <b>{len(city_products)}</b>\n"
            
            message_text += "\nВыберите категорию или товар:"
            
            await callback.message.edit_text(message_text, reply_markup=keyboard, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Ошибка в show_categories_and_products: {e}")
            await callback.message.edit_text("⚠️ Произошла ошибка при загрузке данных")

    @dp.callback_query(F.data.startswith("category_"))
    async def show_products(callback: CallbackQuery):
        try:
            category_id = int(callback.data.split("_")[1])
            category = storage_db.categories.get(category_id)
            
            if not category or not category.is_active:
                await callback.message.edit_text("⚠️ Категория не найдена")
                return
            
            active_products = {k: v for k, v in storage_db.products.items() if v.is_active}
            category_products = [prod for prod in active_products.values() if prod.category_id == category_id]
            
            if not category_products:
                await callback.message.edit_text("⚠️ В этой категории пока нет товаров")
                return
            
            keyboard_buttons = []
            for product in category_products:
                keyboard_buttons.append(
                    [InlineKeyboardButton(
                        text=f"{product.name} - {product.price:.2f}₽", 
                        callback_data=f"product_{product.id}"
                    )]
                )
            
            settings = storage_db.operator_settings
            if settings.operator_enabled:
                keyboard_buttons.append(
                    [InlineKeyboardButton(text=settings.operator_button_text, url=settings.operator_link)]
                )
            
            city_id = category.city_id if category else None
            back_data = f"city_{city_id}" if city_id else "main_menu"
            
            keyboard_buttons.append(
                [InlineKeyboardButton(text="🔙 Назад", callback_data=back_data)]
            )
            
            keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
            await callback.message.edit_text(f"🛍️ Товары в категории '{category.name}':", reply_markup=keyboard)
        except Exception as e:
            logger.error(f"Ошибка в show_products: {e}")
            await callback.message.edit_text("⚠️ Произошла ошибка при загрузке товаров")

    @dp.callback_query(F.data.startswith("product_"))
    async def show_product_detail(callback: CallbackQuery, state: FSMContext):
        try:
            product_id = int(callback.data.split("_")[1])
            product = storage_db.products.get(product_id)
            
            if not product or not product.is_active:
                await callback.message.edit_text("⚠️ Товар не найден")
                return
            
            await state.update_data(selected_product_id=product_id)
            
            back_data = ""
            if product.category_id:
                back_data = f"category_{product.category_id}"
            elif product.city_id:
                back_data = f"city_{product.city_id}"
            else:
                back_data = "main_menu"
            
            caption = (
                f"📦 <b>{product.name}</b>\n\n"
                f"📝 {product.description}\n\n"
                f"💰 Цена: <b>{product.price:.2f}₽</b>\n\n"
                f"Выберите способ оплаты:"
            )
            
            keyboard_buttons = [
                [
                    InlineKeyboardButton(text="💳 Карта", callback_data="payment_card"),
                    InlineKeyboardButton(text="₿ Крипта", callback_data="payment_crypto")
                ]
            ]
            
            settings = storage_db.operator_settings
            if settings.operator_enabled:
                keyboard_buttons.append(
                    [InlineKeyboardButton(text=settings.operator_button_text, url=settings.operator_link)]
                )
            
            keyboard_buttons.append(
                [InlineKeyboardButton(text="🔙 Назад", callback_data=back_data)]
            )
            
            keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
            
            if product.photo_url:
                await callback.message.delete()
                await callback.message.answer_photo(
                    photo=product.photo_url,
                    caption=caption,
                    reply_markup=keyboard,
                    parse_mode="HTML"
                )
            else:
                await callback.message.edit_text(
                    caption,
                    reply_markup=keyboard,
                    parse_mode="HTML"
                )
        except Exception as e:
            logger.error(f"Ошибка в show_product_detail: {e}")
            await callback.message.edit_text("⚠️ Произошла ошибка при загрузке товара")

    # ======================= ОПЛАТА =======================
    @dp.callback_query(F.data.startswith("payment_"))
    async def select_payment_method(callback: CallbackQuery, state: FSMContext):
        try:
            payment_method = callback.data.split("_")[1]
            
            await state.update_data(payment_method=payment_method)
            
            payment_details = storage_db.payment_details
            
            if payment_method == "card":
                payment_info = (
                    "💳 <b>Оплата банковской картой</b>\n\n"
                    f"<b>Реквизиты для оплаты:</b>\n"
                    f"Карта: <code>{payment_details.card_number}</code>\n"
                    f"Получатель: {payment_details.card_holder}\n\n"
                    "<b>Инструкция:</b>\n"
                    "1. Переведите сумму на карту выше\n"
                    "2. Сделайте скриншот чека об оплате\n"
                    "3. Отправьте скриншот в этот чат\n\n"
                    "⚠️ <i>В комментарии укажите номер заказа</i>"
                )
            else:
                payment_info = (
                    "₿ <b>Оплата криптовалютой</b>\n\n"
                    f"<b>Реквизиты для оплаты:</b>\n"
                    f"Сеть: {payment_details.crypto_network}\n"
                    f"Монета: {payment_details.crypto_coin}\n"
                    f"Адрес: <code>{payment_details.crypto_wallet}</code>\n\n"
                    "<b>Инструкция:</b>\n"
                    "1. Отправьте USDT на адрес выше\n"
                    "2. Сделайте скриншот перевода\n"
                    "3. Отправьте скриншот в этот чат\n\n"
                    "⚠️ <i>Укажите сумму точно как в заказе</i>"
                )
            
            user_data = await state.get_data()
            product_id = user_data.get('selected_product_id')
            product = storage_db.products.get(product_id) if product_id else None
            
            if product:
                payment_info_text = payment_info
                payment_info_text += f"\n\n<b>Сумма к оплате: {product.price:.2f}₽</b>"
            else:
                payment_info_text = payment_info
            
            if product:
                if product.category_id:
                    back_data = f"product_{product_id}"
                elif product.city_id:
                    back_data = f"product_{product_id}"
                else:
                    back_data = "main_menu"
            else:
                back_data = "main_menu"
            
            keyboard_buttons = [
                [InlineKeyboardButton(
                    text="🔙 Назад к товару", 
                    callback_data=back_data
                )]
            ]
            
            settings = storage_db.operator_settings
            if settings.operator_enabled:
                keyboard_buttons.append(
                    [InlineKeyboardButton(text=settings.operator_button_text, url=settings.operator_link)]
                )
            
            keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
            
            await callback.message.edit_text(
                payment_info_text,
                reply_markup=keyboard,
                parse_mode="HTML"
            )
            
            await state.set_state(UserState.waiting_for_payment_proof)
        except Exception as e:
            logger.error(f"Ошибка в select_payment_method: {e}")
            await callback.message.edit_text("⚠️ Произошла ошибка при выборе оплаты")

    @dp.message(UserState.waiting_for_payment_proof)
    async def receive_payment_proof(message: Message, state: FSMContext):
        try:
            user_data = await state.get_data()
            product_id = user_data.get('selected_product_id')
            payment_method = user_data.get('payment_method')
            
            product = storage_db.products.get(product_id)
            
            if not product:
                await message.answer("⚠️ Ошибка: товар не найден")
                await state.clear()
                return
            
            if not (message.photo or message.document):
                await message.answer("⚠️ Пожалуйста, отправьте фото или документ (скриншот оплаты)")
                return
            
            payment_proof = ""
            file_type = ""
            
            if message.photo:
                payment_proof = message.photo[-1].file_id
                file_type = "photo"
            elif message.document:
                payment_proof = message.document.file_id
                file_type = "document"
            
            order = Order(
                id=0,
                user_id=message.from_user.id,
                username=message.from_user.username or "Без имени",
                product_id=product_id,
                product_name=product.name,
                price=product.price,
                payment_method=payment_method,
                payment_proof=payment_proof,
                timestamp=datetime.now().isoformat()
            )
            
            order_id = storage_db.add_order(order)
            
            admin_text = (
                f"🛒 <b>НОВЫЙ ЗАКАЗ #{order_id}</b>\n\n"
                f"👤 <b>Пользователь:</b>\n"
                f"• ID: {message.from_user.id}\n"
                f"• Ник: @{message.from_user.username or 'нет'}\n"
                f"• Имя: {message.from_user.first_name or ''} {message.from_user.last_name or ''}\n\n"
                f"📦 <b>Товар:</b>\n"
                f"• Название: {product.name}\n"
                f"• ID товара: {product_id}\n\n"
                f"💰 <b>Оплата:</b>\n"
                f"• Сумма: {product.price:.2f}₽\n"
                f"• Способ: {'💳 Карта' if payment_method == 'card' else '₿ Крипта'}\n"
                f"• Время: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}"
            )
            
            admins = admin_manager.get_admins_list()
            notification_sent = False
            
            for admin_id in admins:
                try:
                    if file_type == "photo":
                        await bot.send_photo(
                            chat_id=admin_id,
                            photo=payment_proof,
                            caption=admin_text,
                            parse_mode="HTML"
                        )
                    elif file_type == "document":
                        await bot.send_document(
                            chat_id=admin_id,
                            document=payment_proof,
                            caption=admin_text,
                            parse_mode="HTML"
                        )
                    notification_sent = True
                except Exception as e:
                    logger.error(f"Не удалось отправить уведомление админу {admin_id}: {e}")
            
            if notification_sent:
                user_response = (
                    f"✅ <b>Спасибо! Ваш заказ #{order_id} принят.</b>\n\n"
                    f"📦 <b>Детали заказа:</b>\n"
                    f"• Товар: {product.name}\n"
                    f"• Сумма: {product.price:.2f}₽\n"
                    f"• Способ оплаты: {'Карта' if payment_method == 'card' else 'Крипта'}\n\n"
                    f"⏳ <b>Статус:</b> Ожидает подтверждения администратором\n\n"
                    f"📞 Администратор свяжется с вами в ближайшее время после проверки оплаты.\n"
                    f"Сохраните номер заказа: <code>{order_id}</code>"
                )
            else:
                user_response = (
                    "⚠️ <b>Заказ принят, но возникли проблемы с уведомлением администраторов.</b>\n"
                    "Пожалуйста, свяжитесь с поддержкой самостоятельно."
                )
            
            await message.answer(user_response, parse_mode="HTML")
            
            continue_keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🏪 Вернуться в главное меню", callback_data="main_menu")]
            ])
            
            await message.answer("Что хотите сделать дальше?", reply_markup=continue_keyboard)
            await state.clear()
        except Exception as e:
            logger.error(f"Ошибка в receive_payment_proof: {e}")
            await message.answer("⚠️ Произошла ошибка при обработке платежа")
            await state.clear()

    # ======================= АДМИН ПАНЕЛЬ =======================
    @dp.message(Command("admin"))
    async def cmd_admin(message: Message):
        if not admin_manager.is_admin(message.from_user.id):
            await message.answer("⛔ Доступ запрещен")
            return
        
        await message.answer(
            "👑 <b>Панель администратора</b>\n\n"
            "Выберите действие:",
            reply_markup=get_admin_keyboard(),
            parse_mode="HTML"
        )

    # ======================= ДОБАВЛЕНИЕ ТОВАРА В ГОРОД =======================
    @dp.callback_query(F.data == "admin_add_product_to_city")
    async def admin_add_product_to_city_start(callback: CallbackQuery, state: FSMContext):
        if not admin_manager.is_admin(callback.from_user.id):
            return
        
        cities = storage_db.cities
        active_cities = {k: v for k, v in cities.items() if v.is_active}
        
        if not active_cities:
            await callback.message.edit_text(
                "⚠️ Сначала добавьте хотя бы один город",
                reply_markup=get_back_admin_keyboard()
            )
            return
        
        city_list = list(active_cities.values())
        city_list.sort(key=lambda x: (x.order, x.name))
        
        keyboard_buttons = []
        for city in city_list:
            keyboard_buttons.append(
                [InlineKeyboardButton(text=f"{city.order}. {city.name}", callback_data=f"direct_city_{city.id}")]
            )
        keyboard_buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="admin_back")])
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
        
        await callback.message.edit_text(
            "📍 <b>Добавление товара в город (без категории)</b>\n\n"
            "Выберите город для нового товара:",
            reply_markup=keyboard,
            parse_mode="HTML"
        )

    @dp.callback_query(F.data.startswith("direct_city_"))
    async def admin_add_product_to_city_city(callback: CallbackQuery, state: FSMContext):
        if not admin_manager.is_admin(callback.from_user.id):
            return
        
        city_id = int(callback.data.split("_")[2])
        city = storage_db.cities.get(city_id)
        
        if not city:
            await callback.answer("Город не найден", show_alert=True)
            return
        
        await state.update_data(direct_city_id=city_id)
        
        await callback.message.edit_text(
            f"📍 <b>Добавление товара в город:</b> {city.name}\n\n"
            f"Введите название товара:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_add_product_to_city")]
            ]),
            parse_mode="HTML"
        )
        await state.set_state(UserState.waiting_for_product_name_direct)

    @dp.message(UserState.waiting_for_product_name_direct)
    async def admin_add_product_to_city_name(message: Message, state: FSMContext):
        if not admin_manager.is_admin(message.from_user.id):
            await state.clear()
            return
        
        if not message.text or len(message.text.strip()) < 2:
            await message.answer("⚠️ Название товара должно быть не менее 2 символов")
            return
        
        await state.update_data(direct_product_name=message.text.strip())
        
        await message.answer(
            "Введите описание товара:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_add_product_to_city")]
            ])
        )
        await state.set_state(UserState.waiting_for_product_description_direct)

    @dp.message(UserState.waiting_for_product_description_direct)
    async def admin_add_product_to_city_description(message: Message, state: FSMContext):
        if not admin_manager.is_admin(message.from_user.id):
            await state.clear()
            return
        
        if not message.text or len(message.text.strip()) < 5:
            await message.answer("⚠️ Описание должно быть не менее 5 символов")
            return
        
        await state.update_data(direct_product_description=message.text.strip())
        
        await message.answer(
            "Введите цену товара (только число, например: 2999.99):",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_add_product_to_city")]
            ])
        )
        await state.set_state(UserState.waiting_for_product_price_direct)

    @dp.message(UserState.waiting_for_product_price_direct)
    async def admin_add_product_to_city_price(message: Message, state: FSMContext):
        if not admin_manager.is_admin(message.from_user.id):
            await state.clear()
            return
        
        try:
            price = float(message.text)
            if price <= 0:
                await message.answer("⚠️ Цена должна быть больше 0")
                return
            
            await state.update_data(direct_product_price=price)
            
            await message.answer(
                "Отправьте фото товара (или отправьте 'нет' чтобы пропустить):",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_add_product_to_city")]
                ])
            )
            await state.set_state(UserState.waiting_for_product_photo_direct)
        except ValueError:
            await message.answer("⚠️ Пожалуйста, введите корректную цену (число)")

    @dp.message(UserState.waiting_for_product_photo_direct)
    async def admin_add_product_to_city_finish(message: Message, state: FSMContext):
        if not admin_manager.is_admin(message.from_user.id):
            await state.clear()
            return
        
        user_data = await state.get_data()
        
        photo_url = None
        if message.photo:
            photo_url = message.photo[-1].file_id
        elif message.text and message.text.lower() != 'нет':
            await message.answer("⚠️ Пожалуйста, отправьте фото или 'нет'")
            return
        
        city_id = user_data.get('direct_city_id')
        city = storage_db.cities.get(city_id)
        
        if not city:
            await message.answer("❌ Ошибка: город не найден", reply_markup=get_back_admin_keyboard())
            await state.clear()
            return
        
        product = Product(
            id=0,
            name=user_data.get('direct_product_name'),
            description=user_data.get('direct_product_description'),
            price=user_data.get('direct_product_price'),
            category_id=None,
            city_id=city_id,
            photo_url=photo_url,
            is_active=True
        )
        
        product_id = storage_db.add_product(product)
        
        await message.answer(
            f"✅ <b>Товар успешно добавлен в город!</b>\n\n"
            f"📍 <b>Город:</b> {city.order}. {city.name}\n"
            f"📦 <b>Товар:</b> {product.name}\n"
            f"💰 <b>Цена:</b> {product.price:.2f}₽\n"
            f"📝 <b>Описание:</b> {product.description}\n"
            f"🆔 <b>ID товара:</b> {product_id}\n\n"
            f"<i>Товар будет отображаться прямо в списке города, без категории.</i>",
            reply_markup=get_back_admin_keyboard(),
            parse_mode="HTML"
        )
        await state.clear()

    # ======================= ДОБАВЛЕНИЕ ГОРОДА =======================
    @dp.callback_query(F.data == "admin_add_city")
    async def admin_add_city_start(callback: CallbackQuery, state: FSMContext):
        if not admin_manager.is_admin(callback.from_user.id):
            return
        
        await callback.message.edit_text(
            "Введите название нового города:",
            reply_markup=get_back_admin_keyboard()
        )
        await state.set_state(UserState.waiting_for_city_name)

    @dp.message(UserState.waiting_for_city_name)
    async def admin_add_city_finish(message: Message, state: FSMContext):
        if not admin_manager.is_admin(message.from_user.id):
            await state.clear()
            return
        
        if not message.text or len(message.text.strip()) < 2:
            await message.answer("⚠️ Название города должно быть не менее 2 символов")
            return
        
        city = City(id=0, name=message.text.strip(), order=999, is_active=True)
        city_id = storage_db.add_city(city)
        
        await message.answer(f"✅ Город '{message.text}' добавлен (ID: {city_id})!\nПорядок можно настроить в разделе 'Порядок городов'", 
                            reply_markup=get_back_admin_keyboard())
        await state.clear()

    # ======================= УПРАВЛЕНИЕ ПОРЯДКОМ ГОРОДОВ =======================
    @dp.callback_query(F.data == "admin_city_order")
    async def admin_city_order(callback: CallbackQuery, state: FSMContext):
        if not admin_manager.is_admin(callback.from_user.id):
            return
        
        cities = storage_db.cities
        if not cities:
            await callback.message.edit_text(
                "⚠️ Городов нет для настройки порядка",
                reply_markup=get_back_admin_keyboard()
            )
            return
        
        city_list = list(cities.values())
        city_list.sort(key=lambda x: (x.order, x.name))
        
        city_list_text = "\n".join([f"{i+1}. {city.name} (ID: {city.id}, Порядок: {city.order})" 
                                for i, city in enumerate(city_list)])
        
        instructions = (
            "🔄 <b>Настройка порядка городов</b>\n\n"
            "<b>Текущий порядок:</b>\n"
            f"{city_list_text}\n\n"
            "<b>Как изменить порядок:</b>\n"
            "1. Введите список ID городов через запятую\n"
            "2. Порядок в списке будет новым порядком городов\n"
            "3. Пример: 1,5,3,2,4\n\n"
            "<b>Введите список ID городов в нужном порядке:</b>"
        )
        
        await callback.message.edit_text(
            instructions,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_back")]
            ]),
            parse_mode="HTML"
        )
        await state.set_state(UserState.waiting_for_city_order)

    @dp.message(UserState.waiting_for_city_order)
    async def admin_set_city_order_finish(message: Message, state: FSMContext):
        if not admin_manager.is_admin(message.from_user.id):
            await state.clear()
            return
        
        try:
            city_ids_text = message.text.strip()
            city_ids = [int(id_str.strip()) for id_str in city_ids_text.split(',') if id_str.strip()]
            
            if not city_ids:
                await message.answer(
                    "⚠️ Введите список ID городов через запятую (например: 1,5,3,2,4)",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_back")]
                    ])
                )
                return
            
            missing_ids = []
            for city_id in city_ids:
                if city_id not in storage_db.cities:
                    missing_ids.append(city_id)
            
            if missing_ids:
                await message.answer(
                    f"⚠️ Не найдены города с ID: {', '.join(map(str, missing_ids))}",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_back")]
                    ])
                )
                return
            
            order_mapping = {}
            for order, city_id in enumerate(city_ids, 1):
                order_mapping[city_id] = order
            
            if storage_db.bulk_update_city_orders(order_mapping):
                updated_cities = []
                for city_id in city_ids:
                    city = storage_db.cities.get(city_id)
                    if city:
                        updated_cities.append(f"{city.order}. {city.name} (ID: {city.id})")
                
                result_text = "✅ <b>Порядок городов обновлен!</b>\n\n<b>Новый порядок:</b>\n" + "\n".join(updated_cities)
            else:
                result_text = "❌ Не удалось обновить порядок городов"
            
            await message.answer(
                result_text,
                reply_markup=get_back_admin_keyboard(),
                parse_mode="HTML"
            )
            await state.clear()
        except ValueError:
            await message.answer(
                "⚠️ Некорректный формат. Введите список ID через запятую (например: 1,5,3,2,4)",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_back")]
                ])
            )
            await state.clear()
        except Exception as e:
            await message.answer(
                f"⚠️ Ошибка: {str(e)}",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_back")]
                ])
            )
            await state.clear()

    # ======================= УДАЛЕНИЕ ГОРОДА =======================
    @dp.callback_query(F.data == "admin_delete_city")
    async def admin_delete_city_start(callback: CallbackQuery):
        if not admin_manager.is_admin(callback.from_user.id):
            return
        
        cities = storage_db.cities
        if not cities:
            await callback.message.edit_text(
                "⚠️ Городов нет для удаления",
                reply_markup=get_back_admin_keyboard()
            )
            return
        
        city_list = list(cities.values())
        city_list.sort(key=lambda x: (x.order, x.name))
        
        keyboard_buttons = []
        for city in city_list:
            keyboard_buttons.append(
                [InlineKeyboardButton(text=f"{city.order}. {city.name} (ID: {city.id})", callback_data=f"delete_city_{city.id}")]
            )
        
        keyboard_buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="admin_back")])
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
        
        await callback.message.edit_text(
            "Выберите город для удаления (удалятся также все категории и товары этого города):",
            reply_markup=keyboard
        )

    @dp.callback_query(F.data.startswith("delete_city_"))
    async def admin_delete_city_confirm(callback: CallbackQuery):
        if not admin_manager.is_admin(callback.from_user.id):
            return
        
        city_id = int(callback.data.split("_")[2])
        city = storage_db.cities.get(city_id)
        
        if not city:
            await callback.answer("Город не найден", show_alert=True)
            return
        
        confirm_keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"confirm_delete_city_{city_id}")],
            [InlineKeyboardButton(text="❌ Нет, отменить", callback_data="admin_delete_city")]
        ])
        
        await callback.message.edit_text(
            f"⚠️ <b>Подтвердите удаление города</b>\n\n"
            f"Город: {city.name}\n"
            f"ID: {city_id}\n"
            f"Порядок: {city.order}\n\n"
            f"<b>Внимание!</b> Будут также удалены:\n"
            f"• Все категории этого города\n"
            f"• Все товары в этих категориях\n"
            f"• Все товары, добавленные напрямую в город\n\n"
            f"Удалить город {city.name}?",
            reply_markup=confirm_keyboard,
            parse_mode="HTML"
        )

    @dp.callback_query(F.data.startswith("confirm_delete_city_"))
    async def admin_delete_city_finish(callback: CallbackQuery):
        if not admin_manager.is_admin(callback.from_user.id):
            return
        
        city_id = int(callback.data.split("_")[3])
        city = storage_db.cities.get(city_id)
        
        if not city:
            await callback.answer("Город не найден", show_alert=True)
            return
        
        city_name = city.name
        if storage_db.delete_city(city_id):
            await callback.message.edit_text(
                f"✅ Город '{city_name}' удален!",
                reply_markup=get_back_admin_keyboard()
            )
        else:
            await callback.message.edit_text(
                f"❌ Не удалось удалить город",
                reply_markup=get_back_admin_keyboard()
            )

    # ======================= ДОБАВЛЕНИЕ КАТЕГОРИИ =======================
    @dp.callback_query(F.data == "admin_add_category")
    async def admin_add_category_start(callback: CallbackQuery, state: FSMContext):
        if not admin_manager.is_admin(callback.from_user.id):
            return
        
        cities = storage_db.cities
        active_cities = {k: v for k, v in cities.items() if v.is_active}
        
        if not active_cities:
            await callback.message.edit_text(
                "⚠️ Сначала добавьте хотя бы один город",
                reply_markup=get_back_admin_keyboard()
            )
            return
        
        city_list = list(active_cities.values())
        city_list.sort(key=lambda x: (x.order, x.name))
        
        keyboard_buttons = []
        for city in city_list:
            keyboard_buttons.append(
                [InlineKeyboardButton(text=f"{city.order}. {city.name}", callback_data=f"admin_city_{city.id}")]
            )
        keyboard_buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="admin_back")])
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
        
        await callback.message.edit_text(
            "Выберите город для новой категории:",
            reply_markup=keyboard
        )

    @dp.callback_query(F.data.startswith("admin_city_"))
    async def admin_add_category_city(callback: CallbackQuery, state: FSMContext):
        if not admin_manager.is_admin(callback.from_user.id):
            return
        
        city_id = int(callback.data.split("_")[2])
        await state.update_data(category_city_id=city_id)
        
        await callback.message.edit_text(
            "Введите название новой категории:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_add_category")]
            ])
        )
        await state.set_state(UserState.waiting_for_category_name)

    @dp.message(UserState.waiting_for_category_name)
    async def admin_add_category_finish(message: Message, state: FSMContext):
        if not admin_manager.is_admin(message.from_user.id):
            await state.clear()
            return
        
        user_data = await state.get_data()
        city_id = user_data.get('category_city_id')
        
        if not message.text or len(message.text.strip()) < 2:
            await message.answer("⚠️ Название категории должно быть не менее 2 символов")
            return
        
        category = Category(id=0, name=message.text.strip(), city_id=city_id, is_active=True)
        category_id = storage_db.add_category(category)
        
        city_name = storage_db.cities.get(city_id, City(0, "Неизвестно")).name
        await message.answer(f"✅ Категория '{message.text}' добавлена в город '{city_name}' (ID: {category_id})!", 
                            reply_markup=get_back_admin_keyboard())
        await state.clear()

    # ======================= УДАЛЕНИЕ КАТЕГОРИИ =======================
    @dp.callback_query(F.data == "admin_delete_category")
    async def admin_delete_category_start(callback: CallbackQuery):
        if not admin_manager.is_admin(callback.from_user.id):
            return
        
        categories = storage_db.categories
        if not categories:
            await callback.message.edit_text(
                "⚠️ Категорий нет для удаления",
                reply_markup=get_back_admin_keyboard()
            )
            return
        
        keyboard_buttons = []
        for cat_id, category in categories.items():
            city = storage_db.cities.get(category.city_id, City(0, "Неизвестно", 999))
            keyboard_buttons.append(
                [InlineKeyboardButton(text=f"{category.name} ({city.order}. {city.name})", callback_data=f"delete_category_{cat_id}")]
            )
        
        keyboard_buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="admin_back")])
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
        
        await callback.message.edit_text(
            "Выберите категорию для удаления (удалятся также все товары этой категории):",
            reply_markup=keyboard
        )

    @dp.callback_query(F.data.startswith("delete_category_"))
    async def admin_delete_category_confirm(callback: CallbackQuery):
        if not admin_manager.is_admin(callback.from_user.id):
            return
        
        category_id = int(callback.data.split("_")[2])
        category = storage_db.categories.get(category_id)
        
        if not category:
            await callback.answer("Категория не найдена", show_alert=True)
            return
        
        city = storage_db.cities.get(category.city_id, City(0, "Неизвестно", 999))
        
        confirm_keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"confirm_delete_category_{category_id}")],
            [InlineKeyboardButton(text="❌ Нет, отменить", callback_data="admin_delete_category")]
        ])
        
        await callback.message.edit_text(
            f"⚠️ <b>Подтвердите удаление категории</b>\n\n"
            f"Категория: {category.name}\n"
            f"Город: {city.order}. {city.name}\n"
            f"ID: {category_id}\n\n"
            f"<b>Внимание!</b> Будут также удалены все товары этой категории.\n\n"
            f"Удалить категорию {category.name}?",
            reply_markup=confirm_keyboard,
            parse_mode="HTML"
        )

    @dp.callback_query(F.data.startswith("confirm_delete_category_"))
    async def admin_delete_category_finish(callback: CallbackQuery):
        if not admin_manager.is_admin(callback.from_user.id):
            return
        
        category_id = int(callback.data.split("_")[3])
        category = storage_db.categories.get(category_id)
        
        if not category:
            await callback.answer("Категория не найдена", show_alert=True)
            return
        
        category_name = category.name
        if storage_db.delete_category(category_id):
            await callback.message.edit_text(
                f"✅ Категория '{category_name}' удалена!",
                reply_markup=get_back_admin_keyboard()
            )
        else:
            await callback.message.edit_text(
                f"❌ Не удалось удалить категорию",
                reply_markup=get_back_admin_keyboard()
            )

    # ======================= ДОБАВЛЕНИЕ ОДНОГО ТОВАРА =======================
    @dp.callback_query(F.data == "admin_add_product")
    async def admin_add_product_start(callback: CallbackQuery, state: FSMContext):
        if not admin_manager.is_admin(callback.from_user.id):
            return
        
        categories = storage_db.categories
        active_categories = {k: v for k, v in categories.items() if v.is_active}
        
        if not active_categories:
            await callback.message.edit_text(
                "⚠️ Сначала добавьте хотя бы одну категорию\n\n"
                "Или используйте '➕📍 Товар в город' для добавления товара без категории",
                reply_markup=get_back_admin_keyboard()
            )
            return
        
        categories_by_city = {}
        for cat_id, category in active_categories.items():
            city = storage_db.cities.get(category.city_id)
            if city:
                if city.id not in categories_by_city:
                    categories_by_city[city.id] = {
                        'city': city,
                        'categories': []
                    }
                categories_by_city[city.id]['categories'].append(category)
        
        sorted_cities = sorted(categories_by_city.values(), key=lambda x: x['city'].order)
        
        keyboard_buttons = []
        for city_data in sorted_cities:
            city = city_data['city']
            for category in city_data['categories']:
                keyboard_buttons.append(
                    [InlineKeyboardButton(
                        text=f"{city.order}. {city.name} → {category.name}", 
                        callback_data=f"admin_category_{category.id}"
                    )]
                )
        
        keyboard_buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="admin_back")])
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
        
        await callback.message.edit_text(
            "Выберите категорию для нового товара:",
            reply_markup=keyboard
        )

    @dp.callback_query(F.data.startswith("admin_category_"))
    async def admin_add_product_category(callback: CallbackQuery, state: FSMContext):
        if not admin_manager.is_admin(callback.from_user.id):
            return
        
        category_id = int(callback.data.split("_")[2])
        await state.update_data(product_category_id=category_id)
        
        await callback.message.edit_text(
            "Введите название товара:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_add_product")]
            ])
        )
        await state.set_state(UserState.waiting_for_product_name)

    @dp.message(UserState.waiting_for_product_name)
    async def admin_add_product_name(message: Message, state: FSMContext):
        if not admin_manager.is_admin(message.from_user.id):
            await state.clear()
            return
        
        if not message.text or len(message.text.strip()) < 2:
            await message.answer("⚠️ Название товара должно быть не менее 2 символов")
            return
        
        await state.update_data(product_name=message.text.strip())
        
        await message.answer(
            "Введите описание товара:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_add_product")]
            ])
        )
        await state.set_state(UserState.waiting_for_product_description)

    @dp.message(UserState.waiting_for_product_description)
    async def admin_add_product_description(message: Message, state: FSMContext):
        if not admin_manager.is_admin(message.from_user.id):
            await state.clear()
            return
        
        if not message.text or len(message.text.strip()) < 5:
            await message.answer("⚠️ Описание должно быть не менее 5 символов")
            return
        
        await state.update_data(product_description=message.text.strip())
        
        await message.answer(
            "Введите цену товара (только число, например: 2999.99):",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_add_product")]
            ])
        )
        await state.set_state(UserState.waiting_for_product_price)

    @dp.message(UserState.waiting_for_product_price)
    async def admin_add_product_price(message: Message, state: FSMContext):
        if not admin_manager.is_admin(message.from_user.id):
            await state.clear()
            return
        
        try:
            price = float(message.text)
            if price <= 0:
                await message.answer("⚠️ Цена должна быть больше 0")
                return
            
            await state.update_data(product_price=price)
            
            await message.answer(
                "Отправьте фото товара (или отправьте 'нет' чтобы пропустить):",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_add_product")]
                ])
            )
            await state.set_state(UserState.waiting_for_product_photo)
        except ValueError:
            await message.answer("⚠️ Пожалуйста, введите корректную цену (число)")

    @dp.message(UserState.waiting_for_product_photo)
    async def admin_add_product_finish(message: Message, state: FSMContext):
        if not admin_manager.is_admin(message.from_user.id):
            await state.clear()
            return
        
        user_data = await state.get_data()
        
        photo_url = None
        if message.photo:
            photo_url = message.photo[-1].file_id
        elif message.text and message.text.lower() != 'нет':
            await message.answer("⚠️ Пожалуйста, отправьте фото или 'нет'")
            return
        
        product = Product(
            id=0,
            name=user_data.get('product_name'),
            description=user_data.get('product_description'),
            price=user_data.get('product_price'),
            category_id=user_data.get('product_category_id'),
            photo_url=photo_url,
            is_active=True
        )
        
        product_id = storage_db.add_product(product)
        
        category = storage_db.categories.get(user_data.get('product_category_id'))
        category_name = category.name if category else "Неизвестно"
        city_name = "Неизвестно"
        if category:
            city = storage_db.cities.get(category.city_id)
            if city:
                city_name = f"{city.order}. {city.name}"
        
        await message.answer(
            f"✅ Товар успешно добавлен!\n\n"
            f"📦 Название: {product.name}\n"
            f"💰 Цена: {product.price:.2f}₽\n"
            f"📂 Категория: {category_name}\n"
            f"🏙️ Город: {city_name}\n"
            f"🆔 ID товара: {product_id}",
            reply_markup=get_back_admin_keyboard()
        )
        await state.clear()

    # ======================= УДАЛЕНИЕ ТОВАРА =======================
    @dp.callback_query(F.data == "admin_delete_product")
    async def admin_delete_product_start(callback: CallbackQuery):
        if not admin_manager.is_admin(callback.from_user.id):
            return
        
        products = storage_db.products
        if not products:
            await callback.message.edit_text(
                "⚠️ Товаров нет для удаления",
                reply_markup=get_back_admin_keyboard()
            )
            return
        
        products_info = []
        for prod_id, product in list(products.items())[:20]:
            location_type = ""
            location_name = ""
            
            if product.category_id:
                category = storage_db.categories.get(product.category_id, Category(0, "", 0))
                city = storage_db.cities.get(category.city_id, City(0, "Неизвестно", 999))
                location_type = "Категория"
                location_name = f"{city.order}. {city.name} → {category.name}"
            elif product.city_id:
                city = storage_db.cities.get(product.city_id, City(0, "Неизвестно", 999))
                location_type = "Город"
                location_name = f"{city.order}. {city.name} (напрямую)"
            else:
                location_type = "Без категории"
                location_name = "Неизвестно"
            
            products_info.append({
                'id': prod_id,
                'product': product,
                'location_type': location_type,
                'location_name': location_name
            })
        
        products_info.sort(key=lambda x: x['product'].name)
        
        keyboard_buttons = []
        for info in products_info:
            keyboard_buttons.append(
                [InlineKeyboardButton(
                    text=f"{info['product'].name} ({info['location_name']})", 
                    callback_data=f"delete_product_{info['id']}"
                )]
            )
        
        keyboard_buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="admin_back")])
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
        
        await callback.message.edit_text(
            "Выберите товар для удаления:",
            reply_markup=keyboard
        )

    @dp.callback_query(F.data.startswith("delete_product_"))
    async def admin_delete_product_confirm(callback: CallbackQuery):
        if not admin_manager.is_admin(callback.from_user.id):
            return
        
        product_id = int(callback.data.split("_")[2])
        product = storage_db.products.get(product_id)
        
        if not product:
            await callback.answer("Товар не найден", show_alert=True)
            return
        
        location_info = ""
        if product.category_id:
            category = storage_db.categories.get(product.category_id, Category(0, "", 0))
            city = storage_db.cities.get(category.city_id, City(0, "Неизвестно", 999))
            location_info = f"Категория: {category.name}\nГород: {city.order}. {city.name}"
        elif product.city_id:
            city = storage_db.cities.get(product.city_id, City(0, "Неизвестно", 999))
            location_info = f"Город: {city.order}. {city.name} (напрямую)"
        else:
            location_info = "Без категории"
        
        confirm_keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"confirm_delete_product_{product_id}")],
            [InlineKeyboardButton(text="❌ Нет, отменить", callback_data="admin_delete_product")]
        ])
        
        await callback.message.edit_text(
            f"⚠️ <b>Подтвердите удаление товара</b>\n\n"
            f"Товар: {product.name}\n"
            f"Цена: {product.price:.2f}₽\n"
            f"ID: {product_id}\n"
            f"Расположение:\n{location_info}\n\n"
            f"Удалить товар {product.name}?",
            reply_markup=confirm_keyboard,
            parse_mode="HTML"
        )

    @dp.callback_query(F.data.startswith("confirm_delete_product_"))
    async def admin_delete_product_finish(callback: CallbackQuery):
        if not admin_manager.is_admin(callback.from_user.id):
            return
        
        product_id = int(callback.data.split("_")[3])
        product = storage_db.products.get(product_id)
        
        if not product:
            await callback.answer("Товар не найден", show_alert=True)
            return
        
        product_name = product.name
        if storage_db.delete_product(product_id):
            await callback.message.edit_text(
                f"✅ Товар '{product_name}' удален!",
                reply_markup=get_back_admin_keyboard()
            )
        else:
            await callback.message.edit_text(
                f"❌ Не удалось удалить товар",
                reply_markup=get_back_admin_keyboard()
            )

    # ======================= МАССОВОЕ ДОБАВЛЕНИЕ ТОВАРОВ =======================
    @dp.callback_query(F.data == "admin_add_multiple_products")
    async def admin_add_multiple_products_start(callback: CallbackQuery, state: FSMContext):
        if not admin_manager.is_admin(callback.from_user.id):
            return
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📂 В категорию", callback_data="admin_multiple_to_category")],
            [InlineKeyboardButton(text="📍 В город (без категории)", callback_data="admin_multiple_to_city")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_back")]
        ])
        
        await callback.message.edit_text(
            "📦 <b>Массовое добавление товаров</b>\n\n"
            "<b>Внимание:</b> Каждый товар будет иметь свое описание и цену!\n\n"
            "Выберите куда добавлять товары:",
            reply_markup=keyboard,
            parse_mode="HTML"
        )

    @dp.callback_query(F.data == "admin_multiple_to_category")
    async def admin_add_multiple_products_to_category(callback: CallbackQuery):
        if not admin_manager.is_admin(callback.from_user.id):
            return
        
        categories = storage_db.categories
        active_categories = {k: v for k, v in categories.items() if v.is_active}
        
        if not active_categories:
            await callback.message.edit_text(
                "⚠️ Сначала добавьте хотя бы одну категорию",
                reply_markup=get_back_admin_keyboard()
            )
            return
        
        categories_by_city = {}
        for cat_id, category in active_categories.items():
            city = storage_db.cities.get(category.city_id)
            if city:
                if city.id not in categories_by_city:
                    categories_by_city[city.id] = {
                        'city': city,
                        'categories': []
                    }
                categories_by_city[city.id]['categories'].append(category)
        
        sorted_cities = sorted(categories_by_city.values(), key=lambda x: x['city'].order)
        
        keyboard_buttons = []
        for city_data in sorted_cities:
            city = city_data['city']
            for category in city_data['categories']:
                keyboard_buttons.append(
                    [InlineKeyboardButton(
                        text=f"{city.order}. {city.name} → {category.name}", 
                        callback_data=f"admin_multiple_category_{category.id}"
                    )]
                )
        
        keyboard_buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="admin_add_multiple_products")])
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
        
        await callback.message.edit_text(
            "📦 <b>Массовое добавление товаров в категорию</b>\n\n"
            "Выберите категорию для новых товаров:",
            reply_markup=keyboard,
            parse_mode="HTML"
        )

    @dp.callback_query(F.data == "admin_multiple_to_city")
    async def admin_add_multiple_products_to_city(callback: CallbackQuery, state: FSMContext):
        if not admin_manager.is_admin(callback.from_user.id):
            return
        
        cities = storage_db.cities
        active_cities = {k: v for k, v in cities.items() if v.is_active}
        
        if not active_cities:
            await callback.message.edit_text(
                "⚠️ Сначала добавьте хотя бы один город",
                reply_markup=get_back_admin_keyboard()
            )
            return
        
        city_list = list(active_cities.values())
        city_list.sort(key=lambda x: (x.order, x.name))
        
        keyboard_buttons = []
        for city in city_list:
            keyboard_buttons.append(
                [InlineKeyboardButton(text=f"{city.order}. {city.name}", callback_data=f"admin_multiple_city_{city.id}")]
            )
        keyboard_buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="admin_add_multiple_products")])
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
        
        await callback.message.edit_text(
            "📦 <b>Массовое добавление товаров в город</b>\n\n"
            "Выберите город для новых товаров:",
            reply_markup=keyboard,
            parse_mode="HTML"
        )

    @dp.callback_query(F.data.startswith("admin_multiple_category_"))
    async def admin_add_multiple_products_category(callback: CallbackQuery, state: FSMContext):
        if not admin_manager.is_admin(callback.from_user.id):
            return
        
        category_id = int(callback.data.split("_")[3])
        await state.update_data(multiple_category_id=category_id)
        await state.update_data(multiple_type="category")
        
        category = storage_db.categories.get(category_id)
        category_name = category.name if category else "Неизвестно"
        city_name = "Неизвестно"
        if category:
            city = storage_db.cities.get(category.city_id)
            if city:
                city_name = f"{city.order}. {city.name}"
        
        await callback.message.edit_text(
            f"📦 <b>Массовое добавление товаров</b>\n\n"
            f"Город: <b>{city_name}</b>\n"
            f"Категория: <b>{category_name}</b>\n\n"
            f"📝 <b>Введите названия товаров через запятую:</b>\n\n"
            f"<i>Пример:</i>\n"
            f"iPhone 15 Pro, MacBook Air, iPad Pro, AirPods Max\n\n"
            f"Каждый товар будет иметь свое описание и цену.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_multiple_to_category")]
            ]),
            parse_mode="HTML"
        )
        await state.set_state(UserState.waiting_for_multiple_products_data)

    @dp.callback_query(F.data.startswith("admin_multiple_city_"))
    async def admin_add_multiple_products_city(callback: CallbackQuery, state: FSMContext):
        if not admin_manager.is_admin(callback.from_user.id):
            return
        
        city_id = int(callback.data.split("_")[3])
        await state.update_data(multiple_city_id=city_id)
        await state.update_data(multiple_type="city")
        
        city = storage_db.cities.get(city_id)
        city_name = city.name if city else "Неизвестно"
        
        await callback.message.edit_text(
            f"📦 <b>Массовое добавление товаров в город</b>\n\n"
            f"Город: <b>{city_name}</b>\n\n"
            f"📝 <b>Введите названия товаров через запятую:</b>\n\n"
            f"<i>Пример:</i>\n"
            f"iPhone 15 Pro, MacBook Air, iPad Pro, AirPods Max\n\n"
            f"Каждый товар будет иметь свое описание и цену.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_multiple_to_city")]
            ]),
            parse_mode="HTML"
        )
        await state.set_state(UserState.waiting_for_multiple_products_data)

    @dp.message(UserState.waiting_for_multiple_products_data)
    async def admin_add_multiple_products_names(message: Message, state: FSMContext):
        if not admin_manager.is_admin(message.from_user.id):
            await state.clear()
            return
        
        product_names = [name.strip() for name in message.text.split(',') if name.strip()]
        
        if not product_names:
            await message.answer(
                "⚠️ Не найдено названий товаров. Введите названия через запятую.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_add_multiple_products")]
                ])
            )
            return
        
        await state.update_data(product_names=product_names)
        
        names_text = "\n".join([f"{i+1}. {name}" for i, name in enumerate(product_names)])
        
        await message.answer(
            f"✅ Найдено <b>{len(product_names)}</b> товаров.\n\n"
            f"📝 <b>Введите описания для каждого товара через запятую в том же порядке:</b>\n\n"
            f"<b>Товары:</b>\n{names_text}\n\n"
            f"<i>Пример:</i>\n"
            f"Новый iPhone с улучшенной камерой, Легкий ноутбук для работы, Планшет с большим экраном, Наушники с шумоподавлением\n\n"
            f"<b>Количество описаний должно совпадать с количеством товаров ({len(product_names)})</b>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_add_multiple_products")]
            ]),
            parse_mode="HTML"
        )
        await state.set_state(UserState.waiting_for_multiple_products_descriptions)

    @dp.message(UserState.waiting_for_multiple_products_descriptions)
    async def admin_add_multiple_products_descriptions(message: Message, state: FSMContext):
        if not admin_manager.is_admin(message.from_user.id):
            await state.clear()
            return
        
        user_data = await state.get_data()
        product_names = user_data.get('product_names', [])
        
        descriptions = [desc.strip() for desc in message.text.split(',') if desc.strip()]
        
        if len(descriptions) != len(product_names):
            await message.answer(
                f"⚠️ Количество описаний ({len(descriptions)}) не совпадает с количеством товаров ({len(product_names)})\n\n"
                f"Пожалуйста, введите {len(product_names)} описаний через запятую.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_add_multiple_products")]
                ])
            )
            return
        
        await state.update_data(product_descriptions=descriptions)
        
        items_text = "\n".join([f"{i+1}. {product_names[i]}: {descriptions[i]}" for i in range(len(product_names))])
        
        await message.answer(
            f"✅ Описания сохранены.\n\n"
            f"💰 <b>Введите цены для каждого товара через запятую в том же порядке:</b>\n\n"
            f"<b>Товары с описаниями:</b>\n{items_text}\n\n"
            f"<i>Пример:</i>\n"
            f"99999.99, 79999.99, 59999.99, 29999.99\n\n"
            f"<b>Количество цен должно совпадать с количеством товаров ({len(product_names)})</b>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_add_multiple_products")]
            ]),
            parse_mode="HTML"
        )
        await state.set_state(UserState.waiting_for_multiple_products_prices)

    @dp.message(UserState.waiting_for_multiple_products_prices)
    async def admin_add_multiple_products_prices(message: Message, state: FSMContext):
        if not admin_manager.is_admin(message.from_user.id):
            await state.clear()
            return
        
        user_data = await state.get_data()
        product_names = user_data.get('product_names', [])
        
        try:
            price_texts = [p.strip() for p in message.text.split(',') if p.strip()]
            
            if len(price_texts) != len(product_names):
                await message.answer(
                    f"⚠️ Количество цен ({len(price_texts)}) не совпадает с количеством товаров ({len(product_names)})\n\n"
                    f"Пожалуйста, введите {len(product_names)} цен через запятую.",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_add_multiple_products")]
                    ])
                )
                return
            
            prices = []
            for i, price_text in enumerate(price_texts):
                try:
                    price = float(price_text)
                    if price <= 0:
                        await message.answer(
                            f"⚠️ Цена для товара '{product_names[i]}' должна быть больше 0",
                            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                                [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_add_multiple_products")]
                            ])
                        )
                        return
                    prices.append(price)
                except ValueError:
                    await message.answer(
                        f"⚠️ Некорректная цена для товара '{product_names[i]}': '{price_text}'\n"
                        f"Пожалуйста, введите число (например: 2999.99)",
                        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_add_multiple_products")]
                        ])
                    )
                    return
            
            await state.update_data(product_prices=prices)
            
            summary = "\n".join([f"{i+1}. {product_names[i]} - {prices[i]:.2f}₽: {user_data.get('product_descriptions', [])[i]}" 
                            for i in range(len(product_names))])
            
            await message.answer(
                f"✅ Цены сохранены.\n\n"
                f"🖼️ <b>Теперь нужно добавить фото для каждого товара.</b>\n\n"
                f"<b>Сводка товаров:</b>\n{summary}\n\n"
                f"<b>Как добавить фото:</b>\n"
                f"1. Отправьте одно фото - оно будет использовано для всех товаров\n"
                f"2. Или отправьте 'нет' чтобы не добавлять фото\n\n"
                f"Отправьте фото или 'нет':",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_add_multiple_products")]
                ]),
                parse_mode="HTML"
            )
            await state.set_state(UserState.waiting_for_multiple_products_photos)
        except Exception as e:
            await message.answer(
                f"⚠️ Ошибка при обработке цен: {str(e)}",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_add_multiple_products")]
                ])
            )

    @dp.message(UserState.waiting_for_multiple_products_photos)
    async def admin_add_multiple_products_finish(message: Message, state: FSMContext):
        if not admin_manager.is_admin(message.from_user.id):
            await state.clear()
            return
        
        user_data = await state.get_data()
        
        product_names = user_data.get('product_names', [])
        product_descriptions = user_data.get('product_descriptions', [])
        product_prices = user_data.get('product_prices', [])
        multiple_type = user_data.get('multiple_type', 'category')
        
        if len(product_names) != len(product_descriptions) or len(product_names) != len(product_prices):
            await message.answer(
                "❌ Ошибка: количество названий, описаний и цен не совпадает",
                reply_markup=get_back_admin_keyboard()
            )
            await state.clear()
            return
        
        photo_url = None
        if message.photo:
            photo_url = message.photo[-1].file_id
        elif message.text and message.text.lower() != 'нет':
            await message.answer(
                "⚠️ Пожалуйста, отправьте фото или 'нет'",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_add_multiple_products")]
                ])
            )
            return
        
        added_products = []
        
        if multiple_type == 'category':
            category_id = user_data.get('multiple_category_id')
            category = storage_db.categories.get(category_id)
            category_name = category.name if category else "Неизвестно"
            city_name = "Неизвестно"
            if category:
                city = storage_db.cities.get(category.city_id)
                if city:
                    city_name = f"{city.order}. {city.name}"
            
            for i, product_name in enumerate(product_names):
                product = Product(
                    id=0,
                    name=product_name,
                    description=product_descriptions[i] if i < len(product_descriptions) else "",
                    price=product_prices[i] if i < len(product_prices) else 0.0,
                    category_id=category_id,
                    photo_url=photo_url,
                    is_active=True
                )
                
                product_id = storage_db.add_product(product)
                added_products.append(f"• {product_name} - {product_prices[i]:.2f}₽ (ID: {product_id})")
            
            if added_products:
                result_text = (
                    f"✅ <b>Успешно добавлено {len(added_products)} товаров в категорию!</b>\n\n"
                    f"🏙️ <b>Город:</b> {city_name}\n"
                    f"📂 <b>Категория:</b> {category_name}\n"
                    f"🖼️ <b>Фото:</b> {'Да' if photo_url else 'Нет'}\n\n"
                    f"<b>Добавленные товары:</b>\n"
                )
                result_text += "\n".join(added_products)
            else:
                result_text = "❌ Не удалось добавить товары"
        
        else:
            city_id = user_data.get('multiple_city_id')
            city = storage_db.cities.get(city_id)
            city_name = city.name if city else "Неизвестно"
            
            for i, product_name in enumerate(product_names):
                product = Product(
                    id=0,
                    name=product_name,
                    description=product_descriptions[i] if i < len(product_descriptions) else "",
                    price=product_prices[i] if i < len(product_prices) else 0.0,
                    category_id=None,
                    city_id=city_id,
                    photo_url=photo_url,
                    is_active=True
                )
                
                product_id = storage_db.add_product(product)
                added_products.append(f"• {product_name} - {product_prices[i]:.2f}₽ (ID: {product_id})")
            
            if added_products:
                result_text = (
                    f"✅ <b>Успешно добавлено {len(added_products)} товаров в город!</b>\n\n"
                    f"📍 <b>Город:</b> {city.order}. {city_name}\n"
                    f"🖼️ <b>Фото:</b> {'Да' if photo_url else 'Нет'}\n\n"
                    f"<b>Добавленные товары:</b>\n"
                )
                result_text += "\n".join(added_products)
            else:
                result_text = "❌ Не удалось добавить товары"
        
        await message.answer(
            result_text,
            reply_markup=get_back_admin_keyboard(),
            parse_mode="HTML"
        )
        await state.clear()

    # ======================= НАСТРОЙКА ОПЛАТЫ =======================
    @dp.callback_query(F.data == "admin_payment_settings")
    async def admin_payment_settings(callback: CallbackQuery):
        if not admin_manager.is_admin(callback.from_user.id):
            return
        
        await callback.message.edit_text(
            "💳 <b>Настройка реквизитов оплаты</b>\n\n"
            "Выберите что настроить:",
            reply_markup=get_payment_settings_keyboard(),
            parse_mode="HTML"
        )

    @dp.callback_query(F.data == "admin_set_card")
    async def admin_set_card_start(callback: CallbackQuery, state: FSMContext):
        if not admin_manager.is_admin(callback.from_user.id):
            return
        
        payment = storage_db.payment_details
        
        await callback.message.edit_text(
            f"💳 <b>Настройка банковской карты</b>\n\n"
            f"Текущие реквизиты:\n"
            f"Карта: {payment.card_number}\n"
            f"Получатель: {payment.card_holder}\n\n"
            f"Введите номер карты:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_payment_settings")]
            ]),
            parse_mode="HTML"
        )
        await state.set_state(UserState.waiting_for_card_number)

    @dp.message(UserState.waiting_for_card_number)
    async def admin_set_card_number(message: Message, state: FSMContext):
        if not admin_manager.is_admin(message.from_user.id):
            await state.clear()
            return
        
        if not message.text or len(message.text.strip()) < 16:
            await message.answer("⚠️ Номер карты должен содержать не менее 16 цифр")
            return
        
        await state.update_data(card_number=message.text.strip())
        
        await message.answer(
            "Введите имя получателя:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_set_card")]
            ])
        )
        await state.set_state(UserState.waiting_for_card_holder)

    @dp.message(UserState.waiting_for_card_holder)
    async def admin_set_card_finish(message: Message, state: FSMContext):
        if not admin_manager.is_admin(message.from_user.id):
            await state.clear()
            return
        
        user_data = await state.get_data()
        card_number = user_data.get('card_number')
        card_holder = message.text.strip()
        
        if not card_holder:
            await message.answer("⚠️ Имя получателя не может быть пустым")
            return
        
        storage_db.update_payment_details(
            card_number=card_number,
            card_holder=card_holder
        )
        
        await message.answer(
            f"✅ Реквизиты карты обновлены!\n\n"
            f"Карта: {card_number}\n"
            f"Получатель: {card_holder}",
            reply_markup=get_back_admin_keyboard()
        )
        await state.clear()

    @dp.callback_query(F.data == "admin_set_crypto")
    async def admin_set_crypto_start(callback: CallbackQuery, state: FSMContext):
        if not admin_manager.is_admin(callback.from_user.id):
            return
        
        payment = storage_db.payment_details
        
        await callback.message.edit_text(
            f"₿ <b>Настройка криптовалюты</b>\n\n"
            f"Текущие реквизиты:\n"
            f"Сеть: {payment.crypto_network}\n"
            f"Монета: {payment.crypto_coin}\n"
            f"Адрес: {payment.crypto_wallet}\n\n"
            f"Введите крипто-адрес (кошелек):",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_payment_settings")]
            ]),
            parse_mode="HTML"
        )
        await state.set_state(UserState.waiting_for_crypto_wallet)

    @dp.message(UserState.waiting_for_crypto_wallet)
    async def admin_set_crypto_wallet(message: Message, state: FSMContext):
        if not admin_manager.is_admin(message.from_user.id):
            await state.clear()
            return
        
        if not message.text or len(message.text.strip()) < 10:
            await message.answer("⚠️ Крипто-адрес должен содержать не менее 10 символов")
            return
        
        await state.update_data(crypto_wallet=message.text.strip())
        
        await message.answer(
            "Введите сеть (например: TRC20, ERC20):",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_set_crypto")]
            ])
        )
        await state.set_state(UserState.waiting_for_crypto_network)

    @dp.message(UserState.waiting_for_crypto_network)
    async def admin_set_crypto_network(message: Message, state: FSMContext):
        if not admin_manager.is_admin(message.from_user.id):
            await state.clear()
            return
        
        if not message.text or len(message.text.strip()) < 3:
            await message.answer("⚠️ Название сети должно содержать не менее 3 символов")
            return
        
        await state.update_data(crypto_network=message.text.strip())
        
        await message.answer(
            "Введите монету (например: USDT, BTC, ETH):",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_set_crypto")]
            ])
        )
        await state.set_state(UserState.waiting_for_crypto_coin)

    @dp.message(UserState.waiting_for_crypto_coin)
    async def admin_set_crypto_finish(message: Message, state: FSMContext):
        if not admin_manager.is_admin(message.from_user.id):
            await state.clear()
            return
        
        user_data = await state.get_data()
        crypto_wallet = user_data.get('crypto_wallet')
        crypto_network = user_data.get('crypto_network')
        crypto_coin = message.text.strip()
        
        if not crypto_coin:
            await message.answer("⚠️ Название монеты не может быть пустым")
            return
        
        storage_db.update_payment_details(
            crypto_wallet=crypto_wallet,
            crypto_network=crypto_network,
            crypto_coin=crypto_coin
        )
        
        await message.answer(
            f"✅ Крипто-реквизиты обновлены!\n\n"
            f"Сеть: {crypto_network}\n"
            f"Монета: {crypto_coin}\n"
            f"Адрес: {crypto_wallet}",
            reply_markup=get_back_admin_keyboard()
        )
        await state.clear()

    @dp.callback_query(F.data == "admin_show_payment")
    async def admin_show_payment(callback: CallbackQuery):
        if not admin_manager.is_admin(callback.from_user.id):
            return
        
        payment = storage_db.payment_details
        
        payment_text = (
            "💳 <b>Текущие реквизиты оплаты</b>\n\n"
            "<b>Банковская карта:</b>\n"
            f"Номер: <code>{payment.card_number}</code>\n"
            f"Получатель: {payment.card_holder}\n\n"
            "<b>Криптовалюта:</b>\n"
            f"Сеть: {payment.crypto_network}\n"
            f"Монета: {payment.crypto_coin}\n"
            f"Адрес: <code>{payment.crypto_wallet}</code>"
        )
        
        await callback.message.edit_text(
            payment_text,
            reply_markup=get_payment_settings_keyboard(),
            parse_mode="HTML"
        )

    # ======================= НАСТРОЙКА ОПЕРАТОРА =======================
    @dp.callback_query(F.data == "admin_operator_settings")
    async def admin_operator_settings(callback: CallbackQuery):
        if not admin_manager.is_admin(callback.from_user.id):
            return
        
        await callback.message.edit_text(
            "👨‍💼 <b>Настройки оператора</b>\n\n"
            "Выберите действие:",
            reply_markup=get_operator_settings_keyboard(),
            parse_mode="HTML"
        )

    @dp.callback_query(F.data == "admin_toggle_operator")
    async def admin_toggle_operator(callback: CallbackQuery):
        if not admin_manager.is_admin(callback.from_user.id):
            return
        
        settings = storage_db.operator_settings
        new_status = not settings.operator_enabled
        
        storage_db.update_operator_settings(operator_enabled=new_status)
        
        status_text = "включена" if new_status else "выключена"
        await callback.answer(f"✅ Кнопка оператора {status_text}", show_alert=True)
        await admin_operator_settings(callback)

    @dp.callback_query(F.data == "admin_set_operator_link")
    async def admin_set_operator_link_start(callback: CallbackQuery, state: FSMContext):
        if not admin_manager.is_admin(callback.from_user.id):
            return
        
        settings = storage_db.operator_settings
        
        await callback.message.edit_text(
            f"🔗 <b>Настройка ссылки оператора</b>\n\n"
            f"Текущая ссылка: {settings.operator_link}\n\n"
            f"Введите новую ссылку (должна начинаться с https://t.me/):",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_operator_settings")]
            ]),
            parse_mode="HTML"
        )
        await state.set_state(UserState.waiting_for_operator_link)

    @dp.message(UserState.waiting_for_operator_link)
    async def admin_set_operator_link_finish(message: Message, state: FSMContext):
        if not admin_manager.is_admin(message.from_user.id):
            await state.clear()
            return
        
        if not message.text.startswith("https://t.me/"):
            await message.answer(
                "⚠️ Ссылка должна начинаться с https://t.me/\n\n"
                "Пример: https://t.me/username",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_operator_settings")]
                ])
            )
            return
        
        storage_db.update_operator_settings(operator_link=message.text.strip())
        
        await message.answer(
            f"✅ Ссылка оператора обновлена: {message.text.strip()}",
            reply_markup=get_back_admin_keyboard()
        )
        await state.clear()

    @dp.callback_query(F.data == "admin_set_operator_text")
    async def admin_set_operator_text_start(callback: CallbackQuery, state: FSMContext):
        if not admin_manager.is_admin(callback.from_user.id):
            return
        
        settings = storage_db.operator_settings
        
        await callback.message.edit_text(
            f"📝 <b>Настройка текста кнопки оператора</b>\n\n"
            f"Текущий текст: {settings.operator_button_text}\n\n"
            f"Введите новый текст для кнопки:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_operator_settings")]
            ]),
            parse_mode="HTML"
        )
        await state.set_state(UserState.waiting_for_operator_button_text)

    @dp.message(UserState.waiting_for_operator_button_text)
    async def admin_set_operator_text_finish(message: Message, state: FSMContext):
        if not admin_manager.is_admin(message.from_user.id):
            await state.clear()
            return
        
        if not message.text or len(message.text.strip()) < 2:
            await message.answer("⚠️ Текст должен содержать не менее 2 символов")
            return
        
        storage_db.update_operator_settings(operator_button_text=message.text.strip())
        
        await message.answer(
            f"✅ Текст кнопки обновлен: {message.text.strip()}",
            reply_markup=get_back_admin_keyboard()
        )
        await state.clear()

    @dp.callback_query(F.data == "admin_show_operator")
    async def admin_show_operator(callback: CallbackQuery):
        if not admin_manager.is_admin(callback.from_user.id):
            return
        
        settings = storage_db.operator_settings
        
        operator_text = (
            "👨‍💼 <b>Настройки оператора</b>\n\n"
            f"<b>Статус:</b> {'✅ Включено' if settings.operator_enabled else '❌ Выключено'}\n"
            f"<b>Ссылка:</b> {settings.operator_link}\n"
            f"<b>Текст кнопки:</b> {settings.operator_button_text}"
        )
        
        await callback.message.edit_text(
            operator_text,
            reply_markup=get_operator_settings_keyboard(),
            parse_mode="HTML"
        )

    # ======================= УПРАВЛЕНИЕ АДМИНАМИ =======================
    @dp.callback_query(F.data == "admin_manage_admins")
    async def admin_manage_admins(callback: CallbackQuery):
        if not admin_manager.is_admin(callback.from_user.id):
            return
        
        admins_list = admin_manager.get_admins_list()
        admins_text = "\n".join([f"👑 {admin_id}" for admin_id in admins_list])
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➕ Добавить админа", callback_data="admin_add_admin")],
            [InlineKeyboardButton(text="➖ Удалить админа", callback_data="admin_remove_admin")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_back")]
        ])
        
        await callback.message.edit_text(
            f"👑 <b>Управление администраторов</b>\n\n"
            f"Текущие админы:\n{admins_text}\n\n"
            f"Всего: {len(admins_list)}\n\n"
            f"Выберите действие:",
            reply_markup=keyboard,
            parse_mode="HTML"
        )

    @dp.callback_query(F.data == "admin_add_admin")
    async def admin_add_admin_start(callback: CallbackQuery, state: FSMContext):
        if not admin_manager.is_admin(callback.from_user.id):
            return
        
        await callback.message.edit_text(
            "Введите ID пользователя, которого хотите сделать админом:\n\n"
            "Пользователь может узнать свой ID через команду /id",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_manage_admins")]
            ])
        )
        await state.set_state(UserState.waiting_for_new_admin_id)

    @dp.message(UserState.waiting_for_new_admin_id)
    async def admin_add_admin_finish(message: Message, state: FSMContext):
        if not admin_manager.is_admin(message.from_user.id):
            await state.clear()
            return
        
        try:
            new_admin_id = int(message.text)
            
            if admin_manager.add_admin(new_admin_id, message.from_user.id):
                await message.answer(f"✅ Пользователь {new_admin_id} добавлен в админы", reply_markup=get_back_admin_keyboard())
                
                try:
                    await bot.send_message(
                        new_admin_id,
                        "🎉 Поздравляем! Вы были назначены администратором бота.\n"
                        "Используйте команду /admin для доступа к панели управления."
                    )
                except:
                    pass
            else:
                await message.answer("❌ Не удалось добавить админа. Возможно, у вас нет прав.", reply_markup=get_back_admin_keyboard())
        except ValueError:
            await message.answer("⚠️ Пожалуйста, введите корректный ID (число)", reply_markup=get_back_admin_keyboard())
        
        await state.clear()

    @dp.callback_query(F.data == "admin_remove_admin")
    async def admin_remove_admin_start(callback: CallbackQuery, state: FSMContext):
        if not admin_manager.is_admin(callback.from_user.id):
            return
        
        admins_list = admin_manager.get_admins_list()
        admins_for_removal = [admin_id for admin_id in admins_list if admin_id != callback.from_user.id]
        
        if not admins_for_removal:
            await callback.message.edit_text(
                "⚠️ Нет других админов для удаления",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_manage_admins")]
                ])
            )
            return
        
        admins_text = "\n".join([f"👑 {admin_id}" for admin_id in admins_for_removal])
        
        await callback.message.edit_text(
            f"Введите ID админа для удаления:\n\n"
            f"<b>Доступные админы:</b>\n{admins_text}\n\n"
            f"Вы не можете удалить себя.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_manage_admins")]
            ]),
            parse_mode="HTML"
        )
        await state.set_state(UserState.waiting_for_remove_admin_id)

    @dp.message(UserState.waiting_for_remove_admin_id)
    async def admin_remove_admin_finish(message: Message, state: FSMContext):
        if not admin_manager.is_admin(message.from_user.id):
            await state.clear()
            return
        
        try:
            admin_id_to_remove = int(message.text)
            
            if admin_manager.remove_admin(admin_id_to_remove, message.from_user.id):
                await message.answer(f"✅ Админ {admin_id_to_remove} удален", reply_markup=get_back_admin_keyboard())
            else:
                await message.answer("❌ Не удалось удалить админа. Возможно, у вас нет прав или это вы сами.", reply_markup=get_back_admin_keyboard())
        except ValueError:
            await message.answer("⚠️ Пожалуйста, введите корректный ID (число)", reply_markup=get_back_admin_keyboard())
        
        await state.clear()

    # ======================= РАССЫЛКА =======================
    @dp.callback_query(F.data == "admin_broadcast")
    async def admin_broadcast_start(callback: CallbackQuery, state: FSMContext):
        if not admin_manager.is_admin(callback.from_user.id):
            return
        
        user_count = len(storage_db.users)
        
        await callback.message.edit_text(
            f"📢 <b>Рассылка сообщений</b>\n\n"
            f"Всего пользователей: {user_count}\n\n"
            f"Введите сообщение для рассылки:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_back")]
            ]),
            parse_mode="HTML"
        )
        await state.set_state(UserState.waiting_for_broadcast_message)

    @dp.message(UserState.waiting_for_broadcast_message)
    async def admin_broadcast_send(message: Message, state: FSMContext):
        if not admin_manager.is_admin(message.from_user.id):
            await state.clear()
            return
        
        users = storage_db.users
        
        if not users:
            await message.answer("⚠️ Нет пользователей для рассылки")
            await state.clear()
            return
        
        confirm_keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Да, отправить", callback_data="confirm_broadcast")],
            [InlineKeyboardButton(text="❌ Нет, отменить", callback_data="cancel_broadcast")]
        ])
        
        await state.update_data(broadcast_message=message.text)
        await message.answer(
            f"📢 <b>Подтверждение рассылки</b>\n\n"
            f"Получателей: {len(users)} пользователей\n\n"
            f"<b>Сообщение:</b>\n{message.text}\n\n"
            f"Отправить рассылку?",
            reply_markup=confirm_keyboard,
            parse_mode="HTML"
        )

    @dp.callback_query(F.data == "confirm_broadcast")
    async def confirm_broadcast(callback: CallbackQuery, state: FSMContext):
        if not admin_manager.is_admin(callback.from_user.id):
            return
        
        user_data = await state.get_data()
        message_text = user_data.get('broadcast_message')
        users = storage_db.users
        
        if not message_text or not users:
            await callback.message.edit_text("❌ Ошибка: нет сообщения или пользователей")
            await state.clear()
            return
        
        progress_msg = await callback.message.edit_text(f"📢 Начинаю рассылку для {len(users)} пользователей...")
        
        success = 0
        failed = 0
        
        for i, user in enumerate(users, 1):
            try:
                await bot.send_message(user['id'], message_text)
                success += 1
                
                if i % 10 == 0:
                    await progress_msg.edit_text(
                        f"📢 Рассылка: отправлено {i} из {len(users)}\n"
                        f"✅ Успешно: {success}\n"
                        f"❌ Ошибок: {failed}"
                    )
                
                await asyncio.sleep(0.1)
                
            except Exception as e:
                failed += 1
                logger.error(f"Не удалось отправить сообщение пользователю {user.get('id')}: {e}")
        
        result_text = (
            f"📢 <b>Рассылка завершена</b>\n\n"
            f"📊 <b>Результаты:</b>\n"
            f"• Всего получателей: {len(users)}\n"
            f"• ✅ Успешно отправлено: {success}\n"
            f"• ❌ Не удалось отправить: {failed}\n\n"
            f"📈 <b>Успешность:</b> {success/len(users)*100:.1f}%"
        )
        
        await progress_msg.edit_text(
            result_text,
            parse_mode="HTML",
            reply_markup=get_back_admin_keyboard()
        )
        await state.clear()

    @dp.callback_query(F.data == "cancel_broadcast")
    async def cancel_broadcast(callback: CallbackQuery, state: FSMContext):
        if not admin_manager.is_admin(callback.from_user.id):
            return
        
        await callback.message.edit_text(
            "❌ Рассылка отменена",
            reply_markup=get_back_admin_keyboard()
        )
        await state.clear()

    # ======================= СТАТИСТИКА =======================
    @dp.callback_query(F.data == "admin_stats")
    async def admin_stats(callback: CallbackQuery):
        if not admin_manager.is_admin(callback.from_user.id):
            return
        
        active_cities = len([c for c in storage_db.cities.values() if c.is_active])
        active_categories = len([c for c in storage_db.categories.values() if c.is_active])
        active_products = len([p for p in storage_db.products.values() if p.is_active])
        
        orders = storage_db.orders
        total_orders = len(orders)
        pending_orders = len([o for o in orders.values() if o.status == "pending"])
        confirmed_orders = len([o for o in orders.values() if o.status == "confirmed"])
        
        total_revenue = sum([o.price for o in orders.values() if o.status == "confirmed"])
        
        total_users = len(storage_db.users)
        
        cities_list = list(storage_db.cities.values())
        cities_list.sort(key=lambda x: x.order)
        cities_text = "\n".join([f"{city.order}. {city.name}" for city in cities_list[:10]])
        
        stats_text = (
            f"📊 <b>Статистика магазина</b>\n\n"
            f"🏙️ <b>Города:</b> {active_cities} (всего: {len(storage_db.cities)})\n"
            f"📂 <b>Категории:</b> {active_categories} (всего: {len(storage_db.categories)})\n"
            f"🛍️ <b>Товары:</b> {active_products} (всего: {len(storage_db.products)})\n\n"
            f"👥 <b>Пользователи:</b> {total_users}\n\n"
            f"🛒 <b>Заказы:</b>\n"
            f"• Всего: {total_orders}\n"
            f"• ⏳ Ожидают: {pending_orders}\n"
            f"• ✅ Подтверждены: {confirmed_orders}\n\n"
            f"💰 <b>Выручка:</b> {total_revenue:.2f}₽\n\n"
            f"👑 <b>Админы:</b> {len(admin_manager.get_admins_list())}\n\n"
            f"📍 <b>Порядок городов (первые 10):</b>\n{cities_text}"
        )
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Обновить", callback_data="admin_stats")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_back")]
        ])
        
        await callback.message.edit_text(
            stats_text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )

    # ======================= КНОПКА НАЗАД В АДМИН-ПАНЕЛИ =======================
    @dp.callback_query(F.data == "admin_back")
    async def admin_back(callback: CallbackQuery):
        if not admin_manager.is_admin(callback.from_user.id):
            return
        
        await callback.message.edit_text(
            "👑 <b>Панель администратора</b>\n\n"
            "Выберите действие:",
            reply_markup=get_admin_keyboard(),
            parse_mode="HTML"
        )

    logger.info("✅ Все хэндлеры зарегистрированы")

# ======================= ВЕБХУК И ЗАПУСК =======================
@asynccontextmanager
async def lifespan(app):
    """Функция жизненного цикла приложения"""
    global bot, dp, storage_db, admin_manager
    
    logger.info("🚀 Инициализация бота и хранилища...")
    
    # Создаем экземпляры бота и диспетчера
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    storage_db = DataStorage()
    admin_manager = AdminManager()
    
    # Регистрируем все хэндлеры
    register_handlers(dp)
    
    logger.info(f"✅ Бот инициализирован. Токен: {BOT_TOKEN[:10]}...")
    logger.info(f"✅ ID владельца: {YOUR_TELEGRAM_ID}")
    logger.info(f"✅ URL сервиса: {RENDER_EXTERNAL_URL}")
    
    # Устанавливаем вебхук
    webhook_url = f"{RENDER_EXTERNAL_URL}/webhook"
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        logger.info("✅ Старый вебхук удален")
        
        await bot.set_webhook(
            webhook_url,
            allowed_updates=dp.resolve_used_update_types(),
            drop_pending_updates=True
        )
        logger.info(f"✅ Вебхук установлен на {webhook_url}")
    except Exception as e:
        logger.error(f"❌ Ошибка при установке вебхука: {e}")
        raise
    
    yield
    
    logger.info("🛑 Остановка бота...")
    try:
        await bot.delete_webhook()
        await bot.session.close()
        logger.info("✅ Вебхук удален, сессия закрыта")
    except Exception as e:
        logger.error(f"❌ Ошибка при остановке: {e}")

async def telegram_webhook(request):
    """Обработчик входящих запросов от Telegram"""
    global bot, dp
    try:
        update_data = await request.json()
        update = Update.model_validate(update_data, context={"bot": bot})
        await dp.feed_update(bot, update)
        return JSONResponse({"ok": True})
    except Exception as e:
        logger.error(f"❌ Ошибка при обработке апдейта: {e}", exc_info=True)
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

async def health_check(request):
    """Эндпоинт для проверки здоровья Render"""
    return PlainTextResponse("OK")

# Создаем Starlette приложение
routes = [
    Route("/webhook", telegram_webhook, methods=["POST"]),
    Route("/health", health_check, methods=["GET"]),
    Route("/", health_check, methods=["GET"]),
]

starlette_app = Starlette(routes=routes, lifespan=lifespan)

# ======================= ТОЧКА ВХОДА =======================
if __name__ == "__main__":
    logger.info(f"🚀 Запуск сервера на порту {PORT}")
    uvicorn.run(starlette_app, host="0.0.0.0", port=PORT)
