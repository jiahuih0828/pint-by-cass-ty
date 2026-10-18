# app.py is the Python side of the website.
# It receives form entries from index.html, checks them, stores them in SQLite,
# and sends calculated information back to the page.
# This version supports two password-protected partner accounts. On a hosted
# setup, both partners connect to the same persistent SQLite database.

# Standard-library imports: these come with Python, so Flask is the only extra package.
# date checks dates; Decimal keeps money calculations accurate to cents.

from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from io import BytesIO
import json
from pathlib import Path
import os
import secrets
import sqlite3
import time
from uuid import uuid4
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from flask import Flask, abort, flash, g, redirect, render_template, request, send_file, session, url_for
from flask_login import LoginManager, UserMixin, current_user, login_user, login_required, logout_user
from flask_wtf.csrf import CSRFProtect
from PIL import Image, ImageOps, UnidentifiedImageError
from werkzeug.security import check_password_hash


# PERSONAL SETTINGS ---------------------------------------------------------
# Change these values to match your business and the names you use together.
BUSINESS_NAME = "Pint"
CURRENCY_CODE = "SGD"
CURRENCY_SYMBOL = "S$"
PARTNERS = ["Cass", "Ty"]
COST_CATEGORIES = ["Ingredients", "Packaging", "Labor", "Delivery", "Equipment", "Other"]
# Each production batch may contain several 400 g pints of one flavour. Prices are whole cents.
FLAVOR_PRICE_CENTS = {"Milo": 150, "Cookies and cream": 150, "Chocolate": 150, "Banana pudding": 200}
# Short public menu descriptions; edit these to match your exact recipes.
FLAVOR_DESCRIPTIONS = {
    "Milo": "Chocolate malt flavour with a smooth, creamy finish.",
    "Cookies and cream": "Creamy ice cream with Oreo cookie pieces.",
    "Chocolate": "Rich chocolate ice cream; Nutella biscuits are an optional mix-in.",
    "Banana pudding": "A smooth, banana-forward flavour inspired by classic banana pudding.",
}
# Recipe choices are stored on each batch, so customers only choose a version
# that you have actually made and still have in stock.
FLAVOR_RECIPE_OPTIONS = {
    "Cookies and cream": ["Oreo without filling", "Oreo with filling"],
    "Chocolate": ["With Nutella biscuit", "Without Nutella biscuit"],
}
# These describe the batches already made when recipe tracking is first added.
CURRENT_BATCH_RECIPES = {
    "Cookies and cream": "Oreo without filling",
    "Chocolate": "With Nutella biscuit",
}
# Current whole-pint customer price and recipe size (400 g = eight 50 g scoops).
STANDARD_PINT_SIZE_G = 400
STANDARD_PINT_PRICE_CENTS = 1000
# Nutrition figures are for a 400 g recipe. A 50 g scoop is shown as one eighth.
# The approximate values are marked with "approximate" as in the recipe notes.
# Milo and Banana pudding values are approximate estimates supplied by the owners.
NUTRITION_VARIANTS = {
    "Milo": [
        {"variant": "Base recipe", "calories_kcal": 313, "protein_g": 11.7,
         "carbs_g": 48.4, "fat_g": 7.5, "approximate": True},
    ],
    "Cookies and cream": [
        {"variant": "Oreo with filling", "calories_kcal": 346.5, "protein_g": 34.8,
         "carbs_g": 50.18, "fat_g": 9.15, "approximate": False},
        {"variant": "Oreo without filling", "calories_kcal": 310.5, "protein_g": 34.6,
         "carbs_g": 48, "fat_g": 6.25, "approximate": True},
    ],
    "Chocolate": [
        {"variant": "With Nutella biscuit", "calories_kcal": 290.5, "protein_g": 32.8,
         "carbs_g": 41.78, "fat_g": 7.45, "approximate": False},
        {"variant": "Without Nutella biscuit", "calories_kcal": 219.5, "protein_g": 31.7,
         "carbs_g": 32.98, "fat_g": 4.05, "approximate": False},
    ],
    "Banana pudding": [
        {"variant": "Base recipe", "calories_kcal": 395, "protein_g": 14.6,
         "carbs_g": 51.5, "fat_g": 15.1, "approximate": True},
    ],
}
# Pint sales are not open yet; switch this on when you are ready to launch them.
PINT_ORDERS_ENABLED = False
PINT_REWARD_READY = PINT_ORDERS_ENABLED and STANDARD_PINT_SIZE_G > 0 and STANDARD_PINT_PRICE_CENTS > 0

# Path(__file__) means "the folder where this app.py file lives." The database
# will be saved inside that folder, in a folder named instance.
BASE_DIR = Path(__file__).resolve().parent
# Read settings from a local .env file when learning locally. The online host
# supplies these same settings in its private environment-variable panel.
load_dotenv(BASE_DIR / ".env")
DATABASE = Path(os.environ.get("TRACKER_DATABASE", str(BASE_DIR / "instance" / "tracker.sqlite3")))
PAYMENT_RECEIPTS_FOLDER = Path(os.environ.get(
    "PAYMENT_RECEIPTS_FOLDER", str(BASE_DIR / "instance" / "payment_receipts")
))
# Flavour pictures uploaded by the owners. On a host, point this at the persistent disk.
FLAVOUR_PHOTOS_FOLDER = Path(os.environ.get(
    "FLAVOUR_PHOTOS_FOLDER", str(BASE_DIR / "instance" / "flavour_photos")
))
# Each owner can have as many pickup areas as they like.
PICKUP_OWNERS = ["Cass", "Tyler"]
PAYMENT_INSTRUCTIONS = os.environ.get(
    "PAYMENT_INSTRUCTIONS",
    "Add your PayNow or bank-transfer instructions before sharing this order page.",
)

# Create the Flask app object. Flask uses it to connect URL paths (like /sales)
# to Python functions below. The secret key protects browser sessions and
# one-time feedback messages; it is separate from the partner passwords.
app = Flask(__name__)
IS_PRODUCTION = os.environ.get("APP_ENV") == "production"
app.secret_key = os.environ.get("FLASK_SECRET_KEY")
if not app.secret_key:
    if IS_PRODUCTION:
        raise RuntimeError("Set FLASK_SECRET_KEY in the hosting service's private settings.")
    app.secret_key = secrets.token_hex(32)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=IS_PRODUCTION,
    PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
    MAX_CONTENT_LENGTH=5 * 1024 * 1024,
)

# CSRF protection checks a hidden token on every form that changes data.
CSRFProtect(app)

# Flask-Login remembers which partner has signed in using a protected session.
login_manager = LoginManager()
login_manager.login_view = "login"
login_manager.init_app(app)


class Partner(UserMixin):
    """A tiny login identity; account passwords are never saved in SQLite."""

    def __init__(self, username):
        self.id = username
        self.display_name = next((name for name in PARTNERS if name.lower() == username), username)


def password_hash_for(username):
    """Find the password hash supplied privately by the owner/host."""
    return os.environ.get(f"{username.upper()}_PASSWORD_HASH", "")


if IS_PRODUCTION:
    missing_password_settings = [
        f"{name.upper()}_PASSWORD_HASH" for name in PARTNERS if not password_hash_for(name.lower())
    ]
    if missing_password_settings:
        raise RuntimeError("Missing partner password hashes: " + ", ".join(missing_password_settings))


@login_manager.user_loader
def load_partner(username):
    """Rebuild the signed-in partner identity from the browser session."""
    if username in {name.lower() for name in PARTNERS} and password_hash_for(username):
        return Partner(username)
    return None


def get_db():
    """Open one database connection for this browser request, when needed."""
    # `g` is Flask's temporary storage for the current request. Reusing the
    # connection avoids opening a new file connection for every SQL command.
    if "db" not in g:
        # Make the instance folder the first time the app runs.
        DATABASE.parent.mkdir(parents=True, exist_ok=True)
        # sqlite3 is built into Python. The database is an ordinary local file.
        g.db = sqlite3.connect(DATABASE)
        # This makes rows behave like dictionaries: row["product"] is readable.
        g.db.row_factory = sqlite3.Row
        # Ask SQLite to enforce the relationship from sales/costs to batches.
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def shop_flavours():
    """Return the owner-managed public flavour list as {name: scoop price}."""
    return {row["name"]: row["price_cents"] for row in get_db().execute(
        "SELECT name, price_cents FROM shop_flavours WHERE active = 1 ORDER BY rowid"
    ).fetchall()}


def shop_settings():
    """Read the small set of owner-editable choices for the customer shop."""
    saved = {row["setting_key"]: row["setting_value"] for row in get_db().execute(
        "SELECT setting_key, setting_value FROM shop_settings"
    ).fetchall()}
    return {
        "allow_event": saved.get("allow_event", "0") == "1",
        "allow_pickup": saved.get("allow_pickup", "1") == "1",
        "allow_scoop": saved.get("allow_scoop", "1") == "1",
        "allow_personal": saved.get("allow_personal", "1") == "1",
        "allow_pint": saved.get("allow_pint", "0") == "1",
        "telegram_url": saved.get("telegram_url", ""),
        "payment_qr": saved.get("payment_qr", ""),
        "payment_instructions": saved.get("payment_instructions", "") or PAYMENT_INSTRUCTIONS,
        "event_menu_json": saved.get("event_menu", ""),
    }


def recipe_options_map():
    """{flavour: [mix-in versions]}; a renamed flavour keeps the options it started with."""
    result = {}
    for row in get_db().execute("SELECT name, recipe_key FROM shop_flavours").fetchall():
        key = row["recipe_key"] or row["name"]
        if key in FLAVOR_RECIPE_OPTIONS:
            result[row["name"]] = FLAVOR_RECIPE_OPTIONS[key]
    return result


def event_menu():
    """{flavour: the exact version sold at events}; flavours the owners switched off are left out."""
    try:
        saved = json.loads(shop_settings()["event_menu_json"] or "{}")
        if not isinstance(saved, dict):
            saved = {}
    except json.JSONDecodeError:
        saved = {}
    options = recipe_options_map()
    menu = {}
    for name in shop_flavours():
        entry = saved.get(name) if isinstance(saved.get(name), dict) else {}
        if entry.get("on") is False:
            continue
        choices = options.get(name, [])
        variant = entry.get("variant")
        if variant not in choices:
            variant = choices[0] if choices else "Base recipe"
        menu[name] = variant
    return menu


def save_payment_qr(upload):
    """Store the payment QR code as a lossless PNG so it stays scannable; return its filename."""
    if Path(upload.filename).suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
        raise ValueError("Upload your QR code as a PNG, JPG or WebP image.")
    try:
        raw_image = upload.read()
        with Image.open(BytesIO(raw_image)) as picture:
            if picture.format not in {"JPEG", "PNG", "WEBP"} or picture.width * picture.height > 30_000_000:
                raise ValueError("Use a PNG, JPG or WebP image smaller than 30 megapixels.")
            picture.verify()
        with Image.open(BytesIO(raw_image)) as picture:
            picture = ImageOps.exif_transpose(picture)
            picture.thumbnail((1400, 1400))
            if picture.mode in ("RGBA", "LA", "P"):
                picture = picture.convert("RGBA")
                backdrop = Image.new("RGB", picture.size, (255, 255, 255))
                backdrop.paste(picture, mask=picture.getchannel("A"))
                picture = backdrop
            cleaned = BytesIO()
            picture.convert("RGB").save(cleaned, format="PNG", optimize=True)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise ValueError("We couldn't read that image. Please choose a clear PNG, JPG or WebP file.")
    FLAVOUR_PHOTOS_FOLDER.mkdir(parents=True, exist_ok=True)
    filename = f"qr-{uuid4().hex}.png"
    (FLAVOUR_PHOTOS_FOLDER / filename).write_bytes(cleaned.getvalue())
    return filename


def pickup_areas():
    """Every pickup area the owners have added, Cass first, then Tyler."""
    return get_db().execute(
        "SELECT id, owner, name FROM pickup_areas ORDER BY CASE owner WHEN 'Cass' THEN 0 ELSE 1 END, id"
    ).fetchall()


def flavour_photos():
    """Return {flavour name: saved picture filename} for flavours that have a picture."""
    return {row["name"]: row["photo_filename"] for row in get_db().execute(
        "SELECT name, photo_filename FROM shop_flavours WHERE active = 1 AND photo_filename != ''"
    ).fetchall()}


def save_flavour_photo(upload):
    """Check, clean (resize, strip metadata) and store one flavour picture; return its filename."""
    if Path(upload.filename).suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
        raise ValueError("Upload a JPG, PNG or WebP picture.")
    try:
        raw_image = upload.read()
        with Image.open(BytesIO(raw_image)) as picture:
            if picture.format not in {"JPEG", "PNG", "WEBP"} or picture.width * picture.height > 30_000_000:
                raise ValueError("Use a JPG, PNG or WebP picture smaller than 30 megapixels.")
            picture.verify()
        with Image.open(BytesIO(raw_image)) as picture:
            picture = ImageOps.exif_transpose(picture)
            picture.thumbnail((1400, 1400))
            if picture.mode in ("RGBA", "LA", "P"):
                picture = picture.convert("RGBA")
                backdrop = Image.new("RGB", picture.size, (255, 255, 255))
                backdrop.paste(picture, mask=picture.getchannel("A"))
                picture = backdrop
            cleaned = BytesIO()
            picture.convert("RGB").save(cleaned, format="JPEG", quality=85, optimize=True)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise ValueError("We couldn't read that picture. Please choose a clear JPG, PNG or WebP image.")
    FLAVOUR_PHOTOS_FOLDER.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid4().hex}.jpg"
    (FLAVOUR_PHOTOS_FOLDER / filename).write_bytes(cleaned.getvalue())
    return filename


def remove_flavour_photo_file(filename):
    """Delete a stored picture file (ignored if it is already gone)."""
    if filename:
        path = FLAVOUR_PHOTOS_FOLDER / Path(filename).name
        if path.is_file():
            path.unlink()


def shop_descriptions():
    rows = get_db().execute("SELECT name, description FROM shop_flavours WHERE active = 1").fetchall()
    return {row["name"]: row["description"] for row in rows}


def singapore_today():
    """Use the business's Singapore calendar date for pickup rules."""
    return datetime.now(ZoneInfo("Asia/Singapore")).date()


@app.teardown_appcontext
def close_db(_error=None):
    """Close the connection when Flask finishes handling this request."""
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    """Create the three tables the first time the app runs.

    CREATE TABLE IF NOT EXISTS means existing data is left alone on later runs.
    Money is stored as whole cents (for example, S$4.25 is stored as 425).
    """
    db = get_db()
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS batches (
            -- A database-generated number is the internal link to this batch.
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            -- The short ID you choose, such as B-001, must be unique.
            code TEXT NOT NULL UNIQUE,
            product TEXT NOT NULL,
            made_on TEXT,
            prepared_by TEXT NOT NULL DEFAULT '',
            -- A production batch may contain several pints of the same flavour.
            pints_made INTEGER NOT NULL DEFAULT 1,
            scoops_per_pint INTEGER NOT NULL DEFAULT 8,
            recipe_variant TEXT NOT NULL DEFAULT 'Base recipe'
        );
        CREATE TABLE IF NOT EXISTS shop_flavours (
            name TEXT PRIMARY KEY,
            price_cents INTEGER NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            active INTEGER NOT NULL DEFAULT 1,
            photo_filename TEXT NOT NULL DEFAULT '',
            recipe_key TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS pickup_areas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner TEXT NOT NULL,
            name TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS shop_settings (
            setting_key TEXT PRIMARY KEY,
            setting_value TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS costs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            purchased_on TEXT NOT NULL,
            -- A NULL batch_id means this is a shared/general business cost.
            batch_id INTEGER REFERENCES batches(id) ON DELETE RESTRICT,
            category TEXT NOT NULL,
            description TEXT NOT NULL,
            quantity REAL NOT NULL,
            unit_cost_cents INTEGER NOT NULL,
            total_cents INTEGER NOT NULL,
            paid_by TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sold_on TEXT NOT NULL,
            batch_id INTEGER NOT NULL REFERENCES batches(id) ON DELETE RESTRICT,
            flavour TEXT NOT NULL,
            quantity REAL NOT NULL,
            unit_price_cents INTEGER NOT NULL,
            total_cents INTEGER NOT NULL,
            buyer TEXT NOT NULL,
            sold_by TEXT NOT NULL,
            notes TEXT NOT NULL DEFAULT '',
            unit_type TEXT NOT NULL DEFAULT 'scoop'
        );
        CREATE TABLE IF NOT EXISTS order_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            confirmation_code TEXT NOT NULL UNIQUE,
            placed_at TEXT NOT NULL,
            customer_name TEXT NOT NULL,
            contact TEXT NOT NULL,
            contact_method TEXT NOT NULL DEFAULT 'Phone / email',
            flavour TEXT NOT NULL,
            recipe_variant TEXT NOT NULL DEFAULT 'Base recipe',
            scoops REAL NOT NULL,
            unit_type TEXT NOT NULL DEFAULT 'scoop',
            unit_price_cents INTEGER NOT NULL,
            total_cents INTEGER NOT NULL,
            pickup_date TEXT NOT NULL DEFAULT '',
            pickup_time TEXT NOT NULL DEFAULT '',
            payment_reference TEXT NOT NULL DEFAULT '',
            notes TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'Awaiting payment screenshot',
            sale_id INTEGER REFERENCES sales(id),
            receipt_filename TEXT NOT NULL DEFAULT '',
            subtotal_cents INTEGER NOT NULL DEFAULT 0,
            discount_cents INTEGER NOT NULL DEFAULT 0,
            reward_points INTEGER NOT NULL DEFAULT 0,
            free_pint_flavour TEXT NOT NULL DEFAULT '',
            points_earned INTEGER NOT NULL DEFAULT 0,
            pint_sale_id INTEGER REFERENCES sales(id),
            contact_key TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS order_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL REFERENCES order_requests(id) ON DELETE CASCADE,
            flavour TEXT NOT NULL,
            recipe_variant TEXT NOT NULL DEFAULT 'Base recipe',
            quantity REAL NOT NULL,
            unit_type TEXT NOT NULL,
            unit_price_cents INTEGER NOT NULL,
            sale_id INTEGER REFERENCES sales(id)
        );
        CREATE TABLE IF NOT EXISTS loyalty_customers (
            contact_key TEXT PRIMARY KEY,
            contact TEXT NOT NULL,
            contact_method TEXT NOT NULL DEFAULT 'Phone / email',
            customer_name TEXT NOT NULL,
            points INTEGER NOT NULL DEFAULT 0,
            reserved_points INTEGER NOT NULL DEFAULT 0,
            lifetime_earned INTEGER NOT NULL DEFAULT 0,
            lifetime_redeemed INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS loyalty_ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            contact_key TEXT NOT NULL REFERENCES loyalty_customers(contact_key),
            changed_at TEXT NOT NULL,
            order_id INTEGER NOT NULL REFERENCES order_requests(id),
            points_change INTEGER NOT NULL,
            reason TEXT NOT NULL,
            balance_after INTEGER NOT NULL
        );
        """
    )
    # Older databases may have a shop_flavours table without these columns; add them so public pages never fail.
    flavour_columns = {row["name"] for row in db.execute("PRAGMA table_info(shop_flavours)").fetchall()}
    if "description" not in flavour_columns:
        db.execute("ALTER TABLE shop_flavours ADD COLUMN description TEXT NOT NULL DEFAULT ''")
    if "active" not in flavour_columns:
        db.execute("ALTER TABLE shop_flavours ADD COLUMN active INTEGER NOT NULL DEFAULT 1")
    if "photo_filename" not in flavour_columns:
        db.execute("ALTER TABLE shop_flavours ADD COLUMN photo_filename TEXT NOT NULL DEFAULT ''")
    if "recipe_key" not in flavour_columns:
        db.execute("ALTER TABLE shop_flavours ADD COLUMN recipe_key TEXT NOT NULL DEFAULT ''")
    # The four starter flavours are added once only, so renaming or deleting one sticks.
    if not db.execute("SELECT 1 FROM shop_settings WHERE setting_key = 'starter_flavours_added'").fetchone():
        for flavour, price in FLAVOR_PRICE_CENTS.items():
            db.execute("INSERT OR IGNORE INTO shop_flavours (name, price_cents, description) VALUES (?, ?, ?)",
                       (flavour, price, FLAVOR_DESCRIPTIONS.get(flavour, "")))
        db.execute("INSERT INTO shop_settings (setting_key, setting_value) VALUES ('starter_flavours_added', '1')")
    for key, value in {"allow_event": "0", "allow_pickup": "1", "allow_scoop": "1", "allow_personal": "1",
                       "allow_pint": "0", "telegram_url": "", "event_menu": "", "payment_qr": "",
                       "payment_instructions": ""}.items():
        db.execute("INSERT OR IGNORE INTO shop_settings (setting_key, setting_value) VALUES (?, ?)", (key, value))
    # Add the flavour column for anyone who already ran the earlier starter.
    sales_columns = {row["name"] for row in db.execute("PRAGMA table_info(sales)").fetchall()}
    if "flavour" not in sales_columns:
        db.execute("ALTER TABLE sales ADD COLUMN flavour TEXT NOT NULL DEFAULT ''")
        db.execute("""UPDATE sales SET flavour = (
                         SELECT product FROM batches WHERE batches.id = sales.batch_id
                     ) WHERE flavour = ''""")
    if "unit_type" not in sales_columns:
        db.execute("ALTER TABLE sales ADD COLUMN unit_type TEXT NOT NULL DEFAULT 'scoop'")
    batch_columns = {row["name"] for row in db.execute("PRAGMA table_info(batches)").fetchall()}
    if "prepared_by" not in batch_columns:
        db.execute("ALTER TABLE batches ADD COLUMN prepared_by TEXT NOT NULL DEFAULT ''")
    if "pints_made" not in batch_columns:
        # Old records used one row per pint, so each old row becomes one pint.
        db.execute("ALTER TABLE batches ADD COLUMN pints_made INTEGER NOT NULL DEFAULT 1")
    if "scoops_per_pint" not in batch_columns:
        # Start old records at the previous estimate; new batches can adjust it.
        db.execute("ALTER TABLE batches ADD COLUMN scoops_per_pint INTEGER NOT NULL DEFAULT 8")
    if "recipe_variant" not in batch_columns:
        # Preserve what's already in the freezer according to Cass and Ty.
        db.execute("ALTER TABLE batches ADD COLUMN recipe_variant TEXT NOT NULL DEFAULT 'Base recipe'")
        for flavour, variant in CURRENT_BATCH_RECIPES.items():
            db.execute("UPDATE batches SET recipe_variant = ? WHERE product = ?", (variant, flavour))
    # Existing order tables receive private receipt storage as an added field.
    order_columns = {row["name"] for row in db.execute("PRAGMA table_info(order_requests)").fetchall()}
    if "receipt_filename" not in order_columns:
        db.execute("ALTER TABLE order_requests ADD COLUMN receipt_filename TEXT NOT NULL DEFAULT ''")
    for column, declaration in {
        "unit_type": "TEXT NOT NULL DEFAULT 'scoop'",
        "contact_method": "TEXT NOT NULL DEFAULT 'Phone / email'",
        "subtotal_cents": "INTEGER NOT NULL DEFAULT 0",
        "discount_cents": "INTEGER NOT NULL DEFAULT 0",
        "reward_points": "INTEGER NOT NULL DEFAULT 0",
        "free_pint_flavour": "TEXT NOT NULL DEFAULT ''",
        "points_earned": "INTEGER NOT NULL DEFAULT 0",
        "pint_sale_id": "INTEGER REFERENCES sales(id)",
        "contact_key": "TEXT NOT NULL DEFAULT ''",
        "fulfilment_method": "TEXT NOT NULL DEFAULT 'pickup'",
        "pickup_location": "TEXT NOT NULL DEFAULT ''",
    }.items():
        if column not in order_columns:
            db.execute(f"ALTER TABLE order_requests ADD COLUMN {column} {declaration}")
    if "recipe_variant" not in order_columns:
        db.execute("ALTER TABLE order_requests ADD COLUMN recipe_variant TEXT NOT NULL DEFAULT 'Base recipe'")
        # Older unconfirmed orders were placed before the recipe choice existed.
        # Align them with the batches that were already prepared at that time.
        for flavour, variant in CURRENT_BATCH_RECIPES.items():
            db.execute("UPDATE order_requests SET recipe_variant = ? WHERE flavour = ?", (variant, flavour))
    loyalty_columns = {row["name"] for row in db.execute("PRAGMA table_info(loyalty_customers)").fetchall()}
    if "contact_method" not in loyalty_columns:
        db.execute("ALTER TABLE loyalty_customers ADD COLUMN contact_method TEXT NOT NULL DEFAULT 'Phone / email'")
    # Bring earlier starter orders into the screenshot-review workflow.
    db.execute("UPDATE order_requests SET status = 'Awaiting payment screenshot' WHERE status = 'Awaiting payment'")
    # Convert old one-flavour orders into one order line without duplicates.
    db.execute("""INSERT INTO order_items (order_id, flavour, recipe_variant, quantity, unit_type, unit_price_cents)
                  SELECT o.id, o.flavour, o.recipe_variant, o.scoops, o.unit_type, o.unit_price_cents
                  FROM order_requests o WHERE NOT EXISTS
                  (SELECT 1 FROM order_items i WHERE i.order_id = o.id)""")
    # One-time move from the two fixed pickup areas to a list of pickup areas.
    if not db.execute("SELECT 1 FROM shop_settings WHERE setting_key = 'pickup_areas_migrated'").fetchone():
        old = {row["setting_key"]: row["setting_value"] for row in db.execute(
            "SELECT setting_key, setting_value FROM shop_settings WHERE setting_key IN ('cass_location', 'tyler_location')"
        ).fetchall()}
        cass_area = old.get("cass_location") or "AMK"
        tyler_area = old.get("tyler_location") or "Serangoon"
        db.execute("INSERT INTO pickup_areas (owner, name) VALUES ('Cass', ?)", (cass_area,))
        db.execute("INSERT INTO pickup_areas (owner, name) VALUES ('Tyler', ?)", (tyler_area,))
        # Orders now keep the readable area name, so later edits never change old orders.
        db.execute("UPDATE order_requests SET pickup_location = ? WHERE pickup_location = 'cass_amk'", (f"Cass · {cass_area}",))
        db.execute("UPDATE order_requests SET pickup_location = ? WHERE pickup_location = 'tyler_serangoon'", (f"Tyler · {tyler_area}",))
        db.execute("INSERT INTO shop_settings (setting_key, setting_value) VALUES ('pickup_areas_migrated', '1')")
    # Save the table setup to disk.
    db.commit()


def money_to_cents(value, label):
    """Convert a user-entered dollar amount into whole cents for storage."""
    try:
        # Decimal avoids common floating-point rounding surprises in money.
        amount = Decimal(value.strip())
        if not amount.is_finite() or amount < 0:
            raise ValueError
        return int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    except (InvalidOperation, AttributeError, ValueError):
        raise ValueError(f"Enter a valid non-negative amount for {label}.")


def positive_quantity(value):
    """Check that an entered quantity is a real number above zero."""
    try:
        quantity = Decimal(value.strip())
        if not quantity.is_finite() or quantity <= 0:
            raise ValueError
        return float(quantity)
    except (InvalidOperation, AttributeError, ValueError):
        raise ValueError("Quantity must be greater than zero.")


def valid_date(value, label):
    """Check that a date from an HTML date field is valid."""
    try:
        return date.fromisoformat(value).isoformat()
    except (TypeError, ValueError):
        raise ValueError(f"Choose a valid date for {label}.")


def money(cents):
    """Turn stored cents back into a friendly display such as S$1,234.56."""
    return f"{CURRENCY_SYMBOL}{(cents or 0) / 100:,.2f}"


def loyalty_key(contact):
    """Use a normalized phone/email as the customer's simple loyalty ID."""
    return "".join(contact.casefold().split()).replace("-", "").replace("(", "").replace(")", "")


def nutrition_display(value, places=2):
    """Format known nutrition values and label missing ones without guessing."""
    return "Not provided" if value is None else f"{value:.{places}f}".rstrip("0").rstrip(".")


def remaining_batch_scoops(db, batch_id):
    """Return this production batch's total unsold 50 g scoop portions."""
    batch = db.execute("SELECT pints_made, scoops_per_pint FROM batches WHERE id = ?", (batch_id,)).fetchone()
    if not batch:
        return 0
    sold = db.execute(
        """SELECT COALESCE(SUM(quantity * CASE WHEN unit_type = 'pint' THEN ? WHEN unit_type = 'personal' THEN 2 ELSE 1 END), 0)
           FROM sales WHERE batch_id = ?""", (batch["scoops_per_pint"], batch_id)
    ).fetchone()[0]
    return max(0, batch["pints_made"] * batch["scoops_per_pint"] - sold)


def available_scoops_by_flavour():
    """Add up unsold scoop portions across every production batch by flavour."""
    db = get_db()
    stock = {flavour: 0 for flavour in shop_flavours()}
    for batch in db.execute("SELECT id, product FROM batches").fetchall():
        if batch["product"] in stock:
            stock[batch["product"]] += remaining_batch_scoops(db, batch["id"])
    return stock


def largest_batch_stock_by_flavour():
    """Return the most scoops an order can use from one matching batch."""
    db = get_db()
    largest = {flavour: 0 for flavour in shop_flavours()}
    for batch in db.execute("SELECT id, product FROM batches").fetchall():
        if batch["product"] in largest:
            largest[batch["product"]] = max(
                largest[batch["product"]], remaining_batch_scoops(db, batch["id"])
            )
    return largest


def available_recipe_stock():
    """Return remaining stock grouped by flavour and exact recipe version."""
    db = get_db()
    stock = {flavour: {} for flavour in shop_flavours()}
    for batch in db.execute("SELECT id, product, recipe_variant FROM batches").fetchall():
        if batch["product"] in stock:
            variant = batch["recipe_variant"] or "Base recipe"
            stock[batch["product"]][variant] = (
                stock[batch["product"]].get(variant, 0)
                + remaining_batch_scoops(db, batch["id"])
            )
    return stock


def largest_batch_stock_for_recipe(flavour, recipe_variant=""):
    """Maximum order size available from one batch of this exact recipe."""
    db = get_db()
    largest = 0
    for batch in db.execute(
        "SELECT id, recipe_variant FROM batches WHERE product = ?", (flavour,)
    ).fetchall():
        if recipe_variant and batch["recipe_variant"] != recipe_variant:
            continue
        largest = max(largest, remaining_batch_scoops(db, batch["id"]))
    return largest


def largest_batch_pints_for_recipe(flavour, recipe_variant=""):
    """Maximum whole pints available from one matching batch."""
    db = get_db()
    largest = 0
    for batch in db.execute(
        "SELECT id, recipe_variant, scoops_per_pint FROM batches WHERE product = ?", (flavour,)
    ).fetchall():
        if recipe_variant and batch["recipe_variant"] != recipe_variant:
            continue
        remaining = remaining_batch_scoops(db, batch["id"])
        largest = max(largest, remaining // max(1, batch["scoops_per_pint"]))
    return largest


# A Jinja filter is a small display helper used in index.html like `amount|money`.
app.jinja_env.filters["money"] = money
app.jinja_env.filters["nutrition"] = nutrition_display


@app.before_request
def ensure_database():
    # This runs before each page/form request; it makes sure the tables exist.
    init_db()


@app.route("/login", methods=["GET", "POST"])
def login():
    """Sign in one of the two partners using that partner's password."""
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    if request.method == "POST":
        username = request.form.get("username", "").strip().lower()
        password = request.form.get("password", "")
        if username in {name.lower() for name in PARTNERS} and password_hash_for(username):
            if check_password_hash(password_hash_for(username), password):
                login_user(Partner(username), remember=False, fresh=True)
                session.permanent = True
                return redirect(url_for("index"))
        # Same message for unknown names and wrong passwords, so the page does
        # not tell strangers which partner accounts exist.
        time.sleep(0.4)
        flash("That owner name or password did not match.", "error")
    return render_template("login.html", business_name=BUSINESS_NAME, partners=PARTNERS)


@app.post("/logout")
@login_required
def logout():
    """End this browser's signed-in session."""
    logout_user()
    flash("You have signed out.", "success")
    return redirect(url_for("login"))


@app.get("/payment-qr/<filename>")
def payment_qr_image(filename):
    """The payment QR code customers scan after ordering."""
    path = FLAVOUR_PHOTOS_FOLDER / Path(filename).name
    if not path.is_file() or not path.name.startswith("qr-"):
        abort(404)
    return send_file(path, mimetype="image/png", max_age=3600)


@app.errorhandler(413)
def upload_too_large(_error):
    flash("That file is too large. Please use a picture under 5 MB.", "error")
    return redirect(request.referrer or url_for("index"))


@app.get("/order")
def order_home():
    """Welcome page for the public shop, with links to the menu and details."""
    return render_template("order_home.html", business_name=BUSINESS_NAME)


@app.get("/order/nutrition")
def order_nutrition():
    """Show whole-recipe nutrition values for each flavour."""
    current_flavours = shop_flavours()
    recipe_keys = {row["name"]: (row["recipe_key"] or row["name"]) for row in get_db().execute(
        "SELECT name, recipe_key FROM shop_flavours").fetchall()}
    nutrition = {name: NUTRITION_VARIANTS.get(recipe_keys.get(name, name), [{
        "variant": "Base recipe", "calories_kcal": None, "protein_g": None,
        "carbs_g": None, "fat_g": None, "approximate": False,
    }]) for name in current_flavours}
    ranked_rows = sorted(((name, facts) for name, variants in nutrition.items() for facts in variants),
                         key=lambda row: (row[1]["protein_g"] is None, -(row[1]["protein_g"] or 0)))
    return render_template("order_nutrition.html", business_name=BUSINESS_NAME,
                           nutrition_variants=nutrition, ranked_rows=ranked_rows)


@app.get("/order/menu")
def order_menu():
    """List the available flavours without price labels in the order options."""
    return render_template("order_menu.html", business_name=BUSINESS_NAME,
                           flavours=list(shop_flavours()), flavour_photos=flavour_photos(),
                           flavor_descriptions=shop_descriptions(), shop=shop_settings())


@app.get("/flavour-photos/<filename>")
def flavour_photo(filename):
    """Public flavour pictures shown on the customer menu."""
    path = FLAVOUR_PHOTOS_FOLDER / Path(filename).name
    if not path.is_file():
        abort(404)
    return send_file(path, mimetype="image/jpeg", max_age=86400)


@app.get("/order/prices")
def order_prices():
    """Show the prices separately from the customer order form."""
    return render_template("order_prices.html", business_name=BUSINESS_NAME,
                           flavor_prices=shop_flavours(), shop=shop_settings())


@app.route("/order/place", methods=["GET", "POST"])
def customer_order():
    """Public flavour menu and pickup-order request form for customers."""
    prices = shop_flavours()
    shop = shop_settings()
    pint_reward_ready = shop["allow_pint"] and STANDARD_PINT_SIZE_G > 0 and STANDARD_PINT_PRICE_CENTS > 0
    min_pickup_date = (singapore_today() + timedelta(days=4)).isoformat()
    descriptions = shop_descriptions()
    if request.method == "POST":
        try:
            customer_name = request.form.get("customer_name", "").strip()
            contact = request.form.get("contact", "").strip()
            # A single contact field supports phone/email or a Telegram @username.
            contact_method = "Telegram" if contact.startswith("@") else "Phone / email"
            fulfilment_method = request.form.get("fulfilment_method", "pickup")
            pickup_location = request.form.get("pickup_location", "")
            if not customer_name or len(customer_name) > 100:
                raise ValueError("Enter your name (up to 100 characters).")
            if not contact or len(contact) > 120:
                raise ValueError("Enter a contact detail so we can confirm your order.")
            if contact_method == "Telegram" and not contact.startswith("@"):
                contact = "@" + contact
            if request.form.get("action") == "check_rewards":
                contact_id_for_check = (f"telegram:{loyalty_key(contact)}" if contact_method == "Telegram"
                                        else loyalty_key(contact))
                account = get_db().execute(
                    "SELECT points, reserved_points, lifetime_earned FROM loyalty_customers WHERE contact_key = ?",
                    (contact_id_for_check,),
                ).fetchone()
                available_points = max(0, account["points"] - account["reserved_points"]) if account else 0
                # Do not show a balance until at least one earlier order earned points.
                loyalty_active = bool(account and account["lifetime_earned"] > 0)
                if not loyalty_active:
                    available_points = None
                eligible_rewards = [tier for tier in (50, 75) if available_points is not None and available_points >= tier]
                if pint_reward_ready and available_points is not None and available_points >= 100:
                    eligible_rewards.append(100)
                return render_template(
                    "order.html", business_name=BUSINESS_NAME, currency_symbol=CURRENCY_SYMBOL,
                    flavor_prices=prices, pint_price_cents=STANDARD_PINT_PRICE_CENTS,
                    pint_reward_ready=pint_reward_ready, form_data=request.form,
                    recipe_options=recipe_options_map(),
                    pickup_areas=pickup_areas(), event_menu=event_menu(),
                    available_points=available_points, eligible_rewards=eligible_rewards,
                    loyalty_active=loyalty_active, shop=shop, min_pickup_date=min_pickup_date,
                    flavour_descriptions=descriptions,
                )
            if fulfilment_method not in {m for m, enabled in (("pickup", shop["allow_pickup"]), ("event", shop["allow_event"])) if enabled}:
                raise ValueError("Choose an available order option.")
            try:
                submitted_items = json.loads(request.form.get("items_json", "[]"))
            except (TypeError, json.JSONDecodeError):
                submitted_items = []
            if not isinstance(submitted_items, list) or not 1 <= len(submitted_items) <= 10:
                raise ValueError("Add between 1 and 10 flavour selections to your order.")
            allowed_units = {unit for unit, enabled in (("scoop", shop["allow_scoop"]), ("personal", shop["allow_personal"]), ("pint", shop["allow_pint"])) if enabled}
            order_lines = []
            subtotal_cents = 0
            events_on_sale = event_menu()
            for chosen in submitted_items:
                if not isinstance(chosen, dict):
                    raise ValueError("Each selection must include a flavour, size, and quantity.")
                flavour = str(chosen.get("flavour", ""))
                unit_type = str(chosen.get("unit_type", ""))
                if flavour not in prices or unit_type not in allowed_units:
                    raise ValueError("Choose an available flavour and order size for every selection.")
                quantity_value = positive_quantity(str(chosen.get("quantity", "")))
                if not quantity_value.is_integer() or quantity_value > 100:
                    raise ValueError("Each flavour quantity must be a whole number from 1 to 100.")
                if fulfilment_method == "event":
                    # Event orders: the owners choose what is sold, so customers cannot personalise it.
                    if flavour not in events_on_sale:
                        raise ValueError(f"{flavour} is not being sold at our event.")
                    recipe_variant = events_on_sale[flavour]
                    if unit_type == "pint":
                        if quantity_value > largest_batch_pints_for_recipe(flavour, recipe_variant):
                            raise ValueError(f"We don't have enough {flavour} pints for that event order.")
                        unit_price_cents = STANDARD_PINT_PRICE_CENTS
                    else:
                        scoop_equivalent = 2 if unit_type == "personal" else 1
                        if quantity_value * scoop_equivalent > largest_batch_stock_for_recipe(flavour, recipe_variant):
                            raise ValueError(f"We don't have enough {flavour} left for that event order.")
                        unit_price_cents = prices[flavour] * scoop_equivalent
                else:
                    # Pickup orders are accepted even with no stock: batches are made after customers order.
                    recipe_options = recipe_options_map().get(flavour, [])
                    if len(recipe_options) > 1:
                        recipe_variant = str(chosen.get("recipe_variant", ""))
                        if recipe_variant not in recipe_options:
                            raise ValueError(f"Choose a mix-in option for {flavour}.")
                    elif recipe_options:
                        recipe_variant = recipe_options[0]
                    else:
                        recipe_variant = "Base recipe"
                    if unit_type == "pint":
                        unit_price_cents = STANDARD_PINT_PRICE_CENTS
                    else:
                        scoop_equivalent = 2 if unit_type == "personal" else 1
                        unit_price_cents = prices[flavour] * scoop_equivalent
                if unit_price_cents <= 0:
                    raise ValueError("That order size is not available for sale yet.")
                line_subtotal = int((Decimal(str(quantity_value)) * unit_price_cents).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
                subtotal_cents += line_subtotal
                order_lines.append({"flavour": flavour, "recipe_variant": recipe_variant,
                                    "quantity": quantity_value, "unit_type": unit_type,
                                    "unit_price_cents": unit_price_cents, "subtotal_cents": line_subtotal})
            flavour = order_lines[0]["flavour"]
            recipe_variant = order_lines[0]["recipe_variant"]
            quantity = order_lines[0]["quantity"]
            unit_type = order_lines[0]["unit_type"]
            unit_price_cents = order_lines[0]["unit_price_cents"]
            pickup_date = request.form.get("pickup_date", "").strip()
            if fulfilment_method == "pickup":
                area = None
                if pickup_location.isdigit():
                    area = get_db().execute("SELECT owner, name FROM pickup_areas WHERE id = ?", (int(pickup_location),)).fetchone()
                if not area:
                    raise ValueError("Choose one of the available pickup areas.")
                pickup_location = f"{area['owner']} · {area['name']}"
                if not pickup_date:
                    raise ValueError("Choose a weekday pickup date at least 4 days from today.")
                pickup_date = valid_date(pickup_date, "pickup date")
                chosen_date = date.fromisoformat(pickup_date)
                if chosen_date < singapore_today() + timedelta(days=4):
                    raise ValueError("Pickup dates must be at least 4 days after the order date.")
                if chosen_date.weekday() >= 5:
                    raise ValueError("Pickup is available on weekdays only. Please choose Monday to Friday.")
            else:
                pickup_date = ""
                pickup_location = ""
            pickup_time = request.form.get("pickup_time", "").strip()[:30] if fulfilment_method == "pickup" else ""
            if pickup_time:
                try:
                    parsed_time = datetime.strptime(pickup_time, "%H:%M")
                except ValueError:
                    raise ValueError("Choose a pickup time between 9 am and 5 pm.")
                if not 9 * 60 <= parsed_time.hour * 60 + parsed_time.minute <= 17 * 60:
                    raise ValueError("Choose a pickup time between 9 am and 5 pm.")
            payment_reference = request.form.get("payment_reference", "").strip()[:100]
            notes = request.form.get("notes", "").strip()[:200]
            contact_id = f"telegram:{loyalty_key(contact)}" if contact_method == "Telegram" else loyalty_key(contact)
            reward_points = int(request.form.get("reward_points", "0") or 0)
            reward_options = {0: 0, 50: 50, 75: 65, 100: 0}
            if reward_points not in reward_options or (reward_points == 100 and not pint_reward_ready):
                raise ValueError("Choose a valid loyalty reward.")
            free_pint_flavour = request.form.get("free_pint_flavour", "") if reward_points == 100 else ""
            if reward_points == 100 and free_pint_flavour not in prices:
                raise ValueError("Choose a flavour for your free pint.")
            if reward_points:
                customer = get_db().execute(
                    "SELECT points, reserved_points FROM loyalty_customers WHERE contact_key = ?", (contact_id,)
                ).fetchone()
                available = (customer["points"] - customer["reserved_points"]) if customer else 0
                if available < reward_points:
                    raise ValueError(f"This contact has {available} available points, which is not enough for that reward.")
            discount_cents = (subtotal_cents * reward_options[reward_points]) // 100
            total_cents = subtotal_cents - discount_cents
            confirmation_code = secrets.token_urlsafe(18)
            db = get_db()
            db.execute("BEGIN IMMEDIATE")
            # Recheck the balance while holding a write lock so two simultaneous
            # order requests cannot spend the same points.
            if reward_points:
                customer = db.execute("SELECT points, reserved_points FROM loyalty_customers WHERE contact_key = ?", (contact_id,)).fetchone()
                available = (customer["points"] - customer["reserved_points"]) if customer else 0
                if available < reward_points:
                    raise ValueError(f"This contact has {available} available points, which is not enough for that reward.")
                db.execute("UPDATE loyalty_customers SET reserved_points = reserved_points + ? WHERE contact_key = ?", (reward_points, contact_id))
            db.execute(
                """INSERT INTO loyalty_customers (contact_key, contact, contact_method, customer_name)
                   VALUES (?, ?, ?, ?) ON CONFLICT(contact_key) DO UPDATE SET contact = excluded.contact,
                   contact_method = excluded.contact_method, customer_name = excluded.customer_name""",
                (contact_id, contact, contact_method, customer_name)
            )
            cursor = db.execute(
                """INSERT INTO order_requests
                   (confirmation_code, placed_at, customer_name, contact, flavour, recipe_variant, scoops,
                    unit_price_cents, total_cents, pickup_date, pickup_time, payment_reference, notes, contact_method, unit_type,
                    subtotal_cents, discount_cents, reward_points, free_pint_flavour, contact_key,
                    fulfilment_method, pickup_location)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (confirmation_code, singapore_today().isoformat(), customer_name, contact, flavour, recipe_variant, quantity,
                 unit_price_cents, total_cents, pickup_date, pickup_time, payment_reference, notes, contact_method, unit_type,
                 subtotal_cents, discount_cents, reward_points, free_pint_flavour, contact_id,
                 fulfilment_method, pickup_location),
            )
            order_id = cursor.lastrowid
            db.executemany(
                """INSERT INTO order_items (order_id, flavour, recipe_variant, quantity, unit_type, unit_price_cents)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                [(order_id, line["flavour"], line["recipe_variant"], line["quantity"], line["unit_type"], line["unit_price_cents"])
                 for line in order_lines],
            )
            db.commit()
            return redirect(url_for("order_received", code=confirmation_code))
        except (ValueError, sqlite3.IntegrityError) as error:
            flash(str(error), "error")
    return render_template(
        "order.html", business_name=BUSINESS_NAME, currency_symbol=CURRENCY_SYMBOL,
        flavor_prices=prices,
        pint_reward_ready=pint_reward_ready, pint_price_cents=STANDARD_PINT_PRICE_CENTS,
        recipe_options=recipe_options_map(),
        pickup_areas=pickup_areas(), event_menu=event_menu(),
        shop=shop, min_pickup_date=min_pickup_date, flavour_descriptions=descriptions,
        form_data=request.form if request.method == "POST" else {}, available_points=None, eligible_rewards=[], loyalty_active=False,
    )


@app.get("/order/received/<code>")
def order_received(code):
    """Show a customer their private confirmation and live pickup status."""
    order = get_db().execute(
        "SELECT id, confirmation_code, flavour, recipe_variant, scoops, unit_type, total_cents, status, receipt_filename, subtotal_cents, discount_cents, reward_points, free_pint_flavour, points_earned, fulfilment_method, pickup_location, pickup_date, pickup_time FROM order_requests WHERE confirmation_code = ?",
        (code,),
    ).fetchone()
    if not order:
        return "Order confirmation not found.", 404
    return render_template(
        "order_received.html", business_name=BUSINESS_NAME, currency_symbol=CURRENCY_SYMBOL,
        payment_instructions=PAYMENT_INSTRUCTIONS, order=order, shop=shop_settings(),
        order_lines=get_db().execute("SELECT * FROM order_items WHERE order_id = ? ORDER BY id", (order["id"],)).fetchall(),
    )


@app.post("/orders/<int:order_id>/pickup-status")
@login_required
def set_pickup_status(order_id):
    """Let a signed-in partner toggle a paid order between preparing and ready."""
    next_status = request.form.get("next_status", "")
    if next_status not in {"ready", "not_ready"}:
        abort(400)
    db = get_db()
    order = db.execute("SELECT status FROM order_requests WHERE id = ?", (order_id,)).fetchone()
    if not order or order["status"] not in {"Payment confirmed", "Ready for pickup"}:
        flash("Only paid orders can be marked ready for pickup.", "error")
    else:
        updated_status = "Ready for pickup" if next_status == "ready" else "Payment confirmed"
        db.execute("UPDATE order_requests SET status = ? WHERE id = ?", (updated_status, order_id))
        db.commit()
        flash(f"Order #{order_id} is now {updated_status.lower()}.", "success")
    return redirect(url_for("index", tab="orders"))


@app.post("/order/received/<code>/receipt")
def upload_order_receipt(code):
    """Save a customer's payment screenshot as a private, cleaned image."""
    db = get_db()
    order = db.execute(
        "SELECT id, status, receipt_filename FROM order_requests WHERE confirmation_code = ?", (code,)
    ).fetchone()
    if not order or order["status"] in {"Payment confirmed", "Cancelled"}:
        abort(404)
    upload = request.files.get("receipt")
    if not upload or not upload.filename:
        flash("Choose a screenshot image first.", "error")
        return redirect(url_for("order_received", code=code))
    if Path(upload.filename).suffix.lower() not in {".jpg", ".jpeg", ".png"}:
        flash("Upload a JPG or PNG screenshot.", "error")
        return redirect(url_for("order_received", code=code))

    try:
        raw_image = upload.read()
        with Image.open(BytesIO(raw_image)) as picture:
            if picture.format not in {"JPEG", "PNG"} or picture.width * picture.height > 20_000_000:
                raise ValueError("Use a JPG or PNG image smaller than 20 megapixels.")
            picture.verify()
        with Image.open(BytesIO(raw_image)) as picture:
            picture = ImageOps.exif_transpose(picture)
            picture.thumbnail((2200, 2200))
            cleaned = BytesIO()
            picture.convert("RGB").save(cleaned, format="JPEG", quality=86, optimize=True)
        PAYMENT_RECEIPTS_FOLDER.mkdir(parents=True, exist_ok=True)
        receipt_name = f"{uuid4().hex}.jpg"
        (PAYMENT_RECEIPTS_FOLDER / receipt_name).write_bytes(cleaned.getvalue())
        old_receipt = order["receipt_filename"]
        db.execute(
            "UPDATE order_requests SET receipt_filename = ?, status = 'Payment review' WHERE id = ?",
            (receipt_name, order["id"]),
        )
        db.commit()
        if old_receipt:
            old_path = PAYMENT_RECEIPTS_FOLDER / Path(old_receipt).name
            if old_path.is_file():
                old_path.unlink()
        flash("Screenshot uploaded. Your payment is waiting for partner confirmation.", "success")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, ValueError):
        flash("We couldn't read that image. Please choose a clear JPG or PNG screenshot.", "error")
    return redirect(url_for("order_received", code=code))


@app.get("/orders/<int:order_id>/receipt")
@login_required
def view_order_receipt(order_id):
    """Let signed-in partners download the screenshot for one order."""
    order = get_db().execute(
        "SELECT receipt_filename FROM order_requests WHERE id = ?", (order_id,)
    ).fetchone()
    if not order or not order["receipt_filename"]:
        abort(404)
    receipt_name = Path(order["receipt_filename"]).name
    receipt_path = PAYMENT_RECEIPTS_FOLDER / receipt_name
    if not receipt_path.is_file():
        abort(404)
    return send_file(receipt_path, mimetype="image/jpeg", as_attachment=True,
                     download_name=f"order-{order_id}-payment.jpg", max_age=0)


@app.get("/")
@login_required
def index():
    """Show the requested page and gather the numbers it needs from SQLite."""
    db = get_db()
    # The tab is part of the address, e.g. /?tab=costs. Accept only known pages.
    tab = request.args.get("tab", "overview")
    if tab not in {"overview", "batches", "costs", "sales", "orders", "loyalty", "shop"}:
        tab = "overview"

    # SUM adds every sale/cost total. COALESCE changes an empty result to zero.
    # COUNT tells the page how many records to describe in small helper text.
    totals = db.execute(
        "SELECT COALESCE(SUM(total_cents), 0) AS revenue, COUNT(*) AS sale_count FROM sales"
    ).fetchone()
    costs_total = db.execute(
        "SELECT COALESCE(SUM(total_cents), 0) AS costs, COUNT(*) AS cost_count FROM costs"
    ).fetchone()
    revenue_cents = totals["revenue"]
    cost_cents = costs_total["costs"]

    # This query makes one summary row per batch. Each small SELECT inside it
    # adds one category of cost for that batch, plus all sales for that batch.
    batches = db.execute(
        """
        SELECT b.id, b.code, b.product, b.recipe_variant, b.made_on, b.prepared_by, b.pints_made, b.scoops_per_pint,
               COALESCE((SELECT SUM(c.total_cents) FROM costs c WHERE c.batch_id = b.id), 0) AS cost_cents,
               (SELECT COUNT(*) FROM costs c WHERE c.batch_id = b.id) AS linked_cost_count,
               (SELECT COUNT(*) FROM sales s WHERE s.batch_id = b.id) AS linked_sale_count,
               (SELECT COUNT(DISTINCT o.id) FROM sales s JOIN order_requests o
                    ON o.sale_id = s.id OR o.pint_sale_id = s.id OR EXISTS
                       (SELECT 1 FROM order_items oi WHERE oi.order_id = o.id AND oi.sale_id = s.id)
                WHERE s.batch_id = b.id AND o.status IN ('Payment confirmed', 'Ready for pickup')) AS linked_order_count,
               COALESCE((SELECT SUM(c.total_cents) FROM costs c WHERE c.batch_id = b.id AND c.category = 'Ingredients'), 0) AS ingredients_cents,
               COALESCE((SELECT SUM(c.total_cents) FROM costs c WHERE c.batch_id = b.id AND c.category = 'Packaging'), 0) AS packaging_cents,
               COALESCE((SELECT SUM(c.total_cents) FROM costs c WHERE c.batch_id = b.id AND c.category = 'Labor'), 0) AS labor_cents,
               COALESCE((SELECT SUM(c.total_cents) FROM costs c WHERE c.batch_id = b.id AND c.category = 'Delivery'), 0) AS delivery_cents,
               COALESCE((SELECT SUM(c.total_cents) FROM costs c WHERE c.batch_id = b.id AND c.category = 'Equipment'), 0) AS equipment_cents,
               COALESCE((SELECT SUM(c.total_cents) FROM costs c WHERE c.batch_id = b.id AND c.category = 'Other'), 0) AS other_cents,
               COALESCE((SELECT SUM(s.total_cents) FROM sales s WHERE s.batch_id = b.id), 0) AS revenue_cents
               , MAX(0, b.pints_made * b.scoops_per_pint - COALESCE((SELECT SUM(s.quantity * CASE WHEN s.unit_type = 'pint' THEN b.scoops_per_pint WHEN s.unit_type = 'personal' THEN 2 ELSE 1 END)
                                     FROM sales s WHERE s.batch_id = b.id), 0)) AS remaining_scoops
        FROM batches b ORDER BY b.id DESC
        """
    ).fetchall()
    # LEFT JOIN lets shared costs appear even though they have no batch.
    # LIMIT keeps the screen focused on recent entries rather than every row.
    recent_costs = db.execute(
        """SELECT c.*, b.code AS batch_code, b.product AS batch_product
           FROM costs c LEFT JOIN batches b ON b.id = c.batch_id
           ORDER BY c.purchased_on DESC, c.id DESC LIMIT 100"""
    ).fetchall()
    # JOIN adds the batch ID/product to each sale row for display.
    recent_sales = db.execute(
        """SELECT s.*, b.code AS batch_code, b.product AS batch_product
           FROM sales s JOIN batches b ON b.id = s.batch_id
           ORDER BY s.sold_on DESC, s.id DESC LIMIT 100"""
    ).fetchall()
    recent_orders = []
    for order_row in db.execute("SELECT * FROM order_requests ORDER BY id DESC LIMIT 100").fetchall():
        order_item = dict(order_row)
        order_item["lines"] = db.execute(
            "SELECT * FROM order_items WHERE order_id = ? ORDER BY id", (order_item["id"],)
        ).fetchall()
        recent_orders.append(order_item)
    # Summarise how many scoops and dollars each listed flavour has sold.
    recorded_flavors = db.execute(
        """SELECT flavour, COALESCE(SUM(quantity), 0) AS scoops,
                  COALESCE(SUM(total_cents), 0) AS revenue_cents
           FROM sales WHERE unit_type = 'scoop' GROUP BY flavour"""
    ).fetchall()
    flavor_totals = {row["flavour"]: row for row in recorded_flavors}
    loyalty_customers = db.execute(
        "SELECT * FROM loyalty_customers ORDER BY points DESC, customer_name COLLATE NOCASE"
    ).fetchall()
    # Pass named values into the HTML template. For example, index.html uses
    # {{ business_name }} and loops over batches, recent_costs, and recent_sales.
    return render_template(
        "index.html",
        business_name=BUSINESS_NAME,
        currency_code=CURRENCY_CODE,
        currency_symbol=CURRENCY_SYMBOL,
        partners=PARTNERS,
        categories=COST_CATEGORIES,
        flavor_prices=shop_flavours(),
        shop=shop_settings(),
        flavor_descriptions=shop_descriptions(),
        flavour_photos=flavour_photos(),
        pickup_areas=pickup_areas(), event_menu=event_menu(),
        pickup_owners=PICKUP_OWNERS,
        flavor_totals=flavor_totals,
        batches=batches,
        recent_costs=recent_costs,
        recent_sales=recent_sales,
        recent_orders=recent_orders,
        loyalty_customers=loyalty_customers,
        revenue_cents=revenue_cents,
        cost_cents=cost_cents,
        profit_cents=revenue_cents - cost_cents,
        sale_count=totals["sale_count"],
        cost_count=costs_total["cost_count"],
        active_tab=tab,
        today=date.today().isoformat(),
        recipe_options=recipe_options_map(),
    )


@app.post("/shop-settings")
@login_required
def update_shop_settings():
    """Save public ordering choices or add/remove an unused menu flavour."""
    db = get_db()
    back_tab = request.form.get("return_tab", "shop")
    if back_tab not in {"shop", "batches"}:
        back_tab = "shop"
    action = request.form.get("action", "save")
    if action == "add_flavour":
        name = request.form.get("flavour_name", "").strip()
        description = request.form.get("description", "").strip()[:240]
        new_photo = ""
        try:
            price = money_to_cents(request.form.get("price", ""), "scoop price")
            if not name or len(name) > 60 or price <= 0:
                raise ValueError("Enter a flavour name (up to 60 characters) and a price above $0.")
            upload = request.files.get("photo")
            if upload and upload.filename:
                new_photo = save_flavour_photo(upload)
            previous = db.execute("SELECT photo_filename FROM shop_flavours WHERE name = ?", (name,)).fetchone()
            db.execute("INSERT INTO shop_flavours (name, price_cents, description) VALUES (?, ?, ?) ON CONFLICT(name) DO UPDATE SET price_cents = excluded.price_cents, description = excluded.description, active = 1",
                       (name, price, description))
            db.commit()
            if new_photo:
                db.execute("UPDATE shop_flavours SET photo_filename = ? WHERE name = ?", (new_photo, name))
                db.commit()
                if previous:
                    remove_flavour_photo_file(previous["photo_filename"])
            flash(f"{name} was added to the customer menu.", "success")
        except (ValueError, sqlite3.IntegrityError) as error:
            remove_flavour_photo_file(new_photo)
            flash(str(error) or "That flavour already exists.", "error")
    elif action == "edit_flavour":
        old_name = request.form.get("old_name", "")
        name = request.form.get("flavour_name", "").strip()
        description = request.form.get("description", "").strip()[:240]
        try:
            price = money_to_cents(request.form.get("price", ""), "scoop price")
            row = db.execute("SELECT name, recipe_key FROM shop_flavours WHERE name = ?", (old_name,)).fetchone()
            if not row:
                raise ValueError("That flavour could not be found.")
            if not name or len(name) > 60 or price <= 0:
                raise ValueError("Enter a flavour name (up to 60 characters) and a scoop price above $0.")
            if name != old_name:
                clash = db.execute("SELECT 1 FROM shop_flavours WHERE name = ? COLLATE NOCASE AND name != ?", (name, old_name)).fetchone()
                if clash:
                    raise ValueError(f"A flavour called {name} already exists (it may have been removed earlier; use Add flavour to bring it back).")
                db.execute("UPDATE shop_flavours SET name = ?, price_cents = ?, description = ?, recipe_key = ? WHERE name = ?",
                           (name, price, description, row["recipe_key"] or old_name, old_name))
                # Keep past batches, sales and orders attached to the renamed flavour.
                for table, column in (("batches", "product"), ("sales", "flavour"), ("order_requests", "flavour"),
                                      ("order_requests", "free_pint_flavour"), ("order_items", "flavour")):
                    db.execute(f"UPDATE {table} SET {column} = ? WHERE {column} = ?", (name, old_name))
                try:
                    saved_menu = json.loads(shop_settings()["event_menu_json"] or "{}")
                except json.JSONDecodeError:
                    saved_menu = {}
                if isinstance(saved_menu, dict) and old_name in saved_menu:
                    saved_menu[name] = saved_menu.pop(old_name)
                    db.execute("UPDATE shop_settings SET setting_value = ? WHERE setting_key = 'event_menu'", (json.dumps(saved_menu),))
            else:
                db.execute("UPDATE shop_flavours SET price_cents = ?, description = ? WHERE name = ?", (price, description, old_name))
            db.commit()
            flash(f"{name} was updated.", "success")
        except (ValueError, sqlite3.IntegrityError) as error:
            db.rollback()
            flash(str(error), "error")
    elif action == "save_event_menu":
        options = recipe_options_map()
        known = shop_flavours()
        chosen_menu = {}
        index_number = 0
        while f"ev_name_{index_number}" in request.form:
            flavour_name = request.form.get(f"ev_name_{index_number}", "")
            if flavour_name in known:
                variant = request.form.get(f"ev_variant_{index_number}", "")
                choices = options.get(flavour_name, [])
                chosen_menu[flavour_name] = {
                    "on": request.form.get(f"ev_on_{index_number}") == "on",
                    "variant": variant if variant in choices else (choices[0] if choices else "Base recipe"),
                }
            index_number += 1
        db.execute("INSERT INTO shop_settings (setting_key, setting_value) VALUES ('event_menu', ?) ON CONFLICT(setting_key) DO UPDATE SET setting_value = excluded.setting_value",
                   (json.dumps(chosen_menu),))
        db.commit()
        flash("Event ice cream saved. Event customers will only see these.", "success")
    elif action == "upload_payment_qr":
        upload = request.files.get("qr_image")
        try:
            if not upload or not upload.filename:
                raise ValueError("Choose your QR code image first.")
            new_qr = save_payment_qr(upload)
            old_qr = shop_settings()["payment_qr"]
            db.execute("INSERT INTO shop_settings (setting_key, setting_value) VALUES ('payment_qr', ?) ON CONFLICT(setting_key) DO UPDATE SET setting_value = excluded.setting_value", (new_qr,))
            db.commit()
            remove_flavour_photo_file(old_qr)
            flash("Payment QR code saved. Customers will see it on their payment page.", "success")
        except ValueError as error:
            flash(str(error), "error")
    elif action == "delete_payment_qr":
        old_qr = shop_settings()["payment_qr"]
        db.execute("UPDATE shop_settings SET setting_value = '' WHERE setting_key = 'payment_qr'")
        db.commit()
        remove_flavour_photo_file(old_qr)
        flash("Payment QR code removed.", "success")
    elif action == "save_payment_text":
        db.execute("INSERT INTO shop_settings (setting_key, setting_value) VALUES ('payment_instructions', ?) ON CONFLICT(setting_key) DO UPDATE SET setting_value = excluded.setting_value",
                   (request.form.get("payment_instructions", "").strip()[:600],))
        db.commit()
        flash("Payment instructions saved.", "success")
    elif action == "upload_flavour_photo":
        name = request.form.get("flavour_name", "")
        flavour_row = db.execute("SELECT photo_filename FROM shop_flavours WHERE name = ? AND active = 1", (name,)).fetchone()
        upload = request.files.get("photo")
        try:
            if not flavour_row:
                raise ValueError("That flavour could not be found.")
            if not upload or not upload.filename:
                raise ValueError("Choose a picture to upload first.")
            new_photo = save_flavour_photo(upload)
            db.execute("UPDATE shop_flavours SET photo_filename = ? WHERE name = ?", (new_photo, name))
            db.commit()
            remove_flavour_photo_file(flavour_row["photo_filename"])
            flash(f"Picture saved for {name}. It now shows on the customer menu.", "success")
        except ValueError as error:
            flash(str(error), "error")
    elif action == "delete_flavour_photo":
        name = request.form.get("flavour_name", "")
        flavour_row = db.execute("SELECT photo_filename FROM shop_flavours WHERE name = ?", (name,)).fetchone()
        if flavour_row and flavour_row["photo_filename"]:
            db.execute("UPDATE shop_flavours SET photo_filename = '' WHERE name = ?", (name,))
            db.commit()
            remove_flavour_photo_file(flavour_row["photo_filename"])
            flash(f"Picture removed from {name}.", "success")
        else:
            flash("That flavour has no picture to remove.", "error")
    elif action in {"add_pickup_area", "edit_pickup_area", "delete_pickup_area"}:
        area_name = request.form.get("area_name", "").strip()[:80]
        area_owner = request.form.get("area_owner", "")
        try:
            if action == "delete_pickup_area":
                db.execute("DELETE FROM pickup_areas WHERE id = ?", (int(request.form.get("area_id", "0") or 0),))
                db.commit()
                flash("Pickup area deleted. Earlier orders keep the area they chose.", "success")
            else:
                if not area_name or area_owner not in PICKUP_OWNERS:
                    raise ValueError("Choose Cass or Tyler and enter a pickup area name.")
                area_id = int(request.form.get("area_id", "0") or 0) if action == "edit_pickup_area" else None
                duplicate = db.execute(
                    "SELECT 1 FROM pickup_areas WHERE owner = ? AND name = ? COLLATE NOCASE AND id IS NOT ?",
                    (area_owner, area_name, area_id)).fetchone()
                if duplicate:
                    raise ValueError(f"{area_owner} already has a pickup area called {area_name}.")
                if action == "add_pickup_area":
                    db.execute("INSERT INTO pickup_areas (owner, name) VALUES (?, ?)", (area_owner, area_name))
                    flash(f"Pickup area added for {area_owner}.", "success")
                else:
                    db.execute("UPDATE pickup_areas SET owner = ?, name = ? WHERE id = ?", (area_owner, area_name, area_id))
                    flash("Pickup area updated.", "success")
                db.commit()
        except ValueError as error:
            flash(str(error), "error")
    elif action == "delete_flavour":
        name = request.form.get("flavour_name", "")
        linked = db.execute("SELECT 1 FROM batches WHERE product = ? UNION SELECT 1 FROM order_requests WHERE flavour = ? UNION SELECT 1 FROM order_items WHERE flavour = ? LIMIT 1", (name, name, name)).fetchone()
        old_photo = db.execute("SELECT photo_filename FROM shop_flavours WHERE name = ?", (name,)).fetchone()
        if old_photo:
            remove_flavour_photo_file(old_photo["photo_filename"])
        if linked:
            db.execute("UPDATE shop_flavours SET active = 0, photo_filename = '' WHERE name = ?", (name,))
        else:
            db.execute("DELETE FROM shop_flavours WHERE name = ?", (name,))
        db.commit()
        flash(f"{name} was removed from the customer menu. Existing business records are kept.", "success")
    else:
        for key in ("allow_event", "allow_pickup", "allow_scoop", "allow_personal", "allow_pint"):
            db.execute("INSERT INTO shop_settings (setting_key, setting_value) VALUES (?, ?) ON CONFLICT(setting_key) DO UPDATE SET setting_value = excluded.setting_value",
                       (key, "1" if request.form.get(key) == "on" else "0"))
        telegram_url = request.form.get("telegram_url", "").strip()[:300]
        if telegram_url and not telegram_url.startswith(("https://t.me/", "https://telegram.me/")):
            flash("Telegram link should start with https://t.me/ or https://telegram.me/.", "error")
        else:
            db.execute("INSERT INTO shop_settings (setting_key, setting_value) VALUES ('telegram_url', ?) ON CONFLICT(setting_key) DO UPDATE SET setting_value = excluded.setting_value", (telegram_url,))
            db.commit()
            flash("Customer ordering settings were saved.", "success")
    return redirect(url_for("index", tab=back_tab))


@app.post("/batches")
@login_required
def add_batch():
    """Handle the Add Batch form from the Batches page."""
    # request.form contains values submitted by named HTML fields.
    code = request.form.get("code", "").strip()
    product = request.form.get("product", "").strip()
    made_on = request.form.get("made_on", "").strip()
    prepared_by = request.form.get("prepared_by", "")
    recipe_variant = request.form.get("recipe_variant", "Base recipe")
    try:
        pints_made = int(request.form.get("pints_made", "1"))
        scoops_per_pint = int(request.form.get("scoops_per_pint", "8"))
    except (TypeError, ValueError):
        pints_made, scoops_per_pint = 0, 0
    if not code or not product or not prepared_by:
        flash("Add a batch ID, product, and who prepared it.", "error")
    elif not 1 <= pints_made <= 500:
        flash("Enter how many pints this batch made (from 1 to 500).", "error")
    elif not 1 <= scoops_per_pint <= 100:
        flash("Enter the number of scoops in one pint (from 1 to 100).", "error")
    elif product not in shop_flavours():
        flash("Choose one of the listed flavours.", "error")
    elif recipe_variant not in recipe_options_map().get(product, ["Base recipe"]):
        flash("Choose a valid recipe or mix-in version for this flavour.", "error")
    elif prepared_by not in PARTNERS and prepared_by != "Both":
        flash("Choose Cass, Ty, or Both as the batch preparer.", "error")
    else:
        try:
            made_on = valid_date(made_on, "batch date") if made_on else None
            db = get_db()
            # The ? placeholders keep user text separate from the SQL command.
            db.execute("INSERT INTO batches (code, product, made_on, prepared_by, pints_made, scoops_per_pint, recipe_variant) VALUES (?, ?, ?, ?, ?, ?, ?)",
                       (code, product, made_on, prepared_by, pints_made, scoops_per_pint, recipe_variant))
            db.commit()
            flash(f"Batch {code} was added.", "success")
        except ValueError as error:
            flash(str(error), "error")
        except sqlite3.IntegrityError:
            flash("That batch ID already exists. Use a unique ID.", "error")
    return redirect(url_for("index", tab="batches"))


@app.post("/batches/<int:batch_id>/capacity")
@login_required
def update_batch_capacity(batch_id):
    """Change the pint count or scoop estimate for an existing batch."""
    try:
        pints_made = int(request.form.get("pints_made", ""))
        scoops_per_pint = int(request.form.get("scoops_per_pint", ""))
        if not 1 <= pints_made <= 500 or not 1 <= scoops_per_pint <= 100:
            raise ValueError("Use 1–500 pints and 1–100 scoops per pint.")
        db = get_db()
        batch = db.execute("SELECT pints_made, scoops_per_pint FROM batches WHERE id = ?", (batch_id,)).fetchone()
        if not batch:
            raise ValueError("That batch could not be found.")
        pint_sales = db.execute(
            "SELECT COALESCE(SUM(quantity), 0) FROM sales WHERE batch_id = ? AND unit_type = 'pint'",
            (batch_id,),
        ).fetchone()[0]
        if pint_sales and scoops_per_pint != batch["scoops_per_pint"]:
            raise ValueError("Scoops per pint cannot be changed after a pint sale is recorded. This keeps past stock counts accurate.")
        sold_scoops = db.execute(
            """SELECT COALESCE(SUM(quantity * CASE WHEN unit_type = 'pint' THEN ? WHEN unit_type = 'personal' THEN 2 ELSE 1 END), 0)
               FROM sales WHERE batch_id = ?""",
            (batch["scoops_per_pint"], batch_id),
        ).fetchone()[0]
        if pints_made * scoops_per_pint < sold_scoops:
            raise ValueError("That would make the batch smaller than the quantity already sold.")
        db.execute("UPDATE batches SET pints_made = ?, scoops_per_pint = ? WHERE id = ?",
                   (pints_made, scoops_per_pint, batch_id))
        db.commit()
        flash("Batch quantity updated.", "success")
    except (ValueError, sqlite3.IntegrityError) as error:
        flash(str(error), "error")
    return redirect(url_for("index", tab="batches"))


@app.post("/batches/<int:batch_id>/delete")
@login_required
def delete_batch(batch_id):
    """Delete a batch and its costs/sales after linked customer orders are cancelled."""
    db = get_db()
    try:
        # Lock writes while checking and deleting, so a sale cannot be linked
        # between the safety check and the delete.
        db.execute("BEGIN IMMEDIATE")
        batch = db.execute("SELECT code FROM batches WHERE id = ?", (batch_id,)).fetchone()
        if not batch:
            db.rollback()
            flash("That batch could not be found.", "error")
            return redirect(url_for("index", tab="batches"))
        linked_orders = db.execute(
            """SELECT COUNT(DISTINCT o.id) FROM order_requests o JOIN sales s
                 ON o.sale_id = s.id OR o.pint_sale_id = s.id OR EXISTS
                    (SELECT 1 FROM order_items oi WHERE oi.order_id = o.id AND oi.sale_id = s.id)
               WHERE s.batch_id = ? AND o.status IN ('Payment confirmed', 'Ready for pickup')""",
            (batch_id,),
        ).fetchone()[0]
        if linked_orders:
            db.rollback()
            flash("Cancel the paid customer order linked to this batch before deleting the batch.", "error")
        else:
            # Removing these rows updates the batch's costs, revenue, and stock
            # totals together with the batch itself.
            db.execute("DELETE FROM costs WHERE batch_id = ?", (batch_id,))
            db.execute("DELETE FROM sales WHERE batch_id = ?", (batch_id,))
            db.execute("DELETE FROM batches WHERE id = ?", (batch_id,))
            db.commit()
            flash(f"Batch {batch['code']} and its linked costs and sales were deleted.", "success")
    except sqlite3.IntegrityError:
        db.rollback()
        flash("This batch has linked records and can’t be deleted.", "error")
    return redirect(url_for("index", tab="batches"))


@app.post("/costs")
@login_required
def add_cost():
    """Validate and save one purchase/cost form."""
    try:
        purchased_on = valid_date(request.form.get("purchased_on"), "purchase date")
        category = request.form.get("category", "")
        description = request.form.get("description", "").strip()
        paid_by = request.form.get("paid_by", "")
        if category not in COST_CATEGORIES:
            raise ValueError("Choose a cost category from the list.")
        if paid_by not in PARTNERS:
            raise ValueError("Choose who paid from the partner list.")
        if not description:
            raise ValueError("Add a short description for this cost.")
        quantity = positive_quantity(request.form.get("quantity", ""))
        unit_cost_cents = money_to_cents(request.form.get("unit_cost", ""), "unit cost")
        # Total purchase cost = quantity × unit price. Store rounded cents.
        total_cents = int((Decimal(str(quantity)) * unit_cost_cents).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        batch_value = request.form.get("batch_id", "").strip()
        batch_id = int(batch_value) if batch_value else None
        db = get_db()
        if batch_id is not None and not db.execute("SELECT 1 FROM batches WHERE id = ?", (batch_id,)).fetchone():
            raise ValueError("Choose a batch from the list or leave the batch blank for a shared cost.")
        # If no batch was chosen, batch_id is None and SQLite saves a shared cost.
        db.execute(
            """INSERT INTO costs (purchased_on, batch_id, category, description, quantity,
                                 unit_cost_cents, total_cents, paid_by)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (purchased_on, batch_id, category, description, quantity, unit_cost_cents, total_cents, paid_by),
        )
        db.commit()
        flash("Cost recorded.", "success")
    except (ValueError, sqlite3.IntegrityError) as error:
        flash(str(error), "error")
    return redirect(url_for("index", tab="costs"))


@app.post("/sales")
@login_required
def add_sale():
    """Validate and save one sales form."""
    try:
        sold_on = valid_date(request.form.get("sold_on"), "sale date")
        buyer = request.form.get("buyer", "").strip()
        sold_by = request.form.get("sold_by", "")
        if not buyer:
            raise ValueError("Add the buyer's name or a short label such as Walk-in.")
        if sold_by not in PARTNERS:
            raise ValueError("Choose who recorded the sale from the partner list.")
        quantity = positive_quantity(request.form.get("quantity", ""))
        unit_type = request.form.get("unit_type", "scoop")
        if unit_type not in {"scoop", "personal", "pint"} or not quantity.is_integer():
            raise ValueError("Choose a valid size and enter a whole-number quantity.")
        batch_id = int(request.form.get("batch_id", ""))
        db = get_db()
        batch = db.execute("SELECT product, pints_made, scoops_per_pint FROM batches WHERE id = ?", (batch_id,)).fetchone()
        if not batch:
            raise ValueError("Choose a batch from the list. Add it on the Batches page first.")
        flavour = batch["product"]
        if flavour not in shop_flavours():
            raise ValueError("This batch does not have a listed flavour and price. Check its batch record.")
        unit_price_cents = STANDARD_PINT_PRICE_CENTS if unit_type == "pint" else shop_flavours()[flavour] * (2 if unit_type == "personal" else 1)
        if unit_price_cents <= 0:
            raise ValueError("Pint price has not been set yet.")
        # A pint sale uses the scoop count configured for the selected production batch.
        used_scoops = quantity * (batch["scoops_per_pint"] if unit_type == "pint" else 2 if unit_type == "personal" else 1)
        if used_scoops > remaining_batch_scoops(db, batch_id):
            raise ValueError("That batch does not have enough unsold ice cream for this quantity.")
        # Sale revenue = quantity × the selected unit price.
        total_cents = int((Decimal(str(quantity)) * unit_price_cents).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        db.execute(
            """INSERT INTO sales (sold_on, batch_id, flavour, quantity, unit_price_cents, total_cents, buyer, sold_by, notes, unit_type)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (sold_on, batch_id, flavour, quantity, unit_price_cents, total_cents, buyer, sold_by,
             request.form.get("notes", "").strip(), unit_type),
        )
        db.commit()
        flash("Sale recorded.", "success")
    except (ValueError, sqlite3.IntegrityError) as error:
        flash(str(error), "error")
    return redirect(url_for("index", tab="sales"))


@app.post("/orders/<int:order_id>/confirm-payment")
@login_required
def confirm_order_payment(order_id):
    """After checking the bank transfer, add the order to sales and revenue."""
    db = get_db()
    order = db.execute("SELECT * FROM order_requests WHERE id = ?", (order_id,)).fetchone()
    try:
        if not order or order["status"] not in {"Awaiting payment screenshot", "Payment review"}:
            raise ValueError("This order is missing or has already been handled.")
        sold_by = request.form.get("sold_by", "")
        if sold_by not in PARTNERS:
            raise ValueError("Choose which partner confirmed the payment.")
        lines = db.execute("SELECT * FROM order_items WHERE order_id = ? ORDER BY id", (order_id,)).fetchall()
        if not lines:
            raise ValueError("This order has no flavour selections. Please review it before confirming.")
        total_line_subtotal = sum(int(Decimal(str(line["quantity"])) * line["unit_price_cents"]) for line in lines)
        allocated_discount = 0
        first_sale_id = None
        for index, line in enumerate(lines):
            batch_id = int(request.form.get(f"batch_id_{line['id']}", ""))
            batch = db.execute("SELECT product, recipe_variant, scoops_per_pint FROM batches WHERE id = ?", (batch_id,)).fetchone()
            if not batch or batch["product"] != line["flavour"]:
                raise ValueError(f"Choose a batch of {line['flavour']} for each order line.")
            if (batch["recipe_variant"] or "Base recipe") != (line["recipe_variant"] or "Base recipe"):
                raise ValueError(f"Choose a batch with the right mix-in recipe for {line['flavour']}.")
            scoops_per_unit = batch["scoops_per_pint"] if line["unit_type"] == "pint" else 2 if line["unit_type"] == "personal" else 1
            if line["quantity"] * scoops_per_unit > remaining_batch_scoops(db, batch_id):
                raise ValueError(f"The selected {line['flavour']} batch does not have enough stock.")
            line_subtotal = int(Decimal(str(line["quantity"])) * line["unit_price_cents"])
            if index == len(lines) - 1:
                line_discount = order["discount_cents"] - allocated_discount
            else:
                line_discount = (order["discount_cents"] * line_subtotal // max(1, total_line_subtotal))
                allocated_discount += line_discount
            line_total = max(0, line_subtotal - line_discount)
            sale = db.execute(
                """INSERT INTO sales (sold_on, batch_id, flavour, quantity, unit_price_cents,
                                     total_cents, buyer, sold_by, notes, unit_type)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (date.today().isoformat(), batch_id, line["flavour"], line["quantity"],
                 line["unit_price_cents"], line_total, order["customer_name"], sold_by,
                 f"Customer order #{order_id} · {line['recipe_variant']}" + (f" · {order['notes']}" if order["notes"] else ""),
                 line["unit_type"]),
            )
            db.execute("UPDATE order_items SET sale_id = ? WHERE id = ?", (sale.lastrowid, line["id"]))
            first_sale_id = first_sale_id or sale.lastrowid
        pint_batch = None
        if order["reward_points"] == 100:
            pint_batch_id = request.form.get("pint_batch_id", "")
            pint_batch = db.execute("SELECT id, product, scoops_per_pint FROM batches WHERE id = ?", (pint_batch_id,)).fetchone()
            if not shop_settings()["allow_pint"] or not pint_batch or pint_batch["product"] != order["free_pint_flavour"]:
                raise ValueError("Choose a matching batch for the free pint reward.")
            if remaining_batch_scoops(db, pint_batch["id"]) < pint_batch["scoops_per_pint"]:
                raise ValueError("The selected batch does not have a full pint available for the reward.")
        pint_sale_id = None
        if pint_batch:
            pint_sale = db.execute(
                """INSERT INTO sales (sold_on, batch_id, flavour, quantity, unit_price_cents,
                                     total_cents, buyer, sold_by, notes, unit_type)
                   VALUES (?, ?, ?, 1, ?, 0, ?, ?, ?, 'pint')""",
                (date.today().isoformat(), pint_batch["id"], order["free_pint_flavour"],
                 STANDARD_PINT_PRICE_CENTS, order["customer_name"], sold_by,
                 f"100-point loyalty free pint · customer order #{order_id}"),
            )
            pint_sale_id = pint_sale.lastrowid
        # Points change only after a partner verifies that the money arrived.
        # A reward was reserved when ordered; now actually deduct it, then give
        # one point for each full SGD dollar paid after the reward discount.
        contact_id = order["contact_key"] or (f"telegram:{loyalty_key(order['contact'])}" if order["contact_method"] == "Telegram" else loyalty_key(order["contact"]))
        customer = db.execute("SELECT * FROM loyalty_customers WHERE contact_key = ?", (contact_id,)).fetchone()
        if customer:
            redeemed = order["reward_points"]
            new_balance = customer["points"] - redeemed
            if new_balance < 0 or customer["reserved_points"] < redeemed:
                raise ValueError("The reserved loyalty balance no longer matches. Review the customer balance before confirming.")
            db.execute(
                "UPDATE loyalty_customers SET points = ?, reserved_points = reserved_points - ?, lifetime_redeemed = lifetime_redeemed + ? WHERE contact_key = ?",
                (new_balance, redeemed, redeemed, contact_id),
            )
            if redeemed:
                db.execute(
                    "INSERT INTO loyalty_ledger (contact_key, changed_at, order_id, points_change, reason, balance_after) VALUES (?, ?, ?, ?, ?, ?)",
                    (contact_id, date.today().isoformat(), order_id, -redeemed, "Reward redeemed", new_balance),
                )
            earned = order["total_cents"] // 100
            if earned:
                new_balance += earned
                db.execute(
                    "UPDATE loyalty_customers SET points = points + ?, lifetime_earned = lifetime_earned + ? WHERE contact_key = ?",
                    (earned, earned, contact_id),
                )
                db.execute(
                    "INSERT INTO loyalty_ledger (contact_key, changed_at, order_id, points_change, reason, balance_after) VALUES (?, ?, ?, ?, ?, ?)",
                    (contact_id, date.today().isoformat(), order_id, earned, "Points for confirmed purchase", new_balance),
                )
            db.execute("UPDATE order_requests SET points_earned = ? WHERE id = ?", (earned, order_id))
        db.execute(
            "UPDATE order_requests SET status = 'Payment confirmed', sale_id = ?, pint_sale_id = ? WHERE id = ?",
            (first_sale_id, pint_sale_id, order_id),
        )
        db.commit()
        flash(f"Payment confirmed; order #{order_id} was added to sales.", "success")
    except (ValueError, sqlite3.IntegrityError) as error:
        db.rollback()
        flash(str(error), "error")
    return redirect(url_for("index", tab="orders"))


@app.post("/orders/<int:order_id>/cancel")
@login_required
def cancel_order(order_id):
    """Cancel an order and, if paid, reverse its sales, stock, and loyalty changes."""
    db = get_db()
    receipt_name = ""
    try:
        db.execute("BEGIN IMMEDIATE")
        order = db.execute("SELECT * FROM order_requests WHERE id = ?", (order_id,)).fetchone()
        if not order or order["status"] == "Cancelled":
            raise ValueError("That order is missing or already cancelled.")

        receipt_name = order["receipt_filename"] or ""
        if order["status"] in {"Payment confirmed", "Ready for pickup"}:
            # A cancelled confirmed order no longer counts as revenue or sold stock.
            # Clear the foreign-key links before deleting their two sale rows.
            db.execute(
                "UPDATE order_requests SET sale_id = NULL, pint_sale_id = NULL WHERE id = ?",
                (order_id,),
            )
            linked_sale_ids = {row["sale_id"] for row in db.execute("SELECT sale_id FROM order_items WHERE order_id = ?", (order_id,)).fetchall() if row["sale_id"]}
            linked_sale_ids.update(sale_id for sale_id in (order["sale_id"], order["pint_sale_id"]) if sale_id)
            db.execute("UPDATE order_items SET sale_id = NULL WHERE order_id = ?", (order_id,))
            if linked_sale_ids:
                db.executemany("DELETE FROM sales WHERE id = ?", [(sale_id,) for sale_id in linked_sale_ids])

            contact_id = order["contact_key"] or (
                f"telegram:{loyalty_key(order['contact'])}"
                if order["contact_method"] == "Telegram" else loyalty_key(order["contact"])
            )
            customer = db.execute(
                "SELECT * FROM loyalty_customers WHERE contact_key = ?", (contact_id,)
            ).fetchone()
            earned = int(order["points_earned"] or 0)
            redeemed = int(order["reward_points"] or 0)
            if customer and (earned or redeemed):
                starting_balance = customer["points"]
                new_balance = starting_balance - earned + redeemed
                if new_balance < max(0, customer["reserved_points"]):
                    raise ValueError(
                        "This customer has already used some points from this order. "
                        "Resolve later orders that used those points before cancelling this order."
                    )
                db.execute(
                    """UPDATE loyalty_customers
                       SET points = ?, lifetime_earned = MAX(0, lifetime_earned - ?),
                           lifetime_redeemed = MAX(0, lifetime_redeemed - ?)
                       WHERE contact_key = ?""",
                    (new_balance, earned, redeemed, contact_id),
                )
                if earned:
                    ledger_balance = starting_balance - earned
                    db.execute(
                        """INSERT INTO loyalty_ledger
                           (contact_key, changed_at, order_id, points_change, reason, balance_after)
                           VALUES (?, ?, ?, ?, ?, ?)""",
                        (contact_id, date.today().isoformat(), order_id, -earned,
                         "Confirmed order cancelled; points reversed", ledger_balance),
                    )
                if redeemed:
                    ledger_balance = starting_balance - earned + redeemed
                    db.execute(
                        """INSERT INTO loyalty_ledger
                           (contact_key, changed_at, order_id, points_change, reason, balance_after)
                           VALUES (?, ?, ?, ?, ?, ?)""",
                        (contact_id, date.today().isoformat(), order_id, redeemed,
                         "Reward points restored after order cancellation", ledger_balance),
                    )
        elif order["reward_points"]:
            # Unpaid orders only reserve points; no sale or points have been recorded yet.
            contact_id = order["contact_key"] or (
                f"telegram:{loyalty_key(order['contact'])}"
                if order["contact_method"] == "Telegram" else loyalty_key(order["contact"])
            )
            db.execute(
                "UPDATE loyalty_customers SET reserved_points = MAX(0, reserved_points - ?) WHERE contact_key = ?",
                (order["reward_points"], contact_id),
            )

        db.execute(
            """UPDATE order_requests SET status = 'Cancelled', sale_id = NULL,
               pint_sale_id = NULL, receipt_filename = '' WHERE id = ?""",
            (order_id,),
        )
        db.commit()
        if receipt_name:
            receipt_path = PAYMENT_RECEIPTS_FOLDER / Path(receipt_name).name
            if receipt_path.is_file():
                receipt_path.unlink()
        flash("Order cancelled. Any paid sale was removed and its stock restored. Handle any customer refund separately.", "success")
    except (ValueError, sqlite3.IntegrityError) as error:
        db.rollback()
        flash(str(error), "error")
    return redirect(url_for("index", tab="orders"))


@app.post("/orders/<int:order_id>/delete")
@login_required
def delete_order_request(order_id):
    """Permanently remove an unpaid or already-cancelled customer request."""
    db = get_db()
    receipt_name = ""
    try:
        db.execute("BEGIN IMMEDIATE")
        order = db.execute("SELECT * FROM order_requests WHERE id = ?", (order_id,)).fetchone()
        if not order:
            raise ValueError("That customer order could not be found.")
        if order["status"] in {"Payment confirmed", "Ready for pickup"}:
            raise ValueError("Cancel this paid order first. That restores stock and adjusts loyalty points safely.")
        if order["status"] in {"Awaiting payment screenshot", "Payment review"} and order["reward_points"]:
            contact_id = order["contact_key"] or (
                f"telegram:{loyalty_key(order['contact'])}"
                if order["contact_method"] == "Telegram" else loyalty_key(order["contact"])
            )
            db.execute(
                "UPDATE loyalty_customers SET reserved_points = MAX(0, reserved_points - ?) WHERE contact_key = ?",
                (order["reward_points"], contact_id),
            )
        receipt_name = order["receipt_filename"] or ""
        # Ledger rows refer to the order, so remove them before deleting it.
        db.execute("DELETE FROM loyalty_ledger WHERE order_id = ?", (order_id,))
        db.execute("DELETE FROM order_requests WHERE id = ?", (order_id,))
        db.commit()
        if receipt_name:
            receipt_path = PAYMENT_RECEIPTS_FOLDER / Path(receipt_name).name
            if receipt_path.is_file():
                receipt_path.unlink()
        flash("Customer order request deleted.", "success")
    except (ValueError, sqlite3.IntegrityError) as error:
        db.rollback()
        flash(str(error), "error")
    return redirect(url_for("index", tab="orders"))


@app.post("/loyalty/delete")
@login_required
def delete_loyalty_customer():
    """Remove a loyalty profile and its point history without deleting orders."""
    contact_id = request.form.get("contact_key", "").strip()
    if not contact_id:
        flash("Choose a customer loyalty account to delete.", "error")
        return redirect(url_for("index", tab="loyalty"))

    db = get_db()
    try:
        db.execute("BEGIN IMMEDIATE")
        customer = db.execute(
            "SELECT contact_key, reserved_points FROM loyalty_customers WHERE contact_key = ?",
            (contact_id,),
        ).fetchone()
        if not customer:
            raise ValueError("That loyalty account could not be found.")
        if customer["reserved_points"] > 0:
            raise ValueError("Cancel or delete the customer’s unpaid reward order before deleting this loyalty account.")

        # Keep customer orders and sales intact; only erase this rewards account
        # and its point-by-point history.
        db.execute("DELETE FROM loyalty_ledger WHERE contact_key = ?", (contact_id,))
        db.execute("DELETE FROM loyalty_customers WHERE contact_key = ?", (contact_id,))
        db.commit()
        flash("Loyalty account and its points history deleted. Customer orders and sales were kept.", "success")
    except (ValueError, sqlite3.IntegrityError) as error:
        db.rollback()
        flash(str(error), "error")
    return redirect(url_for("index", tab="loyalty"))


@app.post("/delete/<kind>/<int:entry_id>")
@login_required
def delete_entry(kind, entry_id):
    """Remove a cost or sale after the user confirms in the browser."""
    # Only these two table names are allowed. This prevents a URL from asking
    # SQLite to delete from some other table.
    if kind not in {"costs", "sales"}:
        flash("That entry type cannot be deleted here.", "error")
        return redirect(url_for("index"))
    db = get_db()
    if kind == "sales" and db.execute(
        "SELECT 1 FROM order_requests WHERE sale_id = ? OR pint_sale_id = ? UNION SELECT 1 FROM order_items WHERE sale_id = ? LIMIT 1",
        (entry_id, entry_id, entry_id)
    ).fetchone():
        flash("This sale is linked to a confirmed customer order and cannot be deleted here.", "error")
        return redirect(url_for("index", tab="orders"))
    cursor = db.execute(f"DELETE FROM {kind} WHERE id = ?", (entry_id,))
    db.commit()
    flash("Entry removed." if cursor.rowcount else "Entry was not found.", "success" if cursor.rowcount else "error")
    return redirect(url_for("index", tab=kind))


if __name__ == "__main__":
    # Local learning mode only. For internet hosting, Gunicorn is used instead.
    app.run(host="127.0.0.1", port=5000, debug=False)
