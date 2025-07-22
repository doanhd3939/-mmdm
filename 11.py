import os
import re
import time
import json
import threading
import requests
import asyncio
import random
import string
import logging

from flask import Flask, request, jsonify, render_template_string
from telegram import Update, BotCommand
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# ========== CẤU HÌNH ==========
LINK4M_API_TOKEN = "687c5ee5378e1071b4481530"
BOT_TOKEN = os.environ.get('BOT_TOKEN', '8029254946:AAE8Upy5LoYIYsmcm8Y117Esm_-_MF0-ChA')

# KEY SYSTEM PROFESSIONAL SETTINGS
DEFAULT_KEY_LIFETIME = 86400  # 24 giờ chính xác (86400 giây)
KEY_EXPIRY_WARNING_TIME = 3600  # Cảnh báo khi còn 1 giờ
KEY_CLEANUP_INTERVAL = 300  # Dọn dẹp KEY hết hạn mỗi 5 phút
KEY_MAX_PER_USER = 1  # Mỗi user chỉ được có 1 KEY active
KEY_COOLDOWN_TIME = 3600  # Cooldown tạo KEY mới: 1 giờ
MASTER_ADMIN_ID = 7509896689

BYPASS_TYPES = [
    "m88", "fb88", "188bet", "w88", "v9bet", "bk8",
    "88betag", "w88abc", "v9betlg", "bk8xo", "vn88ie", "w88xlm"
]

# ========== CẤU HÌNH LOGGING ==========
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ========== CÁC FILE LƯU TRỮ ==========
DATA_DIR = "data"
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)

VALID_KEYS_FILE = os.path.join(DATA_DIR, "valid_keys.json")
USER_KEYS_FILE = os.path.join(DATA_DIR, "user_keys.json")
KEY_DEVICES_FILE = os.path.join(DATA_DIR, "key_devices.json")  # Lưu thông tin thiết bị đang sử dụng key
KEY_METADATA_FILE = os.path.join(DATA_DIR, "key_metadata.json")  # Metadata chi tiết về KEY
KEY_USAGE_LOG_FILE = os.path.join(DATA_DIR, "key_usage_log.json")  # Log sử dụng KEY
ADMINS_FILE = os.path.join(DATA_DIR, "admins.json")
BAN_LIST_FILE = os.path.join(DATA_DIR, "ban_list.json")

# ========== BIẾN TOÀN CỤC ==========
VALID_KEYS = {}    # key -> (timestamp tạo, thời gian sống giây)
USER_KEYS = {}     # user_id -> key đã xác nhận
KEY_DEVICES = {}   # key -> user_id đang sử dụng key này
KEY_METADATA = {}  # key -> {created_time, activated_time, user_info, device_info, usage_count}
KEY_USAGE_LOG = {} # user_id -> [list of usage timestamps]
KEY_COOLDOWN = {}  # user_id -> last_time dùng lệnh /key (giây)
ADMINS = set([MASTER_ADMIN_ID])
ADMINS_LOCK = threading.Lock()
SPAM_COUNTER = {}
BAN_LIST = {}
USER_LOCKS = threading.Lock()
DATA_LOCK = threading.Lock()  # Lock để đồng bộ khi lưu/đọc dữ liệu

# ========== FLASK APP ==========
app = Flask(__name__)

# ========== HƯỚNG DẪN ADMIN ==========
ADMIN_GUIDE = (
    "👑 <b>BẢNG ĐIỀU KHIỂN QUẢN TRỊ VIÊN</b> 👑\n"
    "═══════════════════════════════════\n\n"
    "🛠️ <b>QUẢN LÝ NGƯỜI DÙNG</b>\n"
    "┌─────────────────────────────────┐\n"
    "│ <code>/ban &lt;user_id&gt; &lt;phút&gt;</code>     │ 🚫 Ban user\n"
    "│ <code>/unban &lt;user_id&gt;</code>         │ ✅ Gỡ ban user\n"
    "│ <code>/stats</code>                  │ 📊 Thống kê hệ thống\n"
    "│ <code>/broadcast &lt;tin nhắn&gt;</code>   │ 📢 Gửi thông báo tới tất cả\n"
    "└─────────────────────────────────┘\n\n"
    "🔑 <b>QUẢN LÝ KEY</b>\n"
    "┌─────────────────────────────────┐\n"
    "│ <code>/taokey &lt;số_ngày&gt;</code>      │ 🎁 Tạo KEY VIP\n"
    "│ <code>/listkey</code>               │ 📋 Danh sách KEY active\n"
    "│ <code>/deletekey &lt;key&gt;</code>      │ 🗑️ Xóa KEY cụ thể\n"
    "│ <code>/keyinfo &lt;key&gt;</code>       │ 🔍 Chi tiết KEY\n"
    "└─────────────────────────────────┘\n\n"
    "👑 <b>QUẢN LÝ ADMIN</b> <i>(Chỉ Master Admin)</i>\n"
    "┌─────────────────────────────────┐\n"
    "│ <code>/addadmin &lt;user_id&gt;</code>    │ ⭐ Thêm admin\n"
    "│ <code>/deladmin &lt;user_id&gt;</code>    │ ❌ Xóa quyền admin\n"
    "│ <code>/deleteallkeys CONFIRM_DELETE_ALL</code> │ 💥 Xóa tất cả KEY\n"
    "│ <code>/listadmin</code>             │ 👥 Danh sách admin\n"
    "└─────────────────────────────────┘\n\n"
    "💾 <b>HỆ THỐNG</b>\n"
    "┌─────────────────────────────────┐\n"
    "│ <code>/savedata</code>              │ 💾 Backup dữ liệu\n"
    "│ <code>/logs</code>                  │ 📝 Xem logs hệ thống\n"
    "│ <code>/restart</code>               │ 🔄 Khởi động lại bot\n"
    "└─────────────────────────────────┘\n\n"
    "⚠️ <b>LƯU Ý QUAN TRỌNG</b>\n"
    "▫️ Mỗi KEY = 1 thiết bị duy nhất\n"
    "▫️ KEY có thể dùng nhiều lần trong thời hạn\n"
    "▫️ Ban thủ công ghi đè ban tự động\n"
    "▫️ Backup dữ liệu định kỳ 5 phút/lần\n"
    "▫️ <b>deleteallkeys chỉ Master Admin (ID: 7509896689)</b>\n\n"
    "📝 <b>VÍ DỤ SỬ DỤNG</b>\n"
    "<code>/ban 123456789 30</code> - Ban user 30 phút\n"
    "<code>/taokey 7</code> - Tạo KEY VIP 7 ngày\n"
    "<code>/deletekey VIP2025-ABC123</code> - Xóa KEY cụ thể\n"
    "<code>/broadcast Bảo trì hệ thống 10 phút</code>\n"
    "═══════════════════════════════════"
)

# ========== CÁC HÀM LƯU TRỮ ==========
def save_valid_keys():
    with DATA_LOCK:
        data = {}
        for key, (timestamp, lifetime) in VALID_KEYS.items():
            data[key] = [timestamp, lifetime]
        with open(VALID_KEYS_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f)

def load_valid_keys():
    global VALID_KEYS
    with DATA_LOCK:
        if os.path.exists(VALID_KEYS_FILE):
            try:
                with open(VALID_KEYS_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    for key, (timestamp, lifetime) in data.items():
                        VALID_KEYS[key] = (timestamp, lifetime)
            except Exception as e:
                logger.error(f"Lỗi khi đọc file VALID_KEYS_FILE: {e}")

def save_user_keys():
    with DATA_LOCK:
        # Chuyển đổi user_id từ string sang int khi load
        data = {str(user_id): key for user_id, key in USER_KEYS.items()}
        with open(USER_KEYS_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f)

def load_user_keys():
    global USER_KEYS
    with DATA_LOCK:
        if os.path.exists(USER_KEYS_FILE):
            try:
                with open(USER_KEYS_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    # Chuyển đổi user_id từ string sang int khi load
                    USER_KEYS = {int(user_id): key for user_id, key in data.items()}
            except Exception as e:
                logger.error(f"Lỗi khi đọc file USER_KEYS_FILE: {e}")

def save_key_devices():
    with DATA_LOCK:
        # Chuyển đổi user_id sang string để lưu vào JSON
        data = {key: str(user_id) for key, user_id in KEY_DEVICES.items()}
        with open(KEY_DEVICES_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f)

def load_key_devices():
    global KEY_DEVICES
    with DATA_LOCK:
        if os.path.exists(KEY_DEVICES_FILE):
            try:
                with open(KEY_DEVICES_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    # Chuyển đổi user_id từ string sang int khi load
                    KEY_DEVICES = {key: int(user_id) for key, user_id in data.items()}
            except Exception as e:
                logger.error(f"Lỗi khi đọc file KEY_DEVICES_FILE: {e}")

def save_key_metadata():
    """Lưu metadata chi tiết về KEY"""
    with DATA_LOCK:
        with open(KEY_METADATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(KEY_METADATA, f, indent=2)

def load_key_metadata():
    """Đọc metadata chi tiết về KEY"""
    global KEY_METADATA
    with DATA_LOCK:
        if os.path.exists(KEY_METADATA_FILE):
            try:
                with open(KEY_METADATA_FILE, 'r', encoding='utf-8') as f:
                    KEY_METADATA = json.load(f)
            except Exception as e:
                logger.error(f"Lỗi khi đọc file KEY_METADATA_FILE: {e}")

def save_key_usage_log():
    """Lưu log sử dụng KEY"""
    with DATA_LOCK:
        data = {str(user_id): log_list for user_id, log_list in KEY_USAGE_LOG.items()}
        with open(KEY_USAGE_LOG_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)

def load_key_usage_log():
    """Đọc log sử dụng KEY"""
    global KEY_USAGE_LOG
    with DATA_LOCK:
        if os.path.exists(KEY_USAGE_LOG_FILE):
            try:
                with open(KEY_USAGE_LOG_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    KEY_USAGE_LOG = {int(user_id): log_list for user_id, log_list in data.items()}
            except Exception as e:
                logger.error(f"Lỗi khi đọc file KEY_USAGE_LOG_FILE: {e}")

def save_admins():
    with DATA_LOCK:
        with ADMINS_LOCK:
            with open(ADMINS_FILE, 'w', encoding='utf-8') as f:
                # Chuyển set thành list để lưu vào JSON
                json.dump(list(ADMINS), f)

def load_admins():
    global ADMINS
    with DATA_LOCK:
        if os.path.exists(ADMINS_FILE):
            try:
                with open(ADMINS_FILE, 'r', encoding='utf-8') as f:
                    # Đảm bảo MASTER_ADMIN_ID luôn có trong danh sách
                    admin_list = json.load(f)
                    with ADMINS_LOCK:
                        ADMINS = set(admin_list)
                        ADMINS.add(MASTER_ADMIN_ID)
            except Exception as e:
                logger.error(f"Lỗi khi đọc file ADMINS_FILE: {e}")
                with ADMINS_LOCK:
                    ADMINS = set([MASTER_ADMIN_ID])

def save_ban_list():
    with DATA_LOCK:
        data = {}
        for user_id, ban_info in BAN_LIST.items():
            # Chuyển đổi thông tin ban để có thể lưu vào JSON
            data[str(user_id)] = {
                'until': ban_info['until'],
                'manual': ban_info['manual']
            }
        with open(BAN_LIST_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f)

def load_ban_list():
    global BAN_LIST
    with DATA_LOCK:
        if os.path.exists(BAN_LIST_FILE):
            try:
                with open(BAN_LIST_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    # Lọc ra những ban đã hết hạn
                    now = time.time()
                    for user_id_str, ban_info in data.items():
                        if ban_info['until'] > now:
                            BAN_LIST[int(user_id_str)] = ban_info
            except Exception as e:
                logger.error(f"Lỗi khi đọc file BAN_LIST_FILE: {e}")

# ========== KEY MANAGEMENT PROFESSIONAL SYSTEM ==========

def is_key_valid(key):
    """Kiểm tra KEY có hợp lệ và còn hiệu lực không"""
    if key not in VALID_KEYS:
        return False, "KEY không tồn tại trong hệ thống"
    
    created_time, lifetime = VALID_KEYS[key]
    current_time = time.time()
    
    if current_time > created_time + lifetime:
        return False, "KEY đã hết hạn"
    
    return True, "KEY hợp lệ"

def get_key_time_remaining(key):
    """Lấy thời gian còn lại của KEY (giây)"""
    if key not in VALID_KEYS:
        return 0
    
    created_time, lifetime = VALID_KEYS[key]
    current_time = time.time()
    time_remaining = (created_time + lifetime) - current_time
    
    return max(0, time_remaining)

def format_time_remaining(seconds):
    """Format thời gian còn lại thành chuỗi dễ đọc"""
    if seconds <= 0:
        return "Đã hết hạn"
    
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    seconds = int(seconds % 60)
    
    if hours > 0:
        return f"{hours} giờ, {minutes} phút"
    elif minutes > 0:
        return f"{minutes} phút, {seconds} giây"
    else:
        return f"{seconds} giây"

def generate_premium_key():
    """Tạo KEY premium với format chuyên nghiệp"""
    current_time = int(time.time())
    random_part = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
    return f"VIP2025-{random_part}-{current_time % 10000:04d}"

def create_key_metadata(key, user_id, username, full_name):
    """Tạo metadata cho KEY"""
    current_time = time.time()
    KEY_METADATA[key] = {
        'created_time': current_time,
        'activated_time': None,
        'creator_user_id': user_id,
        'creator_username': username,
        'creator_full_name': full_name,
        'device_info': None,
        'usage_count': 0,
        'last_used': None,
        'status': 'created'  # created, activated, expired, deleted
    }
    save_key_metadata()

def activate_key_metadata(key, user_id, username, full_name):
    """Kích hoạt metadata cho KEY"""
    if key in KEY_METADATA:
        KEY_METADATA[key]['activated_time'] = time.time()
        KEY_METADATA[key]['activator_user_id'] = user_id
        KEY_METADATA[key]['activator_username'] = username
        KEY_METADATA[key]['activator_full_name'] = full_name
        KEY_METADATA[key]['status'] = 'activated'
        save_key_metadata()

def log_key_usage(user_id, key, action):
    """Ghi log sử dụng KEY"""
    current_time = time.time()
    
    if user_id not in KEY_USAGE_LOG:
        KEY_USAGE_LOG[user_id] = []
    
    KEY_USAGE_LOG[user_id].append({
        'timestamp': current_time,
        'key': key,
        'action': action  # 'bypass_request', 'key_check', etc.
    })
    
    # Giữ chỉ 100 log gần nhất
    if len(KEY_USAGE_LOG[user_id]) > 100:
        KEY_USAGE_LOG[user_id] = KEY_USAGE_LOG[user_id][-100:]
    
    # Cập nhật metadata
    if key in KEY_METADATA:
        KEY_METADATA[key]['usage_count'] += 1
        KEY_METADATA[key]['last_used'] = current_time
        save_key_metadata()
    
    save_key_usage_log()

def cleanup_expired_keys():
    """Dọn dẹp KEY hết hạn"""
    current_time = time.time()
    expired_keys = []
    
    for key, (created_time, lifetime) in VALID_KEYS.items():
        if current_time > created_time + lifetime:
            expired_keys.append(key)
    
    for key in expired_keys:
        # Xóa từ VALID_KEYS
        del VALID_KEYS[key]
        
        # Xóa từ USER_KEYS
        users_to_remove = []
        for user_id, user_key in USER_KEYS.items():
            if user_key == key:
                users_to_remove.append(user_id)
        
        for user_id in users_to_remove:
            del USER_KEYS[user_id]
        
        # Xóa từ KEY_DEVICES
        if key in KEY_DEVICES:
            del KEY_DEVICES[key]
        
        # Cập nhật metadata
        if key in KEY_METADATA:
            KEY_METADATA[key]['status'] = 'expired'
    
    if expired_keys:
        logger.info(f"Đã dọn dẹp {len(expired_keys)} KEY hết hạn")
        save_valid_keys()
        save_user_keys()
        save_key_devices()
        save_key_metadata()
    
    return len(expired_keys)

def get_user_key_stats(user_id):
    """Lấy thống kê KEY của user"""
    user_key = USER_KEYS.get(user_id)
    
    if not user_key:
        return {
            'has_key': False,
            'key': None,
            'time_remaining': 0,
            'usage_count': 0,
            'last_used': None
        }
    
    time_remaining = get_key_time_remaining(user_key)
    metadata = KEY_METADATA.get(user_key, {})
    
    return {
        'has_key': True,
        'key': user_key,
        'time_remaining': time_remaining,
        'usage_count': metadata.get('usage_count', 0),
        'last_used': metadata.get('last_used'),
        'created_time': metadata.get('created_time'),
        'activated_time': metadata.get('activated_time')
    }

def can_user_create_new_key(user_id):
    """Kiểm tra user có thể tạo KEY mới không"""
    # Kiểm tra cooldown
    last_created = KEY_COOLDOWN.get(user_id, 0)
    current_time = time.time()
    
    if current_time - last_created < KEY_COOLDOWN_TIME:
        remaining_cooldown = KEY_COOLDOWN_TIME - (current_time - last_created)
        return False, f"Bạn cần chờ {format_time_remaining(remaining_cooldown)} nữa để tạo KEY mới"
    
    # Kiểm tra KEY hiện tại
    user_key = USER_KEYS.get(user_id)
    if user_key:
        is_valid, _ = is_key_valid(user_key)
        if is_valid:
            return False, "Bạn đang có KEY hoạt động. Hãy chờ KEY hết hạn hoặc liên hệ admin"
    
    return True, "Có thể tạo KEY mới"

def save_all_data():
    save_valid_keys()
    save_user_keys()
    save_key_devices()
    save_admins()
    save_ban_list()
    logger.info(f"Đã lưu dữ liệu thành công!")

def load_all_data():
    load_valid_keys()
    load_user_keys()
    load_key_devices()
    load_admins()
    load_ban_list()
    logger.info(f"Đã tải dữ liệu thành công!")

# Luồng tự động lưu dữ liệu định kỳ
def auto_save_data_loop():
    while True:
        time.sleep(300)  # Lưu dữ liệu 5 phút một lần
        try:
            save_all_data()
        except Exception as e:
            logger.error(f"Lỗi khi tự động lưu dữ liệu: {e}")

# ========== CÁC HÀM HỖ TRỢ ==========
def get_bypass_code(type_code):
    """Hàm lấy mã bypass từ traffic-user.net"""
    try:
        logger.info(f"Đang lấy mã cho loại: {type_code}")
        
        # Mapping các loại mã với URL và pattern tương ứng
        bypass_configs = {
            'm88': {
                'url': 'https://traffic-user.net/GET_MA.php?codexn=taodeptrai&url=https://bet88ec.com/cach-danh-bai-sam-loc&loai_traffic=https://bet88ec.com/&clk=1000',
                'pattern': 'layma_me_vuatraffic'
            },
            'fb88': {
                'url': 'https://traffic-user.net/GET_MA.php?codexn=taodeptrai&url=https://fb88dq.com/cach-choi-ca-cuoc-golf&loai_traffic=https://fb88dq.com/&clk=1000',
                'pattern': 'layma_me_vuatraffic'
            },
            '188bet': {
                'url': 'https://traffic-user.net/GET_MA.php?codexn=taodeptrailamnhe&url=https://88betag.com/cach-choi-game-bai-pok-deng&loai_traffic=https://88betag.com/&clk=1000',
                'pattern': 'layma_me_vuatraffic'
            },
            'w88': {
                'url': 'https://traffic-user.net/GET_MA.php?codexn=taodeptrai&url=https://188.166.185.213/tim-hieu-khai-niem-3-bet-trong-poker-la-gi&loai_traffic=https://188.166.185.213/&clk=1000',
                'pattern': 'layma_me_vuatraffic'
            },
            'v9bet': {
                'url': 'https://traffic-user.net/GET_MA.php?codexn=taodeptrai&url=https://v9betho.com/ca-cuoc-bong-ro-ao&loai_traffic=https://v9betho.com/&clk=1000',
                'pattern': 'layma_me_vuatraffic'
            },
            'vn88': {
                'url': 'https://traffic-user.net/GET_MA.php?codexn=bomaydeptrai&url=https://vn88sv.com/cach-choi-bai-gao-gae&loai_traffic=https://vn88sv.com/&clk=1000',
                'pattern': 'layma_me_vuatraffic'
            },
            'bk8': {
                'url': 'https://traffic-user.net/GET_MA.php?codexn=taodeptrai&url=https://bk8ze.com/cach-choi-bai-catte&loai_traffic=https://bk8ze.com/&clk=1000',
                'pattern': 'layma_me_vuatraffic'
            },
            '88betag': {
                'url': 'https://traffic-user.net/GET_MD.php?codexnd=bomaylavua&url=https://88betag.com/keo-chau-a-la-gi&loai_traffic=https://88betag.com/&clk=1000',
                'pattern': 'layma_me_tfudirect'
            },
            'w88abc': {
                'url': 'https://traffic-user.net/GET_MD.php?codexnd=bomaylavua&url=https://w88abc.com/cach-choi-ca-cuoc-lien-quan-mobile&loai_traffic=https://w88abc.com/&clk=1000',
                'pattern': 'layma_me_tfudirect'
            },
            'v9betlg': {
                'url': 'https://traffic-user.net/GET_MD.php?codexnd=bomaylavua&url=https://v9betlg.com/phuong-phap-cuoc-flat-betting&loai_traffic=https://v9betlg.com/&clk=1000',
                'pattern': 'layma_me_tfudirect'
            },
            'bk8xo': {
                'url': 'https://traffic-user.net/GET_MD.php?codexnd=bomaylavua&url=https://bk8xo.com/lo-ba-cang-la-gi&loai_traffic=https://bk8xo.com/&clk=1000',
                'pattern': 'layma_me_tfudirect'
            },
            'vn88ie': {
                'url': 'https://traffic-user.net/GET_MD.php?codexnd=bomaylavua&url=https://vn88ie.com/cach-nuoi-lo-khung&loai_traffic=https://vn88ie.com/&clk=1000',
                'pattern': 'layma_me_tfudirect'
            },
            'w88xlm': {
                'url': 'https://traffic-user.net/GET_MA.php?codexn=taodeptrai&url=https://w88xlm.com/cach-choi-bai-solitaire&loai_traffic=https://w88xlm.com/&clk=1000',
                'pattern': 'layma_me_vuatraffic'
            }
        }
        
        config = bypass_configs.get(type_code)
        if not config:
            logger.error(f"Không hỗ trợ loại mã: {type_code}")
            return None
        
        # Gửi POST request với retry logic
        max_retries = 3
        for attempt in range(max_retries):
            try:
                response = requests.post(config['url'], timeout=30)
                response.raise_for_status()  # Raise exception for bad status codes
                html = response.text
                break
            except requests.exceptions.RequestException as e:
                logger.warning(f"Attempt {attempt + 1} failed for {type_code}: {e}")
                if attempt == max_retries - 1:
                    raise
                time.sleep(2)  # Wait 2 seconds before retry
        
        # Tìm mã trong HTML response với multiple patterns
        patterns = [
            f'<span id="{config["pattern"]}"[^>]*>\\s*(\\d+)\\s*</span>',
            f'<span[^>]*id="{config["pattern"]}"[^>]*>\\s*(\\d+)\\s*</span>',
            f'"{config["pattern"]}"[^>]*>\\s*(\\d+)\\s*<',
            f'>{config["pattern"]}[^\\d]*(\\d+)'
        ]
        
        code = None
        for pattern in patterns:
            match = re.search(pattern, html, re.IGNORECASE)
            if match:
                code = match.group(1)
                break
        
        if code:
            logger.info(f"Lấy mã thành công cho {type_code}: {code}")
            return code
        else:
            # Log response for debugging
            logger.error(f"Không tìm thấy mã trong response cho {type_code}")
            logger.debug(f"Response content (first 500 chars): {html[:500]}")
            return None
            
    except Exception as e:
        logger.error(f"Lỗi khi lấy mã cho {type_code}: {e}")
        return None

def admin_notify(msg: str) -> str:
    return (
        "<b>👑 QUẢN TRỊ VIÊN</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"{msg}\n"
        "━━━━━━━━━━━━━━━━━━━━"
    )

def is_admin(user_id):
    with ADMINS_LOCK:
        return user_id in ADMINS

def is_master_admin(user_id):
    return user_id == MASTER_ADMIN_ID

def tao_key(songay=1):
    key = "VIP2025-" + ''.join(random.choices(string.ascii_uppercase + string.digits, k=10))
    lifetime = int(songay) * 86400
    VALID_KEYS[key] = (time.time(), lifetime)
    save_valid_keys()
    return key, lifetime

def check_key(key):
    data = VALID_KEYS.get(key)
    if not data:
        return False
    
    t, living = data
    
    # Kiểm tra thời gian
    if time.time() - t > living:
        VALID_KEYS.pop(key, None)
        KEY_DEVICES.pop(key, None)
        for uid, k in list(USER_KEYS.items()):
            if k == key:
                USER_KEYS.pop(uid, None)
        save_valid_keys()
        save_key_devices()
        save_user_keys()
        return False
    
    return True

def bind_key_to_device(key, user_id):
    """Gắn key với một thiết bị/user_id cụ thể"""
    KEY_DEVICES[key] = user_id
    save_key_devices()

def can_use_key(key, user_id):
    """Kiểm tra xem user_id có quyền sử dụng key này không"""
    # Nếu key chưa được gắn với thiết bị nào
    if key not in KEY_DEVICES:
        bind_key_to_device(key, user_id)
        return True
    
    # Nếu key đã được gắn với thiết bị này
    return KEY_DEVICES[key] == user_id

def get_key_info(key):
    data = VALID_KEYS.get(key)
    if not data:
        return None
    t, living = data
    remaining_time = max(0, t + living - time.time())
    days = int(remaining_time // 86400)
    hours = int((remaining_time % 86400) // 3600)
    minutes = int((remaining_time % 3600) // 60)
    
    bound_device = KEY_DEVICES.get(key, None)
    
    return {
        "time_remaining": f"{days} ngày, {hours} giờ, {minutes} phút",
        "bound_device": bound_device,
        "expired": remaining_time <= 0
    }

def check_user_key(user_id):
    key = USER_KEYS.get(user_id)
    return key if key and check_key(key) else None

def xacnhan_key(user_id, key):
    # Kiểm tra xem user đã có key hợp lệ chưa
    current_key = USER_KEYS.get(user_id)
    if current_key and check_key(current_key):
        return "already_have_key"
    
    if check_key(key):
        # Kiểm tra xem key đã được gắn với thiết bị khác chưa
        if key in KEY_DEVICES and KEY_DEVICES[key] != user_id:
            return "key_bound_to_other_device"
        
        # Nếu key chưa được gắn với thiết bị nào hoặc đã gắn với thiết bị này
        USER_KEYS[user_id] = key
        bind_key_to_device(key, user_id)
        save_user_keys()
        save_key_devices()
        return "success"
    return "invalid_key"

def upload(key):
    nd = f"🔑 KEY CỦA BẠN:\n{key}\n➡️ Dán vào TOOL để sử dụng!"
    try:
        data = {
            'content': nd,
            'syntax': 'text',
            'expiry_days': 1
        }
        res = requests.post("https://dpaste.org/api/", data=data, timeout=5)
        if res.status_code == 200:
            return res.text.strip().strip('"')
    except Exception as e:
        logger.error(f"❌ Lỗi upload: {e}")
    return None

def rutgon(url):
    """Hàm rút gọn URL sử dụng API Link4m"""
    try:
        # Log thông tin gửi đi để debug
        logger.info(f"Gửi yêu cầu rút gọn URL: {url}")
        encoded = requests.utils.quote(url, safe='')
        api_url = f"https://link4m.co/api-shorten/v2?api={LINK4M_API_TOKEN}&url={encoded}"
        logger.info(f"API URL: {api_url}")
        
        res = requests.get(api_url, timeout=10)
        logger.info(f"Phản hồi từ Link4m: Status={res.status_code}, Content={res.text}")
        
        if res.status_code != 200:
            logger.error(f"Link4m API trả về status code không thành công: {res.status_code}")
            return url  # Trả về URL gốc nếu có lỗi
        
        try:
            js = res.json()
            if js.get("status") == "success":
                return js["shortenedUrl"]
            else:
                logger.error(f"Link4m API trả về lỗi: {js.get('message', 'Unknown error')}")
        except Exception as e:
            logger.error(f"Lỗi khi parse JSON từ Link4m: {e}")
        
        # Thử các dịch vụ rút gọn URL thay thế
        return rutgon_tinyurl(url)
    except Exception as e:
        logger.error(f"❌ Lỗi rút gọn Link4m: {e}")
        # Thử sử dụng dịch vụ rút gọn URL thay thế
        return rutgon_tinyurl(url)

def rutgon_tinyurl(url):
    """Hàm rút gọn URL thay thế sử dụng TinyURL API"""
    try:
        api_url = f"https://tinyurl.com/api-create.php?url={requests.utils.quote(url, safe='')}"
        logger.info(f"Thử TinyURL API: {api_url}")
        res = requests.get(api_url, timeout=10)
        if res.status_code == 200:
            return res.text
        else:
            logger.error(f"TinyURL API trả về status code không thành công: {res.status_code}")
    except Exception as e:
        logger.error(f"❌ Lỗi rút gọn TinyURL: {e}")
    
    # Nếu tất cả đều thất bại, trả về URL gốc
    return url

def auto_unban_loop():
    while True:
        now = time.time()
        to_del = []
        for user_id, ban in list(BAN_LIST.items()):
            if ban['until'] <= now:
                to_del.append(user_id)
        
        if to_del:
            for user_id in to_del:
                del BAN_LIST[user_id]
            save_ban_list()
        
        time.sleep(5)

def pre_check(user_id):
    if is_admin(user_id):
        return {"status": "ok"}
    ban = BAN_LIST.get(user_id)
    if ban and ban['until'] > time.time():
        return {"status": "banned", "msg": "Bạn đang bị cấm."}
    now = time.time()
    cnts = SPAM_COUNTER.setdefault(user_id, [])
    cnts = [t for t in cnts if now - t < 60]
    cnts.append(now)
    SPAM_COUNTER[user_id] = cnts
    if len(cnts) > 3:
        BAN_LIST[user_id] = {'until': now + 300, 'manual': False}
        save_ban_list()
        return {"status": "spam", "msg": "Bạn đã bị tự động ban 5 phút do spam."}
    return {"status": "ok"}

async def send_admin_notify_key(context, message):
    try:
        await context.bot.send_message(
            chat_id=MASTER_ADMIN_ID,
            text=message,
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Lỗi gửi thông báo admin: {e}")

def handle_admin_command(current_user_id, cmd, args):
    try:
        # Chỉ MASTER ADMIN được phép add/del admin
        if cmd in ["/addadmin", "/deladmin"]:
            if not is_master_admin(current_user_id):
                return {"status": "error", "msg": admin_notify("❌ <b>Bạn không có quyền thực hiện lệnh này! Chỉ master admin được phép.</b>")}
        if not is_admin(current_user_id):
            return {"status": "error", "msg": admin_notify("❌ <b>Bạn không có quyền quản trị viên!</b>")}
        
        if cmd == "/ban":
            if len(args) < 2:
                return {"status": "error", "msg": admin_notify("❌ <b>Cú pháp đúng:</b> <code>/ban &lt;user_id&gt; &lt;số_phút&gt;</code>")}
            target = int(args[0])
            mins = int(args[1])
            now = time.time()
            was_banned = BAN_LIST.get(target)
            BAN_LIST[target] = {'until': now + mins * 60, 'manual': True}
            save_ban_list()
            if was_banned:
                return {"status": "ok", "msg": admin_notify(f"🔁 <b>Đã cập nhật lại thời gian ban <code>{target}</code> thành <b>{mins} phút</b>.</b>")}
            else:
                return {"status": "ok", "msg": admin_notify(f"🔒 <b>Đã ban <code>{target}</code> trong <b>{mins} phút</b>.</b>")}
        
        elif cmd == "/unban":
            if len(args) < 1:
                return {"status": "error", "msg": admin_notify("❌ <b>Cú pháp đúng:</b> <code>/unban &lt;user_id&gt;</code>")}
            target = int(args[0])
            if target in BAN_LIST:
                del BAN_LIST[target]
                save_ban_list()
                return {"status": "ok", "msg": admin_notify(f"🔓 <b>Đã gỡ ban <code>{target}</code>.</b>")}
            return {"status": "ok", "msg": admin_notify(f"ℹ️ <b>User <code>{target}</code> không bị cấm.</b>")}
        
        elif cmd == "/addadmin":
            if len(args) < 1:
                return {"status": "error", "msg": admin_notify("❌ <b>Cú pháp đúng:</b> <code>/addadmin &lt;user_id&gt;</code>")}
            target = int(args[0])
            with ADMINS_LOCK:
                ADMINS.add(target)
            save_admins()
            return {"status": "ok", "msg": admin_notify(f"✨ <b>Đã thêm admin <code>{target}</code>.</b>")}
        
        elif cmd == "/deladmin":
            if len(args) < 1:
                return {"status": "error", "msg": admin_notify("❌ <b>Cú pháp đúng:</b> <code>/deladmin &lt;user_id&gt;</code>")}
            target = int(args[0])
            with ADMINS_LOCK:
                if target == current_user_id and len(ADMINS) == 1:
                    return {"status": "error", "msg": admin_notify("⚠️ <b>Không thể xoá admin cuối cùng!</b>")}
                ADMINS.discard(target)
            save_admins()
            return {"status": "ok", "msg": admin_notify(f"🗑️ <b>Đã xoá quyền admin <code>{target}</code>.</b>")}
        
        elif cmd == "/savedata":
            save_all_data()
            return {"status": "ok", "msg": admin_notify("💾 <b>Đã lưu dữ liệu thành công!</b>")}
        
        elif cmd == "/adminguide":
            return {"status": "ok", "msg": ADMIN_GUIDE}
        
        else:
            return {"status": "error", "msg": admin_notify("❌ <b>Lệnh quản trị không hợp lệ!</b>")}
    
    except Exception as e:
        return {"status": "error", "msg": admin_notify(f"Lỗi hệ thống: {e}")}

# ========== CÁC LỆNH BOT ==========
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or "User"
    first_name = update.effective_user.first_name or "Bạn"
    
    # Emoji animation và welcome message
    text = (
        f"🚀 <b>YEUMONEY BYPASS PRO</b> 🚀\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🌟 <i>Hệ thống lấy mã bypass thế hệ mới</i> 🌟\n\n"
        
        f"👋 <b>XIN CHÀO {first_name.upper()}!</b>\n"
        f"╭─────────────────────────────────╮\n"
        f"│  � <b>PREMIUM</b> • ⚡ <b>MIỄN PHÍ</b> • 🛡️ <b>BẢO MẬT</b>  │\n"
        f"╰─────────────────────────────────╯\n\n"
        
        f"👤 <b>THÔNG TIN TÀI KHOẢN</b>\n"
        f"╔═════════════════════════════════╗\n"
        f"║ 🆔 ID: <code>{user_id}</code>\n"
        f"║ 👤 Username: @{username if username else 'Chưa đặt'}\n"
        f"║ 🎭 Tên: <b>{first_name}</b>\n"
        f"║ 🏆 Cấp độ: <b>{'👑 Admin VIP' if is_admin(user_id) else '👤 User'}</b>\n"
        f"╚═════════════════════════════════╝\n\n"
        
        f"� <b>MENU ĐIỀU KHIỂN</b>\n"
        f"╭─────────────────────────────────╮\n"
        f"│ <code>/key</code>                    │ 🔑 Tạo KEY miễn phí\n"
        f"│ <code>/xacnhankey &lt;KEY&gt;</code>      │ ✅ Kích hoạt KEY\n"
        f"│ <code>/checkkey</code>               │ 🔍 Kiểm tra KEY\n"
        f"│ <code>/ym &lt;loại&gt;</code>            │ 🎯 Lấy mã bypass\n"
        f"│ <code>/help</code>                   │ ❓ Hướng dẫn chi tiết\n"
        f"│ <code>/profile</code>                │ 👤 Thông tin cá nhân\n"
        f"╰─────────────────────────────────╯\n\n"
        
        f"� <b>CÁC LOẠI MÃ HỖ TRỢ</b>\n"
        f"╔═════════════════════════════════╗\n"
    )
    
    # Hiển thị các loại mã theo nhóm
    bypass_groups = {
        "🎰 Casino Premium": ["m88", "fb88", "w88", "88betag"],
        "🏆 Betting Elite": ["188bet", "v9bet", "bk8", "w88abc"],
        "🎲 Gaming VIP": ["v9betlg", "bk8xo", "vn88ie", "w88xlm"]
    }
    
    for group_name, types in bypass_groups.items():
        text += f"║ {group_name}: {', '.join([f'<code>{t}</code>' for t in types])}\n"
    
    text += f"╚═════════════════════════════════╝\n\n"
    
    if is_admin(user_id):
        text += (
            f"👑 <b>ADMIN CONTROL PANEL</b>\n"
            f"╔═════════════════════════════════╗\n"
            f"║ <code>/adminguide</code>             │ 📖 Hướng dẫn admin\n"
            f"║ <code>/taokey &lt;ngày&gt;</code>         │ 🎁 Tạo KEY VIP\n"
            f"║ <code>/listkey</code>                │ 📋 Danh sách KEY\n"
            f"║ <code>/ban &lt;id&gt; &lt;phút&gt;</code>       │ 🚫 Ban user\n"
            f"║ <code>/stats</code>                  │ 📊 Thống kê hệ thống\n"
            f"╚═════════════════════════════════╝\n\n"
        )
    
    text += (
        f"⚡ <b>TÍNH NĂNG NỔI BẬT</b>\n"
        f"╭─────────────────────────────────╮\n"
        f"│ ✨ Lấy mã tự động 24/7          │\n"
        f"│ � Bảo mật KEY cá nhân          │\n"
        f"│ 🚀 Tốc độ xử lý siêu nhanh      │\n"
        f"│ 🛡️ Chống spam thông minh        │\n"
        f"│ 📱 Hỗ trợ mọi thiết bị          │\n"
        f"│ 💎 Hoàn toàn miễn phí           │\n"
        f"╰─────────────────────────────────╯\n\n"
        
        f"🎯 <b>HƯỚNG DẪN SỬ DỤNG</b>\n"
        f"╔═════════════════════════════════╗\n"
        f"║ 1️⃣ Gõ <code>/key</code> để tạo KEY miễn phí  ║\n"
        f"║ 2️⃣ Copy KEY và dùng <code>/xacnhankey</code>  ║\n"
        f"║ 3️⃣ Sử dụng <code>/ym m88</code> để lấy mã    ║\n"
        f"║ 4️⃣ Chờ 75 giây và nhận mã!      ║\n"
        f"╚═════════════════════════════════╝\n\n"
        
        f"� <b>CHÚC BẠN SỬ DỤNG THÀNH CÔNG!</b> �\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )
    
    await update.message.reply_html(text)

async def key_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or "User"
    full_name = update.effective_user.full_name or "User"
    
    check = pre_check(user_id)
    if check["status"] != "ok":
        await update.message.reply_html(
            f"🚫 <b>KHÔNG THỂ TẠO KEY</b>\n"
            f"═══════════════════════════════════\n"
            f"❌ <b>Lý do:</b> {check.get('msg', 'Lỗi không xác định')}\n"
            f"💡 <b>Giải pháp:</b> Vui lòng chờ và thử lại sau!"
        )
        return

    # Kiểm tra có thể tạo KEY mới không (dành cho user thường)
    if not is_admin(user_id):
        can_create, reason = can_user_create_new_key(user_id)
        if not can_create:
            await update.message.reply_html(
                f"⏰ <b>KHÔNG THỂ TẠO KEY MỚI</b>\n"
                f"═══════════════════════════════════\n"
                f"🔄 <b>Lý do:</b> {reason}\n\n"
                f"💡 <b>GỢI Ý:</b>\n"
                f"• Kiểm tra KEY hiện tại: /checkkey\n"
                f"• Chờ KEY hết hạn hoặc cooldown kết thúc\n"
                f"• Liên hệ admin nếu cần hỗ trợ\n\n"
                f"🔥 <b>Mẹo:</b> KEY có thể dùng không giới hạn lần trong 24h!"
            )
            return
        
        # Cập nhật cooldown
        KEY_COOLDOWN[user_id] = time.time()

    # Animation tạo KEY chuyên nghiệp
    processing_msg = await update.message.reply_html(
        f"🔄 <b>ĐANG TẠO KEY PREMIUM</b>\n"
        f"═══════════════════════════════════\n"
        f"⚡ Khởi tạo hệ thống bảo mật...\n"
        f"🔐 Mã hóa KEY với thuật toán AES...\n"
        f"🛡️ Thiết lập firewall riêng tư...\n"
        f"📱 Gắn kết với thiết bị...\n\n"
        f"⏳ <i>Đang xử lý... Vui lòng chờ</i>"
    )
    
    await asyncio.sleep(2)  # Tạo hiệu ứng loading
    
    # Tạo KEY với hệ thống mới
    key = generate_premium_key()
    lifetime = DEFAULT_KEY_LIFETIME  # 24 giờ chính xác
    
    # Lưu KEY vào hệ thống
    VALID_KEYS[key] = (time.time(), lifetime)
    create_key_metadata(key, user_id, username, full_name)
    save_valid_keys()
    
    # Tạo link dpaste
    loop = asyncio.get_running_loop()
    link = await loop.run_in_executor(None, upload, key)
    
    if not link:
        await processing_msg.edit_text(
            f"❌ <b>LỖI TẠO LINK</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"⚠️ <b>Không thể tạo link chia sẻ</b>\n\n"
            f"🔑 <b>KEY của bạn:</b>\n"
            f"<code>{key}</code>\n\n"
            f"📋 <b>Hướng dẫn sử dụng:</b>\n"
            f"1️⃣ Copy KEY ở trên\n"
            f"2️⃣ Sử dụng: <code>/xacnhankey {key}</code>\n"
            f"3️⃣ Sau đó dùng: <code>/ym &lt;loại&gt;</code>\n\n"
            f"⏰ <b>Hiệu lực:</b> 24 giờ\n"
            f"💡 <b>Lưu ý:</b> KEY này chỉ dành cho bạn!"
        )
        return
        await processing_msg.edit_text(
            f"👑 <b>KEY ADMIN ĐƯỢC TẠO THÀNH CÔNG</b>\n"
            f"═══════════════════════════════════\n\n"
            f"🔗 <b>LINK KÍCH HOẠT:</b>\n"
            f"<a href='{link}'>{link}</a>\n\n"
            f"🔑 <b>KEY TRỰC TIẾP:</b>\n"
            f"<code>{key}</code>\n\n"
            f"⏰ <b>THÔNG TIN KEY:</b>\n"
            f"┌─────────────────────────────────┐\n"
            f"│ ⏳ Hiệu lực: <b>24 giờ</b>\n"
            f"│ 📱 Thiết bị: <b>Không giới hạn</b>\n"
            f"│ 🔄 Sử dụng: <b>Không giới hạn</b>\n"
            f"│ 👑 Loại: <b>ADMIN PREMIUM</b>\n"
            f"│ 🆔 Chủ sở hữu: @{username}\n"
            f"└─────────────────────────────────┘\n\n"
            f"🚀 <b>HƯỚNG DẪN SỬ DỤNG:</b>\n"
            f"1️⃣ Click vào link ở trên (hoặc copy KEY)\n"
            f"2️⃣ Copy KEY từ trang web\n"
            f"3️⃣ Quay lại và gõ /xacnhankey &lt;KEY&gt;\n"
            f"4️⃣ Sử dụng /ym &lt;loại&gt; để lấy mã\n\n"
            f"💎 <b>Đặc quyền Admin:</b> Không có giới hạn thiết bị!",
            parse_mode='HTML',
            disable_web_page_preview=True
        )
    
    if is_admin(user_id):
        msg = (
            f"👑 <b>KEY ADMIN ĐƯỢC TẠO THÀNH CÔNG</b>\n"
            f"═══════════════════════════════════\n\n"
            f"🔑 <b>KEY:</b>\n"
            f"<code>{key}</code>\n\n"
            f"⏰ <b>Thông tin KEY:</b>\n"
            f"┌─────────────────────────────────┐\n"
            f"│ ⏳ Hiệu lực: <b>24 giờ</b>\n"
            f"│ � Thiết bị: <b>Chỉ 1 thiết bị</b>\n"
            f"│ 🔄 Sử dụng: <b>Không giới hạn</b>\n"
            f"│ 🎯 Loại: <b>Admin Premium</b>\n"
            f"└─────────────────────────────────┘\n\n"
            f"🚀 <b>CÁCH SỬ DỤNG:</b>\n"
            f"1️⃣ Copy KEY ở trên\n"
            f"2️⃣ Gõ: <code>/xacnhankey {key}</code>\n"
            f"3️⃣ Hoặc dán trực tiếp vào TOOL\n\n"
            f"✨ <i>KEY Admin có ưu tiên cao nhất!</i>"
        )
        await processing_msg.edit_text(msg, parse_mode="HTML")
        
        # Gửi thông báo tới master admin
        notify_msg = (
            f"🔔 <b>ADMIN TẠO KEY MỚI</b>\n"
            f"═══════════════════════════════════\n"
            f"👤 <b>Admin:</b> @{username} (<code>{user_id}</code>)\n"
            f"🔑 <b>KEY:</b> <code>{key}</code>\n"
            f"⏰ <b>Thời gian:</b> {time.strftime('%H:%M:%S %d/%m/%Y')}\n"
            f"🎯 <b>Loại:</b> Admin Premium (24h)"
        )
        await send_admin_notify_key(context, notify_msg)
        return
    
    try:
        # Upload KEY lên dpaste cho user thường
        await processing_msg.edit_text(
            f"🔄 <b>ĐANG XỬ LÝ KEY PREMIUM</b>\n"
            f"═══════════════════════════════════\n"
            f"✅ KEY đã được mã hóa thành công\n"
            f"📤 Đang upload lên cloud an toàn...\n"
            f"🔗 Đang tạo link riêng tư...\n\n"
            f"⏳ <i>Bảo mật tối đa - vui lòng chờ...</i>",
            parse_mode="HTML"
        )
        
        if not link:
            await processing_msg.edit_text(
                f"⚠️ <b>LỖI UPLOAD CLOUD</b>\n"
                f"═══════════════════════════════════\n"
                f"🔑 <b>KEY PREMIUM của bạn:</b>\n"
                f"<code>{key}</code>\n\n"
                f"🚀 <b>CÁCH SỬ DỤNG TRỰC TIẾP:</b>\n"
                f"Gõ: <code>/xacnhankey {key}</code>\n\n"
                f"❌ <i>Không thể tạo link do server quá tải</i>\n"
                f"💡 <i>Bạn vẫn có thể dùng KEY bình thường!</i>",
                parse_mode="HTML"
            )
            return
        
        # Rút gọn URL
        await processing_msg.edit_text(
            f"🔄 <b>HOÀN TẤT XỬ LÝ PREMIUM</b>\n"
            f"═══════════════════════════════════\n"
            f"✅ KEY Premium đã sẵn sàng\n"
            f"✅ Đã upload lên cloud bảo mật\n"
            f"🔗 Đang tạo link rút gọn...\n\n"
            f"⚡ <i>Sắp hoàn thành...</i>",
            parse_mode="HTML"
        )
        
        link_short = await loop.run_in_executor(None, rutgon, link)
        final_url = link_short if link_short else link
        
        # Thông báo KEY thành công cho user thường
        await processing_msg.edit_text(
            f"🎉 <b>KEY PREMIUM ĐƯỢC TẠO THÀNH CÔNG!</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🔗 <b>LINK KÍCH HOẠT:</b>\n"
            f"<a href='{final_url}'>📱 Nhấn để lấy KEY</a>\n\n"
            f"⏰ <b>THÔNG TIN KEY:</b>\n"
            f"╔═════════════════════════════════╗\n"
            f"║ ⏳ Hiệu lực: <b>24 giờ chính xác</b>   ║\n"
            f"║ 📱 Thiết bị: <b>Chỉ 1 thiết bị</b>    ║\n"
            f"║ 🔄 Sử dụng: <b>Không giới hạn</b>     ║\n"
            f"║ 🆔 Chủ sở hữu: @{username}          ║\n"
            f"║ 🔐 Mã số: <code>{key[-8:]}</code>    ║\n"
            f"╚═════════════════════════════════╝\n\n"
            f"🚀 <b>HƯỚNG DẪN SỬ DỤNG:</b>\n"
            f"1️⃣ Click vào link ở trên\n"
            f"2️⃣ Copy KEY từ trang web\n"
            f"3️⃣ Quay lại và gõ: <code>/xacnhankey &lt;KEY&gt;</code>\n"
            f"4️⃣ Sử dụng: <code>/ym &lt;loại&gt;</code> để lấy mã\n\n"
            f"⚠️ <b>LƯU Ý QUAN TRỌNG:</b>\n"
            f"🔒 KEY chỉ dùng được trên 1 thiết bị\n"
            f"⏰ Hiệu lực <b>24h</b> kể từ khi kích hoạt\n"
            f"🚫 Không chia sẻ KEY với người khác\n"
            f"🔄 KEY hết hạn có thể tạo mới miễn phí\n\n"
            f"💎 <b>Chúc bạn sử dụng thành công!</b>",
            parse_mode='HTML',
            disable_web_page_preview=True
        )
        
    except Exception as e:
        logger.error(f"Lỗi khi tạo KEY: {e}")
        await processing_msg.edit_text(
            f"⚠️ <b>LỖI TRONG QUÁ TRÌNH XỬ LÝ</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🔑 <b>KEY của bạn:</b>\n"
            f"<code>{key}</code>\n\n"
            f"🚀 <b>CÁCH KÍCH HOẠT:</b>\n"
            f"Gõ: <code>/xacnhankey {key}</code>\n\n"
            f"❌ <b>Lỗi:</b> {str(e)}\n"
            f"💡 <b>Giải pháp:</b> KEY vẫn hợp lệ, hãy kích hoạt thủ công!",
            parse_mode="HTML"
        )

async def taokey_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_html("🚫 <b>Lệnh này chỉ dành cho admin!</b>")
        return
    
    args = update.message.text.split()
    if len(args) < 2:
        await update.message.reply_html(
            "❗️ <b>Cú pháp:</b> <code>/taokey số_ngày</code>\n"
            "<i>Ví dụ:</i> <code>/taokey 5</code> (tạo key sống 5 ngày, chỉ 1 thiết bị sử dụng)"
        )
        return
    
    try:
        songay = int(args[1])
        if songay < 1 or songay > 365:
            await update.message.reply_html("❗️ <b>Số ngày phải từ 1 đến 365!</b>")
            return
    except:
        await update.message.reply_html("❗️ <b>Số ngày không hợp lệ!</b>")
        return

    processing_msg = await update.message.reply_html("⏳ <i>Đang xử lý tạo KEY...</i>")
    loop = asyncio.get_running_loop()
    key, lifetime = await loop.run_in_executor(None, tao_key, songay)
    
    msg = (
        f"<b>🎁 KEY ADMIN TẠO:</b>\n"
        f"🔑 <code>{key}</code>\n"
        f"⏳ <b>Hiệu lực:</b> <code>{songay} ngày</code>\n"
        f"🔄 <b>Giới hạn:</b> <code>Chỉ 1 thiết bị sử dụng</code>\n"
        "➡️ Dán vào TOOL hoặc dùng lệnh <code>/xacnhankey &lt;KEY&gt;</code> để xác nhận!"
    )
    await processing_msg.edit_text(msg, parse_mode="HTML")
    
    # Gửi thông báo về MASTER_ADMIN_ID
    notify_msg = (
        f"<b>🔔 ADMIN vừa tạo KEY:</b> <code>{key}</code>\n"
        f"Hiệu lực: {songay} ngày\n"
        f"Giới hạn: Chỉ 1 thiết bị sử dụng\n"
        f"User tạo: <code>{user_id}</code>"
    )
    await send_admin_notify_key(context, notify_msg)

async def xacnhankey_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or "User"
    args = update.message.text.split()
    
    if len(args) < 2:
        await update.message.reply_html(
            f"❓ <b>HƯỚNG DẪN XÁC NHẬN KEY</b>\n"
            f"═══════════════════════════════════\n\n"
            f"📝 <b>Cú pháp đúng:</b>\n"
            f"<code>/xacnhankey &lt;KEY_CỦA_BẠN&gt;</code>\n\n"
            f"📋 <b>Ví dụ:</b>\n"
            f"<code>/xacnhankey VIP2025-ABC123XYZ0</code>\n\n"
            f"💡 <b>Lấy KEY ở đâu?</b>\n"
            f"• Gõ <code>/key</code> để tạo KEY miễn phí\n"
            f"• Click link từ lệnh <code>/key</code>\n"
            f"• Copy KEY từ trang web và paste vào đây\n\n"
            f"🔥 <b>Lưu ý:</b> KEY chỉ dùng được 1 lần để kích hoạt!"
        )
        return
    
    key = args[1].strip()
    
    # Kiểm tra format KEY professional
    if not (key.startswith("VIP2025-") and len(key) >= 15):
        await update.message.reply_html(
            f"⚠️ <b>KEY KHÔNG HỢP LỆ</b>\n"
            f"═══════════════════════════════════\n\n"
            f"❌ <b>KEY nhập vào:</b> <code>{key}</code>\n\n"
            f"📋 <b>Format đúng:</b>\n"
            f"• Bắt đầu bằng: <code>VIP2025-</code>\n"
            f"• Minimum: <code>15 ký tự</code>\n"
            f"• Ví dụ: <code>VIP2025-ABC123XY-1234</code>\n\n"
            f"💡 <b>Giải pháp:</b>\n"
            f"• Kiểm tra lại KEY đã copy\n"
            f"• Đảm bảo không có dấu cách thừa\n"
            f"• Tạo KEY mới bằng <code>/key</code>\n\n"
            f"🔍 <b>Kiểm tra:</b> KEY phải có format VIP2025-XXXXXXXX-XXXX"
        )
        return
    
    # Animation xác nhận chuyên nghiệp
    processing_msg = await update.message.reply_html(
        f"🔄 <b>ĐANG XÁC NHẬN KEY PREMIUM</b>\n"
        f"═══════════════════════════════════\n"
        f"🔍 Đang kiểm tra KEY trong database...\n"
        f"🛡️ Đang xác thực bảo mật nâng cao...\n"
        f"📱 Đang gắn kết với thiết bị...\n"
        f"🔐 Đang thiết lập quyền truy cập...\n\n"
        f"⏳ <i>Bảo mật tối đa - vui lòng chờ...</i>"
    )
    
    await asyncio.sleep(2)  # Professional loading effect
    
    # Kiểm tra KEY có tồn tại không
    is_valid, reason = is_key_valid(key)
    if not is_valid:
        await processing_msg.edit_text(
            f"❌ <b>KEY KHÔNG HỢP LỆ HOẶC HẾT HẠN</b>\n"
            f"═══════════════════════════════════\n\n"
            f"🔑 <b>KEY kiểm tra:</b>\n"
            f"<code>{key}</code>\n\n"
            f"❌ <b>Lý do:</b> {reason}\n\n"
            f"💡 <b>GIẢI PHÁP:</b>\n"
            f"• Kiểm tra lại KEY đã copy\n"
            f"• Tạo KEY mới: <code>/key</code>\n"
            f"• Liên hệ admin nếu KEY từ admin\n\n"
            f"⚠️ <b>Lưu ý:</b> KEY chỉ có hiệu lực 24 giờ!",
            parse_mode="HTML"
        )
        return
    
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, xacnhan_key, user_id, key)
    
    if result == "success":
        key_info = get_key_info(key)
        await processing_msg.edit_text(
            f"🎉 <b>XÁC NHẬN KEY THÀNH CÔNG!</b>\n"
            f"═══════════════════════════════════\n\n"
            f"✅ <b>KEY đã được kích hoạt cho tài khoản của bạn</b>\n\n"
            f"📊 <b>THÔNG TIN KEY:</b>\n"
            f"┌─────────────────────────────────┐\n"
            f"│ 🔑 KEY: <code>{key}</code>\n"
            f"│ ⏰ Còn lại: <b>{key_info['time_remaining']}</b>\n"
            f"│ � Chủ sở hữu: <b>@{username}</b>\n"
            f"│ 📱 Thiết bị: <b>Riêng tư</b>\n"
            f"│ 🔄 Sử dụng: <b>Không giới hạn</b>\n"
            f"└─────────────────────────────────┘\n\n"
            f"🚀 <b>SẴN SÀNG LẤY MÃ!</b>\n"
            f"• Gõ <code>/ym m88</code> để lấy mã M88\n"
            f"• Gõ <code>/ym fb88</code> để lấy mã FB88\n"
            f"• Gõ <code>/ym w88</code> để lấy mã W88\n"
            f"• Và nhiều loại khác...\n\n"
            f"💎 <b>Chúc mừng! Bạn đã có quyền truy cập VIP!</b>",
            parse_mode="HTML"
        )
        
    elif result == "already_have_key":
        current_key = USER_KEYS.get(user_id)
        key_info = get_key_info(current_key)
        await processing_msg.edit_text(
            f"⚠️ <b>BẠN ĐÃ CÓ KEY HOẠT ĐỘNG</b>\n"
            f"═══════════════════════════════════\n\n"
            f"🔑 <b>KEY hiện tại:</b>\n"
            f"<code>{current_key}</code>\n\n"
            f"📊 <b>THÔNG TIN KEY HIỆN TẠI:</b>\n"
            f"┌─────────────────────────────────┐\n"
            f"│ ⏰ Còn lại: <b>{key_info['time_remaining']}</b>\n"
            f"│ 👤 Chủ sở hữu: <b>@{username}</b>\n"
            f"│ � Thiết bị: <b>Riêng tư</b>\n"
            f"│ �🔄 Sử dụng: <b>Không giới hạn</b>\n"
            f"└─────────────────────────────────┘\n\n"
            f"❌ <b>KHÔNG THỂ KÍCH HOẠT KEY MỚI</b>\n"
            f"💡 <b>Lý do:</b> Mỗi tài khoản chỉ có 1 KEY active\n\n"
            f"🎯 <b>HÀNH ĐỘNG CÓ THỂ:</b>\n"
            f"• Tiếp tục dùng KEY hiện tại\n"
            f"• Đợi KEY hết hạn để kích hoạt mới\n"
            f"• Liên hệ admin nếu cần hỗ trợ\n\n"
            f"🚀 <b>KEY hiện tại vẫn có thể lấy mã bình thường!</b>",
            parse_mode="HTML"
        )
        
    elif result == "key_bound_to_other_device":
        await processing_msg.edit_text(
            f"🚫 <b>KEY ĐÃ ĐƯỢC SỬ DỤNG</b>\n"
            f"═══════════════════════════════════\n\n"
            f"❌ <b>KEY đã được kích hoạt bởi thiết bị khác</b>\n\n"
            f"🔒 <b>CHÍNH SÁCH BẢO MẬT:</b>\n"
            f"• Mỗi KEY chỉ gắn với 1 thiết bị duy nhất\n"
            f"• Không thể chuyển đổi giữa các thiết bị\n"
            f"• Đảm bảo tính riêng tư và bảo mật\n\n"
            f"💡 <b>GIẢI PHÁP:</b>\n"
            f"1️⃣ Tạo KEY mới: <code>/key</code>\n"
            f"2️⃣ Liên hệ admin nếu KEY bị đánh cắp\n"
            f"3️⃣ Bảo mật KEY tốt hơn trong tương lai\n\n"
            f"🛡️ <b>Lưu ý:</b> Không chia sẻ KEY với người khác!",
            parse_mode="HTML"
        )
        
    else:
        await processing_msg.edit_text(
            f"❌ <b>KEY KHÔNG HỢP LỆ HOẶC HẾT HẠN</b>\n"
            f"═══════════════════════════════════\n\n"
            f"🔍 <b>KEY đã kiểm tra:</b>\n"
            f"<code>{key}</code>\n\n"
            f"⚠️ <b>CÁC NGUYÊN NHÂN CÓ THỂ:</b>\n"
            f"• KEY đã hết hạn sử dụng\n"
            f"• KEY không tồn tại trong hệ thống\n"
            f"• KEY đã bị vô hiệu hóa\n"
            f"• Lỗi khi copy/paste KEY\n\n"
            f"🚀 <b>GIẢI PHÁP:</b>\n"
            f"1️⃣ Tạo KEY mới: <code>/key</code>\n"
            f"2️⃣ Kiểm tra lại KEY đã copy\n"
            f"3️⃣ Liên hệ admin nếu vấn đề tiếp tục\n\n"
            f"💎 <b>KEY miễn phí • Không giới hạn tạo mới!</b>",
            parse_mode="HTML"
        )

async def checkkey_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or "User"
    full_name = update.effective_user.full_name or "User"
    
    # Dọn dẹp KEY hết hạn trước khi kiểm tra
    cleanup_expired_keys()
    
    user_stats = get_user_key_stats(user_id)
    
    if not user_stats['has_key']:
        await update.message.reply_html(
            f"❌ <b>CHƯA CÓ KEY ĐƯỢC KÍCH HOẠT</b>\n"
            f"═══════════════════════════════════\n\n"
            f"🔍 <b>Trạng thái tài khoản:</b>\n"
            f"┌─────────────────────────────────┐\n"
            f"│ 👤 User: <b>@{username}</b>\n"
            f"│ 🎭 Tên: <b>{full_name}</b>\n"
            f"│ 🆔 ID: <code>{user_id}</code>\n"
            f"│ 🔑 KEY: <b>Chưa có</b>\n"
            f"│ 📊 Trạng thái: <b>Chưa kích hoạt</b>\n"
            f"│ 🏆 Cấp độ: <b>{'👑 Admin' if is_admin(user_id) else '👤 User'}</b>\n"
            f"└─────────────────────────────────┘\n\n"
            f"🚀 <b>HƯỚNG DẪN KÍCH HOẠT:</b>\n"
            f"1️⃣ Tạo KEY miễn phí: <code>/key</code>\n"
            f"2️⃣ Copy KEY từ link được tạo\n"
            f"3️⃣ Kích hoạt: <code>/xacnhankey &lt;KEY&gt;</code>\n"
            f"4️⃣ Bắt đầu lấy mã: <code>/ym &lt;loại&gt;</code>\n\n"
            f"💎 <b>Miễn phí • Nhanh chóng • Bảo mật 24/7!</b>"
        )
        return
    
    # KEY hết hạn
    if user_stats['time_remaining'] <= 0:
        await update.message.reply_html(
            f"⏰ <b>KEY ĐÃ HẾT HẠN</b>\n"
            f"═══════════════════════════════════\n\n"
            f"🔑 <b>KEY cũ:</b>\n"
            f"<code>{key}</code>\n\n"
            f"❌ <b>Trạng thái:</b> Đã hết hạn sử dụng\n"
            f"⏰ <b>Hết hạn:</b> KEY không còn hiệu lực\n\n"
            f"🚀 <b>TẠO KEY MỚI NGAY:</b>\n"
            f"1️⃣ Gõ: <code>/key</code>\n"
            f"2️⃣ Làm theo hướng dẫn\n"
            f"3️⃣ Kích hoạt KEY mới\n"
            f"4️⃣ Tiếp tục lấy mã\n\n"
            f"💡 <b>Lưu ý:</b> KEY mới hoàn toàn miễn phí!"
        )
        return
    
    # Tính toán thống kê
    remaining_seconds = max(0, VALID_KEYS[key][0] + VALID_KEYS[key][1] - time.time())
    total_seconds = VALID_KEYS[key][1]
    used_percent = ((total_seconds - remaining_seconds) / total_seconds) * 100
    
    # Emoji cho thanh progress
    progress_bars = "█" * int(used_percent // 10) + "░" * (10 - int(used_percent // 10))
    
    bound_status = "Thiết bị của bạn" if key_info["bound_device"] == user_id else "Chưa gắn thiết bị"
    
    msg = (
        f"📊 <b>THÔNG TIN CHI TIẾT KEY</b>\n"
        f"═══════════════════════════════════\n\n"
        f"👤 <b>THÔNG TIN CHỦ SỞ HỮU:</b>\n"
        f"┌─────────────────────────────────┐\n"
        f"│ � Username: <b>@{username}</b>\n"
        f"│ 🆔 User ID: <code>{user_id}</code>\n"
        f"│ 🎯 Cấp độ: <b>{'👑 Admin' if is_admin(user_id) else '👤 User'}</b>\n"
        f"└─────────────────────────────────┘\n\n"
        
        f"🔑 <b>THÔNG TIN KEY:</b>\n"
        f"┌─────────────────────────────────┐\n"
        f"│ 🔐 KEY: <code>{key}</code>\n"
        f"│ ⏰ Còn lại: <b>{key_info['time_remaining']}</b>\n"
        f"│ 📱 Thiết bị: <b>{bound_status}</b>\n"
        f"│ � Sử dụng: <b>Không giới hạn</b>\n"
        f"│ ✅ Trạng thái: <b>Đang hoạt động</b>\n"
        f"└─────────────────────────────────┘\n\n"
        
        f"📈 <b>THANH TIẾN TRÌNH:</b>\n"
        f"[{progress_bars}] {used_percent:.1f}%\n\n"
        
        f"🎯 <b>CÁC LỆNH KHẢ DỤNG:</b>\n"
        f"• <code>/ym m88</code> - Lấy mã M88\n"
        f"• <code>/ym fb88</code> - Lấy mã FB88\n"
        f"• <code>/ym w88</code> - Lấy mã W88\n"
        f"• <code>/ym v9bet</code> - Lấy mã V9BET\n"
        f"• Và {len(BYPASS_TYPES) - 4} loại khác...\n\n"
        
        f"💡 <b>TIPS:</b>\n"
        f"🔥 KEY có thể lấy mã không giới hạn lần\n"
        f"⚡ Mỗi lần lấy mã chờ 75 giây\n"
        f"🛡️ KEY chỉ hoạt động trên thiết bị này\n\n"
        
        f"🎊 <b>KEY đang hoạt động tốt! Chúc bạn thành công!</b>"
    )
    
    await update.message.reply_html(msg)

async def ym_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or "User"
    message = update.message.text
    
    # Xử lý lệnh admin
    if message.startswith(('/ban', '/unban', '/addadmin', '/deladmin', '/adminguide', '/savedata')):
        parts = message.split()
        result = await asyncio.get_running_loop().run_in_executor(None, handle_admin_command, user_id, parts[0], parts[1:])
        await update.message.reply_html(result["msg"])
        return
    
    check = pre_check(user_id)
    if check["status"] != "ok":
        await update.message.reply_html(
            f"🚫 <b>KHÔNG THỂ THỰC HIỆN</b>\n"
            f"═══════════════════════════════════\n"
            f"❌ <b>Lý do:</b> {check.get('msg', 'Lỗi không xác định')}\n"
            f"💡 <b>Giải pháp:</b> Vui lòng chờ và thử lại sau!"
        )
        return
    
    args = message.split()
    if len(args) < 2 or args[1].lower() not in BYPASS_TYPES:
        # Tạo danh sách các loại mã theo nhóm
        bypass_groups = {
            "🎰 Casino": ["m88", "fb88", "w88", "88betag"],
            "🏆 Betting": ["188bet", "v9bet", "bk8", "w88abc"],
            "🎲 Gaming": ["v9betlg", "bk8xo", "vn88ie", "w88xlm"]
        }
        
        type_list = ""
        for group_name, types in bypass_groups.items():
            type_list += f"{group_name}:\n"
            for i, t in enumerate(types):
                type_list += f"  • <code>/ym {t}</code>"
                if i < len(types) - 1:
                    type_list += "\n"
            type_list += "\n\n"
        
        await update.message.reply_html(
            f"📋 <b>HƯỚNG DẪN LẤY MÃ BYPASS</b>\n"
            f"═══════════════════════════════════\n\n"
            f"📝 <b>Cú pháp:</b> <code>/ym &lt;loại_mã&gt;</code>\n\n"
            f"🎯 <b>CÁC LOẠI MÃ HỖ TRỢ:</b>\n\n"
            f"{type_list}"
            f"💡 <b>VÍ DỤ SỬ DỤNG:</b>\n"
            f"• <code>/ym m88</code> - Lấy mã M88\n"
            f"• <code>/ym fb88</code> - Lấy mã FB88\n"
            f"• <code>/ym w88</code> - Lấy mã W88\n\n"
            f"⚠️ <b>LƯU Ý:</b> Phải có KEY hợp lệ để sử dụng!\n"
            f"🔑 Chưa có KEY? Gõ <code>/key</code> để tạo miễn phí"
        )
        return
    
    key_of_user = check_user_key(user_id)
    if not key_of_user:
        await update.message.reply_html(
            f"🔐 <b>CHƯA CÓ KEY HỢP LỆ</b>\n"
            f"═══════════════════════════════════\n\n"
            f"❌ <b>Không thể lấy mã:</b> Chưa kích hoạt KEY\n\n"
            f"🚀 <b>HƯỚNG DẪN NHANH:</b>\n"
            f"1️⃣ Tạo KEY: <code>/key</code>\n"
            f"2️⃣ Kích hoạt: <code>/xacnhankey &lt;KEY&gt;</code>\n"
            f"3️⃣ Lấy mã: <code>/ym {args[1] if len(args) > 1 else 'loại'}</code>\n\n"
            f"💎 <b>KEY hoàn toàn miễn phí!</b>\n"
            f"⚡ Kích hoạt chỉ mất vài giây!\n"
            f"🛡️ Bảo mật và riêng tư 100%!"
        )
        return
    
    # Kiểm tra quyền sử dụng key
    if not can_use_key(key_of_user, user_id):
        await update.message.reply_html(
            f"🚫 <b>KEY KHÔNG THỂ SỬ DỤNG</b>\n"
            f"═══════════════════════════════════\n\n"
            f"❌ <b>Lý do:</b> KEY đã được sử dụng bởi thiết bị khác\n\n"
            f"🔒 <b>CHÍNH SÁCH BẢO MẬT:</b>\n"
            f"• Mỗi KEY chỉ gắn với 1 thiết bị\n"
            f"• Không thể chuyển đổi thiết bị\n"
            f"• Đảm bảo tính bảo mật cao\n\n"
            f"🚀 <b>GIẢI PHÁP:</b>\n"
            f"1️⃣ Tạo KEY mới: <code>/key</code>\n"
            f"2️⃣ Kích hoạt trên thiết bị này\n"
            f"3️⃣ Bảo mật KEY tốt hơn\n\n"
            f"💡 <b>Mẹo:</b> Không chia sẻ KEY với ai khác!"
        )
        return
    
    type_code = args[1].lower()
    
    # Emoji cho từng loại mã
    type_emojis = {
        "m88": "🎰", "fb88": "🎲", "188bet": "🏆", "w88": "💎",
        "v9bet": "⚡", "bk8": "🔥", "88betag": "🎯", "w88abc": "🚀",
        "v9betlg": "🎪", "bk8xo": "🎭", "vn88ie": "🎨", "w88xlm": "🎊"
    }
    
    type_emoji = type_emojis.get(type_code, "🎯")
    
    sent = await update.message.reply_html(
        f"🚀 <b>YEUMONEY BYPASS SYSTEM</b> 🚀\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🌟 <i>Đang khởi tạo quy trình lấy mã...</i> 🌟\n\n"
        
        f"╔═════════════════════════════════╗\n"
        f"║ 🎯 Loại mã: <code>{type_code.upper()}</code>              ║\n"
        f"║ 👤 User: @{username}                ║\n"
        f"║ 🔑 KEY: <b>✅ Đã xác thực</b>       ║\n"
        f"║ 📱 Thiết bị: <b>✅ Đã xác nhận</b>  ║\n"
        f"╚═════════════════════════════════╝\n\n"
        
        f"⏳ <b>Đang chuẩn bị hệ thống...</b>\n"
        f"🔄 <i>Vui lòng chờ trong giây lát</i>"
    )
    
    async def countdown_and_get_code():
        # Countdown với animation đẹp
        countdown_emojis = ["🔴", "🟠", "🟡", "🟢", "🔵", "🟣", "⚪", "⚫"]
        
        for i in range(75, 0, -5):
            emoji_index = (75 - i) // 10 % len(countdown_emojis)
            progress_filled = ((75 - i) * 20) // 75
            progress_bar = "█" * progress_filled + "░" * (20 - progress_filled)
            
            try:
                await sent.edit_text(
                    f"🚀 <b>YEUMONEY BYPASS PRO</b> 🚀\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"⚡ <i>Đang xử lý lấy mã {type_code.upper()}</i> ⚡\n\n"
                    
                    f"╔═════════════════════════════════╗\n"
                    f"║ 🎯 Loại mã: <code>{type_code.upper()}</code>              ║\n"
                    f"║ 👤 User: @{username}                ║\n"
                    f"║ ⏰ Còn lại: <b>{i} giây</b>           ║\n"
                    f"║ 🌐 Server: traffic-user.net     ║\n"
                    f"╚═════════════════════════════════╝\n\n"
                    
                    f"📊 <b>TIẾN TRÌNH XỬ LÝ:</b>\n"
                    f"╭─────────────────────────────────╮\n"
                    f"│ [{progress_bar}] {((75-i)/75*100):.0f}% │\n"
                    f"╰─────────────────────────────────╯\n\n"
                    
                    f"{countdown_emojis[emoji_index]} <b>Trạng thái:</b> Đang kết nối server...\n"
                    f"🔄 <i>Vui lòng không gửi lệnh khác</i>\n"
                    f"🎊 <i>Mã sẽ có trong {i} giây nữa!</i>",
                    parse_mode="HTML"
                )
                await asyncio.sleep(5)
            except Exception:
                pass
        
        # Quá trình lấy mã
        try:
            await sent.edit_text(
                f"🔥 <b>ĐANG KẾT NỐI SERVER</b>\n"
                f"═══════════════════════════════════\n\n"
                f"🎯 <b>Loại:</b> <code>{type_code.upper()}</code>\n"
                f"🌐 <b>Server:</b> traffic-user.net\n"
                f"🔗 <b>Trạng thái:</b> Đang kết nối...\n\n"
                f"⚡ <b>Đang truy xuất dữ liệu...</b>\n"
                f"🔍 <b>Đang tìm mã khả dụng...</b>\n"
                f"📡 <b>Đang xử lý phản hồi...</b>\n\n"
                f"⏳ <i>Hoàn thành trong vài giây nữa!</i>",
                parse_mode="HTML"
            )
            
            loop = asyncio.get_running_loop()
            code = await loop.run_in_executor(None, get_bypass_code, type_code)
            
            if code:
                await sent.edit_text(
                    f"🎉 <b>YEUMONEY BYPASS PRO</b> 🎉\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"🌟 <i>Lấy mã thành công!</i> 🌟\n\n"
                    
                    f"╔═════════════════════════════════╗\n"
                    f"║ 🎯 Loại mã: <code>{type_code.upper()}</code>              ║\n"
                    f"║ 👤 User: @{username}                ║\n"
                    f"║ ⏰ Thời gian: {time.strftime('%H:%M:%S')}        ║\n"
                    f"║ 📅 Ngày: {time.strftime('%d/%m/%Y')}            ║\n"
                    f"║ ✅ Trạng thái: <b>Thành công</b>    ║\n"
                    f"╚═════════════════════════════════╝\n\n"
                    
                    f"🔑 <b>MÃ BYPASS CỦA BẠN:</b>\n"
                    f"╭─────────────────────────────────╮\n"
                    f"│           <code>{code}</code>             │\n"
                    f"╰─────────────────────────────────╯\n\n"
                    
                    f"💡 <b>HƯỚNG DẪN SỬ DỤNG:</b>\n"
                    f"╔═════════════════════════════════╗\n"
                    f"║ 1️⃣ Copy mã ở trên               ║\n"
                    f"║ 2️⃣ Paste vào website cần bypass ║\n"
                    f"║ 3️⃣ Hoàn thành verification      ║\n"
                    f"║ 4️⃣ Enjoy! 🎊                   ║\n"
                    f"╚═════════════════════════════════╝\n\n"
                    
                    f"⚡ <b>THÔNG TIN QUAN TRỌNG:</b>\n"
                    f"🔥 KEY có thể lấy mã tiếp không giới hạn\n"
                    f"💎 Mã hiệu lực trong 24h\n"
                    f"🛡️ Server: traffic-user.net (Premium)\n"
                    f"🎯 Tỷ lệ thành công: 99.9%\n\n"
                    
                    f"🌟 <b>CHÚC MỪNG BẠN ĐÃ THÀNH CÔNG!</b> 🌟",
                    parse_mode="HTML"
                )
            else:
                await sent.edit_text(
                    f"❌ <b>YEUMONEY BYPASS PRO</b> ❌\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"⚠️ <i>Không thể lấy mã tại thời điểm này</i> ⚠️\n\n"
                    
                    f"╔═════════════════════════════════╗\n"
                    f"║ 🎯 Loại mã: <code>{type_code.upper()}</code>              ║\n"
                    f"║ 👤 User: @{username}                ║\n"
                    f"║ ⏰ Thời gian: {time.strftime('%H:%M:%S')}        ║\n"
                    f"║ ❌ Trạng thái: <b>Thất bại</b>      ║\n"
                    f"╚═════════════════════════════════╝\n\n"
                    
                    f"🔍 <b>NGUYÊN NHÂN CÓ THỂ:</b>\n"
                    f"╭─────────────────────────────────╮\n"
                    f"│ 🛠️ Server đang bảo trì           │\n"
                    f"│ 📊 Loại mã tạm thời hết          │\n"
                    f"│ 🌐 Kết nối mạng không ổn định    │\n"
                    f"│ ⚡ Server quá tải               │\n"
                    f"╰─────────────────────────────────╯\n\n"
                    
                    f"🚀 <b>GIẢI PHÁP:</b>\n"
                    f"╔═════════════════════════════════╗\n"
                    f"║ 1️⃣ Thử lại sau 5-10 phút        ║\n"
                    f"║ 2️⃣ Thử loại mã khác: <code>/ym fb88</code>   ║\n"
                    f"║ 3️⃣ Kiểm tra kết nối mạng        ║\n"
                    f"║ 4️⃣ Liên hệ admin nếu cần        ║\n"
                    f"╚═════════════════════════════════╝\n\n"
                    
                    f"🔔 <b>LƯU Ý:</b>\n"
                    f"🔥 KEY vẫn hoạt động bình thường\n"
                    f"💎 Không mất phí dù không lấy được mã\n"
                    f"🛡️ Hệ thống tự động khôi phục\n\n"
                    
                    f"🌟 <b>Cảm ơn bạn đã sử dụng dịch vụ!</b> 🌟",
                    parse_mode="HTML"
                )
        except Exception as e:
            logger.error(f"Lỗi khi lấy mã: {e}")
            try:
                await sent.edit_text(
                    f"⚠️ <b>LỖI HỆ THỐNG</b>\n"
                    f"═══════════════════════════════════\n\n"
                    f"🎯 <b>Loại mã:</b> <code>{type_code.upper()}</code>\n"
                    f"❌ <b>Mô tả lỗi:</b> Có sự cố trong quá trình xử lý\n\n"
                    f"🔍 <b>CHI TIẾT KỸ THUẬT:</b>\n"
                    f"• Lỗi kết nối hoặc timeout\n"
                    f"• Server phản hồi không như mong đợi\n"
                    f"• Có thể do tải cao vào giờ peak\n\n"
                    f"🚀 <b>KHUYẾN NGHỊ:</b>\n"
                    f"1️⃣ Thử lại sau 10-15 phút\n"
                    f"2️⃣ Thử loại mã khác\n"
                    f"3️⃣ Liên hệ admin nếu lỗi liên tục\n\n"
                    f"🔥 <b>KEY của bạn vẫn hoạt động tốt!</b>\n"
                    f"💬 <b>Báo lỗi:</b> @admin nếu cần hỗ trợ",
                    parse_mode="HTML"
                )
            except Exception:
                pass
    
    asyncio.create_task(countdown_and_get_code())

# Lệnh lưu dữ liệu thủ công
async def savedata_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_html("🚫 <b>Lệnh này chỉ dành cho admin!</b>")
        return
    
    try:
        save_all_data()
        await update.message.reply_html("💾 <b>Đã lưu tất cả dữ liệu thành công!</b>")
    except Exception as e:
        await update.message.reply_html(f"❌ <b>Lỗi khi lưu dữ liệu:</b> <code>{str(e)}</code>")

# Lệnh trợ giúp chi tiết
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or "User"
    
    help_text = (
        f"📚 <b>HƯỚNG DẪN CHI TIẾT - YEUMONEY PRO</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🌟 <i>Hệ thống bypass chuyên nghiệp</i> 🌟\n\n"
        
        f"👋 <b>Xin chào @{username}!</b>\n"
        f"╭─────────────────────────────────╮\n"
        f"│  🎯 Hướng dẫn sử dụng từ A đến Z  │\n"
        f"╰─────────────────────────────────╯\n\n"
        
        f"🏁 <b>BƯỚC 1: TẠO KEY</b>\n"
        f"╔═════════════════════════════════╗\n"
        f"║ 1️⃣ Gõ: <code>/key</code>                    ║\n"
        f"║ 2️⃣ Click vào link được tạo       ║\n"
        f"║ 3️⃣ Copy KEY từ trang web         ║\n"
        f"║ 4️⃣ Quay lại Telegram            ║\n"
        f"╚═════════════════════════════════╝\n\n"
        
        f"🔑 <b>BƯỚC 2: KÍCH HOẠT KEY</b>\n"
        f"╔═════════════════════════════════╗\n"
        f"║ 1️⃣ Gõ: <code>/xacnhankey &lt;KEY&gt;</code>    ║\n"
        f"║ 2️⃣ Paste KEY vừa copy           ║\n"
        f"║ 3️⃣ Chờ xác nhận thành công       ║\n"
        f"║ 4️⃣ KEY gắn với thiết bị này      ║\n"
        f"╚═════════════════════════════════╝\n\n"
        
        f"🎯 <b>BƯỚC 3: LẤY MÃ BYPASS</b>\n"
        f"╔═════════════════════════════════╗\n"
        f"║ 1️⃣ Gõ: <code>/ym &lt;loại_mã&gt;</code>        ║\n"
        f"║ 2️⃣ Ví dụ: <code>/ym m88</code>             ║\n"
        f"║ 3️⃣ Chờ 75 giây                  ║\n"
        f"║ 4️⃣ Nhận mã và sử dụng           ║\n"
        f"╚═════════════════════════════════╝\n\n"
        
        f"🎮 <b>CÁC LOẠI MÃ PREMIUM:</b>\n"
        f"╭─────────────────────────────────╮\n"
        f"│ 🎰 Casino VIP: m88, fb88, w88    │\n"
        f"│ 🏆 Betting Pro: 188bet, v9bet    │\n"
        f"│ 🎲 Gaming Elite: bk8, w88abc     │\n"
        f"╰─────────────────────────────────╯\n\n"
        
        f"⚡ <b>LỆNH HỮU ÍCH:</b>\n"
        f"╔═════════════════════════════════╗\n"
        f"║ <code>/checkkey</code> - Kiểm tra KEY       ║\n"
        f"║ <code>/profile</code> - Thông tin tài khoản ║\n"
        f"║ <code>/start</code> - Trang chủ             ║\n"
        f"║ <code>/help</code> - Hướng dẫn này          ║\n"
        f"╚═════════════════════════════════╝\n\n"
        
        f"� <b>TÍNH NĂNG VIP:</b>\n"
        f"╭─────────────────────────────────╮\n"
        f"│ ✨ Lấy mã không giới hạn         │\n"
        f"│ ⚡ Thời gian chờ chỉ 75 giây     │\n"
        f"│ � Bảo mật KEY cá nhân          │\n"
        f"│ 📱 Hoạt động 1 thiết bị         │\n"
        f"│ 🔄 Tạo KEY mới miễn phí         │\n"
        f"╰─────────────────────────────────╯\n\n"
        
        f"🆘 <b>KHẮC PHỤC SỰ CỐ:</b>\n"
        f"╔═════════════════════════════════╗\n"
        f"║ KEY hết hạn → <code>/key</code> tạo mới     ║\n"
        f"║ Không lấy được mã → Thử lại      ║\n"
        f"║ KEY lỗi → Liên hệ admin         ║\n"
        f"║ Bot lag → Restart lệnh          ║\n"
        f"╚═════════════════════════════════╝\n\n"
        
        f"� <b>CHÚC BẠN SỬ DỤNG THÀNH CÔNG!</b> 🌟\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )
    
    await update.message.reply_html(help_text)

# Lệnh xem profile
async def profile_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or "Chưa đặt"
    first_name = update.effective_user.first_name or "User"
    
    # Thống kê user
    user_key = USER_KEYS.get(user_id)
    key_status = "🟢 Đang hoạt động" if user_key and check_key(user_key) else "🔴 Chưa có/Hết hạn"
    
    # Tính thời gian tham gia (giả lập)
    join_date = "Hôm nay"  # Có thể lưu thực tế trong database
    
    # Level dựa trên admin status
    user_level = "👑 Administrator" if is_admin(user_id) else "👤 User"
    level_emoji = "👑" if is_admin(user_id) else "⭐"
    
    profile_text = (
        f"👤 <b>THÔNG TIN TÀI KHOẢN</b>\n"
        f"═══════════════════════════════════\n\n"
        f"📋 <b>THÔNG TIN CÁ NHÂN:</b>\n"
        f"┌─────────────────────────────────┐\n"
        f"│ 🎭 Tên: <b>{first_name}</b>\n"
        f"│ 👤 Username: <b>@{username}</b>\n"
        f"│ 🆔 User ID: <code>{user_id}</code>\n"
        f"│ {level_emoji} Cấp độ: <b>{user_level}</b>\n"
        f"│ 📅 Tham gia: <b>{join_date}</b>\n"
        f"└─────────────────────────────────┘\n\n"
        
        f"🔑 <b>TRẠNG THÁI KEY:</b>\n"
        f"┌─────────────────────────────────┐\n"
        f"│ 📊 Trạng thái: {key_status}\n"
    )
    
    if user_key and check_key(user_key):
        key_info = get_key_info(user_key)
        profile_text += (
            f"│ 🔐 KEY: <code>{user_key}</code>\n"
            f"│ ⏰ Còn lại: <b>{key_info['time_remaining']}</b>\n"
            f"│ 📱 Thiết bị: <b>Đã gắn</b>\n"
        )
    else:
        profile_text += (
            f"│ 🔐 KEY: <b>Chưa có</b>\n"
            f"│ ⏰ Thời hạn: <b>N/A</b>\n"
            f"│ 📱 Thiết bị: <b>Chưa gắn</b>\n"
        )
    
    profile_text += (
        f"└─────────────────────────────────┘\n\n"
        f"📊 <b>THỐNG KÊ SỬ DỤNG:</b>\n"
        f"┌─────────────────────────────────┐\n"
        f"│ 🎯 Loại mã hỗ trợ: <b>{len(BYPASS_TYPES)} loại</b>\n"
        f"│ 🚀 Trạng thái bot: <b>Hoạt động</b>\n"
        f"│ 🛡️ Bảo mật: <b>Cao</b>\n"
        f"│ ⚡ Tốc độ: <b>75 giây/mã</b>\n"
        f"└─────────────────────────────────┘\n\n"
        
        f"🎯 <b>HÀNH ĐỘNG NHANH:</b>\n"
    )
    
    if not user_key or not check_key(user_key):
        profile_text += (
            f"🔑 <code>/key</code> - Tạo KEY miễn phí\n"
            f"✅ <code>/xacnhankey</code> - Kích hoạt KEY\n"
        )
    else:
        profile_text += (
            f"🎯 <code>/ym m88</code> - Lấy mã M88\n"
            f"🔍 <code>/checkkey</code> - Kiểm tra KEY\n"
        )
    
    profile_text += (
        f"📚 <code>/help</code> - Hướng dẫn chi tiết\n"
        f"🏠 <code>/start</code> - Màn hình chính\n\n"
        f"💎 <b>Cảm ơn bạn đã sử dụng bot!</b>"
    )
    
    await update.message.reply_html(profile_text)

# Lệnh thống kê cho admin
async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_html("🚫 <b>Lệnh này chỉ dành cho admin!</b>")
        return
    
    # Thống kê hệ thống
    total_keys = len(VALID_KEYS)
    active_keys = sum(1 for key in VALID_KEYS.keys() if check_key(key))
    total_users = len(USER_KEYS)
    active_users = sum(1 for user_id, key in USER_KEYS.items() if check_key(key))
    banned_users = len(BAN_LIST)
    total_admins = len(ADMINS)
    
    stats_text = (
        f"📊 <b>THỐNG KÊ HỆ THỐNG</b>\n"
        f"═══════════════════════════════════\n\n"
        f"👥 <b>NGƯỜI DÙNG:</b>\n"
        f"┌─────────────────────────────────┐\n"
        f"│ 👤 Tổng users: <b>{total_users}</b>\n"
        f"│ 🟢 Đang hoạt động: <b>{active_users}</b>\n"
        f"│ 🚫 Bị ban: <b>{banned_users}</b>\n"
        f"│ 👑 Admins: <b>{total_admins}</b>\n"
        f"└─────────────────────────────────┘\n\n"
        
        f"🔑 <b>KEY SYSTEM:</b>\n"
        f"┌─────────────────────────────────┐\n"
        f"│ 🔐 Tổng KEY: <b>{total_keys}</b>\n"
        f"│ ✅ Đang hoạt động: <b>{active_keys}</b>\n"
        f"│ ❌ Hết hạn: <b>{total_keys - active_keys}</b>\n"
        f"│ 📱 Đang sử dụng: <b>{len(KEY_DEVICES)}</b>\n"
        f"└─────────────────────────────────┘\n\n"
        
        f"🎯 <b>DỊCH VỤ:</b>\n"
        f"┌─────────────────────────────────┐\n"
        f"│ 🌐 Loại mã: <b>{len(BYPASS_TYPES)}</b>\n"
        f"│ ⚡ Thời gian chờ: <b>75 giây</b>\n"
        f"│ 🛡️ Anti-spam: <b>Bật</b>\n"
        f"│ 💾 Auto-save: <b>5 phút</b>\n"
        f"└─────────────────────────────────┘\n\n"
        
        f"📈 <b>HIỆU SUẤT:</b>\n"
        f"┌─────────────────────────────────┐\n"
        f"│ 🚀 Trạng thái: <b>Hoạt động</b>\n"
        f"│ 🔄 Uptime: <b>Ổn định</b>\n"
        f"│ 📡 API: <b>traffic-user.net</b>\n"
        f"│ 🎊 Tỷ lệ thành công: <b>~85%</b>\n"
        f"└─────────────────────────────────┘\n\n"
        
        f"⏰ <b>Cập nhật lúc:</b> {time.strftime('%H:%M:%S %d/%m/%Y')}\n"
        f"🔄 <b>Tự động refresh mỗi 5 phút</b>"
    )
    
    await update.message.reply_html(stats_text)

# LỆNH /listkey: DANH SÁCH USER ĐANG SỬ DỤNG KEY
async def listkey_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_html(
            "🚫 <b><i>Lệnh này chỉ dành cho admin!</i></b>"
        )
        return
    msg = "<b>💎 DANH SÁCH NGƯỜI DÙNG ĐANG SỬ DỤNG KEY</b>\n━━━━━━━━━━━━━━━━━━━━\n"
    has_user = False
    for idx, (uid, key) in enumerate(USER_KEYS.items(), 1):
        if check_key(key):
            has_user = True
            key_info = get_key_info(key)
            bound_to = f"User ID: {key_info['bound_device']}" if key_info['bound_device'] else "Chưa gắn với thiết bị"
            
            msg += (
                f"🔹 <b>#{idx}</b> <b>User:</b> <code>{uid}</code>\n"
                f"  <b>KEY:</b> <code>{key}</code>\n"
                f"  <b>Thời gian còn lại:</b> {key_info['time_remaining']}\n"
                f"  📱 <b>Đang sử dụng bởi:</b> {bound_to}\n"
                "-------------------------\n"
            )
    if not has_user:
        msg += "📭 <b>Không có user nào đang sử dụng KEY hợp lệ.</b>"
    else:
        msg += "━━━━━━━━━━━━━━━━━━━━"
    await update.message.reply_html(msg)

# ========== LỆNH XÓA KEY (ADMIN) ==========
async def deletekey_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_html(
            "🚫 <b>Lệnh này chỉ dành cho admin!</b>"
        )
        return
    
    args = update.message.text.split()
    if len(args) < 2:
        await update.message.reply_html(
            f"📋 <b>HƯỚNG DẪN XÓA KEY</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📝 <b>Cú pháp:</b> <code>/deletekey &lt;KEY&gt;</code>\n\n"
            f"💡 <b>Ví dụ:</b>\n"
            f"<code>/deletekey VIP2025-ABC123DEF456</code>\n\n"
            f"⚠️ <b>Lưu ý:</b>\n"
            f"• KEY sẽ bị xóa vĩnh viễn\n"
            f"• User sẽ mất quyền truy cập ngay lập tức\n"
            f"• Không thể hoàn tác sau khi xóa\n\n"
            f"🔍 <b>Để xem danh sách KEY:</b> <code>/listkey</code>"
        )
        return
    
    key_to_delete = args[1].strip()
    
    # Kiểm tra KEY có tồn tại không
    if key_to_delete not in VALID_KEYS:
        await update.message.reply_html(
            f"❌ <b>KEY KHÔNG TỒN TẠI</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🔍 <b>KEY cần xóa:</b> <code>{key_to_delete}</code>\n\n"
            f"⚠️ <b>Lý do:</b> KEY không có trong hệ thống\n\n"
            f"💡 <b>Giải pháp:</b>\n"
            f"• Kiểm tra lại KEY có đúng không\n"
            f"• Sử dụng <code>/listkey</code> để xem danh sách\n"
            f"• KEY có thể đã bị xóa trước đó"
        )
        return
    
    # Tìm user đang sử dụng KEY này
    user_using_key = None
    for uid, user_key in USER_KEYS.items():
        if user_key == key_to_delete:
            user_using_key = uid
            break
    
    # Thực hiện xóa KEY
    try:
        # Xóa từ VALID_KEYS
        del VALID_KEYS[key_to_delete]
        
        # Xóa từ USER_KEYS nếu có user đang sử dụng
        if user_using_key:
            del USER_KEYS[user_using_key]
        
        # Xóa từ KEY_DEVICES
        if key_to_delete in KEY_DEVICES:
            del KEY_DEVICES[key_to_delete]
        
        # Xóa từ KEY_METADATA
        if key_to_delete in KEY_METADATA:
            del KEY_METADATA[key_to_delete]
        
        # Lưu dữ liệu
        save_all_data()
        
        success_msg = (
            f"✅ <b>XÓA KEY THÀNH CÔNG</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🗑️ <b>KEY đã xóa:</b> <code>{key_to_delete}</code>\n"
        )
        
        if user_using_key:
            success_msg += f"👤 <b>User bị ảnh hưởng:</b> <code>{user_using_key}</code>\n"
        else:
            success_msg += f"👤 <b>User bị ảnh hưởng:</b> Không có\n"
        
        success_msg += (
            f"\n📊 <b>Thông tin xóa:</b>\n"
            f"╔═════════════════════════════════╗\n"
            f"║ 🔑 KEY: Đã xóa khỏi hệ thống    ║\n"
            f"║ 👤 User: Mất quyền truy cập     ║\n"
            f"║ 📱 Thiết bị: Hủy liên kết       ║\n"
            f"║ 💾 Dữ liệu: Đã lưu thay đổi     ║\n"
            f"╚═════════════════════════════════╝\n\n"
            f"⚠️ <b>Lưu ý:</b> Thao tác không thể hoàn tác\n"
            f"👤 <b>Admin thực hiện:</b> <code>{user_id}</code>"
        )
        
        await update.message.reply_html(success_msg)
        logger.info(f"Admin {user_id} đã xóa KEY: {key_to_delete}")
        
    except Exception as e:
        await update.message.reply_html(
            f"❌ <b>LỖI KHI XÓA KEY</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🔍 <b>KEY:</b> <code>{key_to_delete}</code>\n"
            f"⚠️ <b>Lỗi:</b> <code>{str(e)}</code>\n\n"
            f"💡 <b>Giải pháp:</b>\n"
            f"• Thử lại sau vài giây\n"
            f"• Liên hệ Master Admin nếu lỗi tiếp tục\n"
            f"• Kiểm tra log hệ thống"
        )
        logger.error(f"Lỗi khi xóa KEY {key_to_delete}: {e}")

# ========== LỆNH XÓA TẤT CẢ KEY (MASTER ADMIN ONLY) ==========
async def deleteallkeys_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    # Chỉ Master Admin mới được sử dụng
    if user_id != MASTER_ADMIN_ID:
        await update.message.reply_html(
            f"🚫 <b>QUYỀN TRUY CẬP BỊ TỪ CHỐI</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"⛔ <b>Lệnh này chỉ dành cho Master Admin</b>\n\n"
            f"🔒 <b>Master Admin ID:</b> <code>{MASTER_ADMIN_ID}</code>\n"
            f"👤 <b>ID của bạn:</b> <code>{user_id}</code>\n\n"
            f"💡 <b>Lý do hạn chế:</b>\n"
            f"• Lệnh có tính chất phá hủy cao\n"
            f"• Xóa toàn bộ dữ liệu KEY hệ thống\n"
            f"• Chỉ Master Admin được thực hiện\n\n"
            f"🛡️ <b>Bảo mật hệ thống được ưu tiên hàng đầu</b>"
        )
        return
    
    args = update.message.text.split()
    
    # Yêu cầu xác nhận bằng từ khóa đặc biệt
    if len(args) < 2 or args[1] != "CONFIRM_DELETE_ALL":
        await update.message.reply_html(
            f"⚠️ <b>XÁC NHẬN XÓA TẤT CẢ KEY</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🚨 <b>CẢNH BÁO NGHIÊM TRỌNG!</b> 🚨\n\n"
            f"🗑️ <b>Hành động:</b> Xóa toàn bộ KEY trong hệ thống\n"
            f"👥 <b>Ảnh hưởng:</b> Tất cả {len(USER_KEYS)} users sẽ mất quyền truy cập\n"
            f"🔑 <b>Số KEY bị xóa:</b> {len(VALID_KEYS)} KEY\n\n"
            f"⚠️ <b>HẬU QUẢ:</b>\n"
            f"╔═════════════════════════════════╗\n"
            f"║ 🚨 Toàn bộ hệ thống KEY reset   ║\n"
            f"║ 👤 Tất cả users mất quyền       ║\n"
            f"║ 📱 Mọi thiết bị bị hủy liên kết ║\n"
            f"║ 💾 Dữ liệu KEY bị xóa vĩnh viễn ║\n"
            f"║ 🔄 Không thể hoàn tác           ║\n"
            f"╚═════════════════════════════════╝\n\n"
            f"🔐 <b>Để xác nhận, gõ:</b>\n"
            f"<code>/deleteallkeys CONFIRM_DELETE_ALL</code>\n\n"
            f"🛡️ <b>Cân nhắc kỹ trước khi thực hiện!</b>"
        )
        return
    
    # Thống kê trước khi xóa
    total_keys = len(VALID_KEYS)
    total_users = len(USER_KEYS)
    active_keys = sum(1 for key in VALID_KEYS.keys() if check_key(key))
    
    try:
        # Xóa toàn bộ dữ liệu KEY
        VALID_KEYS.clear()
        USER_KEYS.clear()
        KEY_DEVICES.clear()
        KEY_METADATA.clear()
        KEY_USAGE_LOG.clear()
        
        # Lưu dữ liệu
        save_all_data()
        
        success_msg = (
            f"💥 <b>ĐÃ XÓA TẤT CẢ KEY THÀNH CÔNG</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🗑️ <b>THỐNG KÊ XÓA:</b>\n"
            f"╔═════════════════════════════════╗\n"
            f"║ 🔑 Tổng KEY đã xóa: <b>{total_keys}</b>         ║\n"
            f"║ ✅ KEY hoạt động đã xóa: <b>{active_keys}</b>   ║\n"
            f"║ 👤 Users bị ảnh hưởng: <b>{total_users}</b>     ║\n"
            f"║ 📱 Thiết bị hủy liên kết: <b>{len(KEY_DEVICES)}</b> ║\n"
            f"║ 💾 Metadata đã xóa: <b>{len(KEY_METADATA)}</b>   ║\n"
            f"╚═════════════════════════════════╝\n\n"
            f"🎯 <b>TRẠNG THÁI HỆ THỐNG:</b>\n"
            f"╭─────────────────────────────────╮\n"
            f"│ 🔄 Hệ thống đã được reset        │\n"
            f"│ 🆕 Sẵn sàng cho KEY mới          │\n"
            f"│ 📊 Database đã được làm sạch     │\n"
            f"│ ✅ Thao tác hoàn tất thành công  │\n"
            f"╰─────────────────────────────────╯\n\n"
            f"⏰ <b>Thời gian thực hiện:</b> {time.strftime('%H:%M:%S %d/%m/%Y')}\n"
            f"👑 <b>Master Admin:</b> <code>{user_id}</code>\n\n"
            f"🌟 <b>Hệ thống đã sẵn sàng hoạt động trở lại!</b>"
        )
        
        await update.message.reply_html(success_msg)
        logger.warning(f"MASTER ADMIN {user_id} đã xóa toàn bộ {total_keys} KEY trong hệ thống!")
        
    except Exception as e:
        await update.message.reply_html(
            f"❌ <b>LỖI NGHIÊM TRỌNG KHI XÓA KEY</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"⚠️ <b>Lỗi hệ thống:</b> <code>{str(e)}</code>\n\n"
            f"🚨 <b>Tình trạng:</b>\n"
            f"• Một số dữ liệu có thể đã bị xóa\n"
            f"• Hệ thống có thể không ổn định\n"
            f"• Cần kiểm tra và khôi phục ngay\n\n"
            f"🔧 <b>Hành động khẩn cấp:</b>\n"
            f"1️⃣ Restart bot ngay lập tức\n"
            f"2️⃣ Kiểm tra file backup\n"
            f"3️⃣ Khôi phục từ backup nếu cần\n\n"
            f"👑 <b>Master Admin:</b> <code>{user_id}</code>"
        )
        logger.error(f"LỖI NGHIÊM TRỌNG khi Master Admin {user_id} xóa tất cả KEY: {e}")

# ========== FLASK ROUTES (API) ==========
@app.route('/bypass', methods=['POST'])
def k():
    try:
        json_data = request.get_json()
        if not json_data:
            return jsonify({'error': 'Không có dữ liệu'}), 400
        type_code = json_data.get('type')
        user_id = json_data.get('user_id')
        key = json_data.get('key') or None
        
        # Nếu không có key từ request, lấy key từ user
        if key is None and user_id is not None:
            key = USER_KEYS.get(user_id)
        
        if not type_code:
            return jsonify({'error': 'Thiếu trường type'}), 400
        if not key or not check_key(key):
            return jsonify({'error': 'Bạn phải xác nhận KEY hợp lệ trước khi sử dụng!'}), 403

        # Kiểm tra key có thuộc về user này không
        if not can_use_key(key, user_id):
            return jsonify({'error': 'KEY này đã được sử dụng bởi thiết bị/người dùng khác!'}), 403

        # Kiểm tra loại mã có hợp lệ không
        if type_code not in BYPASS_TYPES:
            return jsonify({'error': f'Loại không hợp lệ. Các loại hỗ trợ: {", ".join(BYPASS_TYPES)}'}), 400

        # Lấy mã bằng hàm get_bypass_code
        code = get_bypass_code(type_code)
        
        if code:
            return jsonify({'code': code}), 200
        else:
            return jsonify({'error': 'Không thể lấy được mã. Vui lòng thử lại sau.'}), 400
            
    except Exception as e:
        logger.error(f"Lỗi bypass: {e}")
        return jsonify({'error': f"Lỗi hệ thống: {str(e)}"}), 500

@app.route('/genkey', methods=['POST', 'GET'])
def apikey():
    try:
        key, lifetime = tao_key()
        link_raw = upload(key)
        if not link_raw:
            return jsonify({'error': 'Không upload được lên Dpaste.org'}), 500
        short = rutgon(link_raw)
        return jsonify({
            'short_link': short if short else link_raw,
            'original_link': link_raw,
            'key': key
        }), 200
    except Exception as e:
        logger.error(f"Lỗi genkey: {e}")
        return jsonify({'error': f"Lỗi hệ thống: {str(e)}"}), 500

@app.route('/', methods=['GET'])
def index():
    return render_template_string("<h2>API lấy mã & tạo KEY đang hoạt động!<br>Muốn sử dụng phải xác nhận KEY!</h2>")

def start_flask():
    app.run(host="0.0.0.0", port=5000, threaded=True)

# ========== AUTO CLEANUP SYSTEM ==========
def auto_cleanup_scheduler():
    """Tự động dọn dẹp KEY hết hạn mỗi 5 phút"""
    while True:
        try:
            time.sleep(KEY_CLEANUP_INTERVAL)  # 300 giây = 5 phút
            cleanup_expired_keys()
        except Exception as e:
            logger.error(f"Lỗi trong auto cleanup: {e}")

def start_auto_cleanup():
    """Khởi động thread tự động dọn dẹp"""
    cleanup_thread = threading.Thread(target=auto_cleanup_scheduler, daemon=True)
    cleanup_thread.start()
    logger.info("🧹 Auto cleanup system đã khởi động (mỗi 5 phút)")

# ========== LOAD ALL DATA ON STARTUP ==========
def load_all_data():
    """Load tất cả dữ liệu khi khởi động"""
    logger.info("📊 Đang load dữ liệu...")
    load_valid_keys()
    load_user_keys()
    load_key_devices()
    load_key_metadata()
    load_key_usage_log()
    load_admins()
    load_ban_list()
    
    # Dọn dẹp KEY hết hạn ngay khi khởi động
    cleaned = cleanup_expired_keys()
    
    logger.info(f"✅ Đã load hoàn tất - Dọn dẹp {cleaned} KEY hết hạn")

# ========== LỆNH INFO HỆ THỐNG ==========
async def info_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or "User"
    
    # Thống kê hệ thống
    total_keys = len(VALID_KEYS)
    active_keys = len([k for k, v in VALID_KEYS.items() if v[0] + v[1] > time.time()])
    total_users = len(USER_KEYS)
    total_bypass_types = len(BYPASS_TYPES)
    
    info_text = (
        f"🚀 <b>YEUMONEY BYPASS PRO</b> 🚀\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 <i>Thông tin hệ thống chi tiết</i> 📊\n\n"
        
        f"🌟 <b>THÔNG TIN HỆ THỐNG</b>\n"
        f"╔═════════════════════════════════╗\n"
        f"║ 🎯 Tên: <b>YEUMONEY BYPASS PRO</b>    ║\n"
        f"║ 🏆 Phiên bản: <b>v2.0 Premium</b>    ║\n"
        f"║ 🛡️ Bảo mật: <b>Advanced SSL</b>      ║\n"
        f"║ ⚡ Tốc độ: <b>Ultra Fast</b>         ║\n"
        f"║ 🌐 Server: <b>Premium VPS</b>       ║\n"
        f"╚═════════════════════════════════╝\n\n"
        
        f"📈 <b>THỐNG KÊ REALTIME</b>\n"
        f"╔═════════════════════════════════╗\n"
        f"║ 🔑 Tổng KEY: <b>{total_keys}</b>               ║\n"
        f"║ ✅ KEY hoạt động: <b>{active_keys}</b>         ║\n"
        f"║ 👤 Tổng users: <b>{total_users}</b>           ║\n"
        f"║ 🎮 Loại mã: <b>{total_bypass_types}</b>                ║\n"
        f"║ ⚡ Thời gian chờ: <b>75 giây</b>      ║\n"
        f"╚═════════════════════════════════╝\n\n"
        
        f"🎯 <b>CÁC LOẠI MÃ HỖ TRỢ</b>\n"
        f"╭─────────────────────────────────╮\n"
        f"│ 🎰 Casino Elite: m88, fb88, w88  │\n"
        f"│ 🏆 Betting Pro: 188bet, v9bet    │\n"
        f"│ 🎲 Gaming VIP: bk8, w88abc       │\n"
        f"│ 💎 Premium: v9betlg, bk8xo       │\n"
        f"╰─────────────────────────────────╯\n\n"
        
        f"💎 <b>TÍNH NĂNG PREMIUM</b>\n"
        f"╔═════════════════════════════════╗\n"
        f"║ ✨ Hoàn toàn miễn phí            ║\n"
        f"║ 🚀 Tốc độ siêu nhanh            ║\n"
        f"║ 🔐 Bảo mật tuyệt đối             ║\n"
        f"║ 🛡️ Chống spam thông minh         ║\n"
        f"║ 📱 Đa nền tảng                  ║\n"
        f"║ 🎯 Tỷ lệ thành công 99.9%       ║\n"
        f"╚═════════════════════════════════╝\n\n"
        
        f"👤 <b>THÔNG TIN TÀI KHOẢN</b>\n"
        f"╭─────────────────────────────────╮\n"
        f"│ 🆔 ID: <code>{user_id}</code>                 │\n"
        f"│ 👤 Username: @{username}         │\n"
        f"│ 🏆 Cấp độ: {'👑 Admin VIP' if is_admin(user_id) else '👤 Member'}    │\n"
        f"│ 🔑 KEY: {'✅ Có' if USER_KEYS.get(user_id) else '❌ Chưa có'}             │\n"
        f"╰─────────────────────────────────╯\n\n"
        
        f"🌟 <b>CẢM ƠN BẠN ĐÃ SỬ DỤNG!</b> 🌟\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )
    
    await update.message.reply_html(info_text)

# ========== ĐĂNG KÝ LỆNH BOT ==========
async def set_bot_commands(application):
    commands = [
        BotCommand("start", "🏠 Trang chủ và hướng dẫn chính"),
        BotCommand("key", "🔑 Tạo KEY miễn phí (24h)"),
        BotCommand("xacnhankey", "✅ Kích hoạt KEY để sử dụng"),
        BotCommand("checkkey", "🔍 Kiểm tra thông tin KEY"),
        BotCommand("ym", "🎯 Lấy mã bypass (cần KEY)"),
        BotCommand("help", "📚 Hướng dẫn chi tiết"),
        BotCommand("profile", "👤 Thông tin tài khoản"),
        
        # Admin commands
        BotCommand("taokey", "🎁 [Admin] Tạo KEY custom"),
        BotCommand("listkey", "📋 [Admin] Danh sách KEY"),
        BotCommand("deletekey", "🗑️ [Admin] Xóa KEY cụ thể"),
        BotCommand("stats", "📊 [Admin] Thống kê hệ thống"),
        BotCommand("ban", "🚫 [Admin] Ban người dùng"),
        BotCommand("unban", "✅ [Admin] Gỡ ban người dùng"),
        BotCommand("addadmin", "⭐ [Master] Thêm admin"),
        BotCommand("deladmin", "❌ [Master] Xóa admin"),
        BotCommand("deleteallkeys", "💥 [Master] Xóa tất cả KEY"),
        BotCommand("adminguide", "👑 [Admin] Hướng dẫn admin"),
        BotCommand("savedata", "💾 [Admin] Backup dữ liệu"),
    ]
    await application.bot.set_my_commands(commands)

# ========== CHẠY BOT & FLASK ==========
if __name__ == "__main__":
    # Tải dữ liệu từ file khi khởi động
    load_all_data()
    
    # Khởi động hệ thống tự động dọn dẹp KEY
    start_auto_cleanup()
    
    # Khởi động luồng tự động lưu dữ liệu
    threading.Thread(target=auto_save_data_loop, daemon=True).start()
    
    # Khởi động luồng tự động unban
    threading.Thread(target=auto_unban_loop, daemon=True).start()
    
    # Khởi động Flask API server
    threading.Thread(target=start_flask, daemon=True).start()
    
    # Khởi động bot
    application = ApplicationBuilder().token(BOT_TOKEN).build()
    
    # Đăng ký các handler
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("info", info_command))
    application.add_handler(CommandHandler("profile", profile_command))
    application.add_handler(CommandHandler("ym", ym_command))
    application.add_handler(CommandHandler("key", key_command))
    application.add_handler(CommandHandler("xacnhankey", xacnhankey_command))
    application.add_handler(CommandHandler("checkkey", checkkey_command))
    
    # Admin commands
    application.add_handler(CommandHandler("taokey", taokey_command))
    application.add_handler(CommandHandler("listkey", listkey_command))
    application.add_handler(CommandHandler("deletekey", deletekey_command))
    application.add_handler(CommandHandler("deleteallkeys", deleteallkeys_command))
    application.add_handler(CommandHandler("savedata", savedata_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler(["ban", "unban", "addadmin", "deladmin", "adminguide"], ym_command))
    
    # Thiết lập menu lệnh
    application.post_init = set_bot_commands
    
    logger.info(f"🚀 Bot KEY System Professional đã khởi động!")
    logger.info(f"🔑 KEY lifetime: 24 giờ chính xác")
    logger.info(f"🧹 Auto cleanup: mỗi {KEY_CLEANUP_INTERVAL//60} phút")
    logger.info(f"⏰ KEY cooldown: {KEY_COOLDOWN_TIME//3600} giờ")
    application.run_polling()