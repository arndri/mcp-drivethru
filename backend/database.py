import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "drivethru.db")


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_connection()
    cur = conn.cursor()

    # Menu & stock table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS menu (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            category TEXT NOT NULL,
            price INTEGER NOT NULL,
            stock INTEGER NOT NULL DEFAULT 0,
            description TEXT
        )
    """)

    # Orders table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_code TEXT NOT NULL,
            customer_name TEXT,
            items TEXT NOT NULL,
            total_price INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'processing',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Seed menu data if empty
    cur.execute("SELECT COUNT(*) FROM menu")
    if cur.fetchone()[0] == 0:
        menu_items = [
            ("Burger Klasik", "burger", 25000, 10, "Beef patty, selada, tomat, saus spesial"),
            ("Burger Spicy", "burger", 28000, 8, "Beef patty pedas, jalapeno, saus sriracha"),
            ("Burger Double", "burger", 35000, 5, "Double beef patty, keju, bacon"),
            ("Ayam Goreng", "ayam", 22000, 15, "Ayam goreng crispy original"),
            ("Ayam Spicy", "ayam", 24000, 12, "Ayam goreng crispy pedas level 3"),
            ("Nugget", "snack", 18000, 20, "6 pcs nugget ayam"),
            ("Kentang Goreng", "snack", 15000, 25, "Medium size, renyah"),
            ("Kentang Goreng Large", "snack", 20000, 18, "Large size, ekstra renyah"),
            ("Coca Cola", "minuman", 10000, 30, "350ml, dingin segar"),
            ("Fanta", "minuman", 10000, 28, "350ml"),
            ("Air Mineral", "minuman", 5000, 50, "600ml"),
            ("Es Teh Manis", "minuman", 8000, 35, "Teh manis dingin"),
            ("Milkshake Cokelat", "minuman", 22000, 10, "Thick milkshake cokelat"),
            ("Milkshake Vanila", "minuman", 22000, 8, "Thick milkshake vanila"),
            ("Paket Hemat 1", "paket", 38000, 10, "Burger Klasik + Kentang + Cola"),
            ("Paket Hemat 2", "paket", 45000, 8, "Ayam Goreng + Kentang Large + Es Teh"),
            ("Paket Family", "paket", 120000, 5, "2 Burger + 2 Ayam + 4 Minuman"),
        ]
        cur.executemany(
            "INSERT INTO menu (name, category, price, stock, description) VALUES (?,?,?,?,?)",
            menu_items
        )

    conn.commit()
    conn.close()
    print(f"[DB] Database initialized at {DB_PATH}")