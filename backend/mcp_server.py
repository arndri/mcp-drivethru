"""
FastMCP Drive-Thru Server
Tools: check_menu, check_stock, place_order, get_order_status
"""
import json
import random
import string
from datetime import datetime
from fastmcp import FastMCP
from database import get_connection, init_db

# Initialize DB on import
init_db()

mcp = FastMCP(
    name="DriveThruMCP",
    instructions="""
Kamu adalah asisten drive-thru restoran cepat saji. 
Tugasmu membantu pelanggan memesan makanan/minuman dengan ramah dan efisien.

Selalu gunakan tools yang tersedia untuk:
1. Cek menu yang tersedia (check_menu)
2. Cek stok item (check_stock) 
3. Proses pesanan (place_order)
4. Cek status pesanan (get_order_status)

Komunikasi dalam Bahasa Indonesia yang ramah, singkat dan kasual.
Jika item tidak tersedia, tawarkan alternatif yang mirip.
"""
)


@mcp.tool()
def check_menu(category: str = "all") -> str:
    """
    Tampilkan daftar menu yang tersedia.
    
    Args:
        category: Kategori menu - 'all', 'burger', 'ayam', 'snack', 'minuman', 'paket'
    
    Returns:
        JSON list of menu items with id, name, price, stock, description
    """
    conn = get_connection()
    cur = conn.cursor()
    
    if category == "all":
        cur.execute("SELECT * FROM menu WHERE stock > 0 ORDER BY category, name")
    else:
        cur.execute(
            "SELECT * FROM menu WHERE category = ? AND stock > 0 ORDER BY name",
            (category.lower(),)
        )
    
    rows = cur.fetchall()
    conn.close()
    
    menu = [dict(row) for row in rows]
    if not menu:
        return json.dumps({"available": [], "message": f"Tidak ada menu tersedia untuk kategori '{category}'"})
    
    return json.dumps({"available": menu, "count": len(menu)})


@mcp.tool()
def check_stock(item_names: list[str]) -> str:
    """
    Cek ketersediaan stok untuk item-item yang ingin dipesan.
    
    Args:
        item_names: List nama item yang ingin dicek stoknya
    
    Returns:
        JSON status stok setiap item (tersedia/habis/tidak_ditemukan)
    """
    conn = get_connection()
    cur = conn.cursor()
    
    results = []
    for name in item_names:
        # Fuzzy search - cari nama yang mengandung kata kunci
        cur.execute(
            "SELECT * FROM menu WHERE LOWER(name) LIKE LOWER(?) ORDER BY stock DESC LIMIT 1",
            (f"%{name}%",)
        )
        row = cur.fetchone()
        
        if not row:
            results.append({
                "requested": name,
                "found": False,
                "status": "tidak_ditemukan",
                "message": f"Menu '{name}' tidak ditemukan"
            })
        elif row["stock"] == 0:
            results.append({
                "requested": name,
                "found": True,
                "id": row["id"],
                "name": row["name"],
                "price": row["price"],
                "stock": 0,
                "status": "habis",
                "message": f"Maaf, {row['name']} sedang habis"
            })
        else:
            results.append({
                "requested": name,
                "found": True,
                "id": row["id"],
                "name": row["name"],
                "price": row["price"],
                "stock": row["stock"],
                "status": "tersedia",
                "message": f"{row['name']} tersedia (stok: {row['stock']})"
            })
    
    conn.close()
    return json.dumps({"stock_check": results})


@mcp.tool()
def place_order(
    items: list[dict],
    customer_name: str = "Pelanggan"
) -> str:
    """
    Buat pesanan baru setelah stok dikonfirmasi tersedia.
    
    Args:
        items: List item pesanan, format: [{"id": int, "name": str, "qty": int, "price": int}]
        customer_name: Nama pelanggan
    
    Returns:
        JSON konfirmasi pesanan dengan order_code dan total harga
    """
    conn = get_connection()
    cur = conn.cursor()
    
    # Validate all items and check stock
    validated_items = []
    total_price = 0
    errors = []
    
    for item in items:
        cur.execute("SELECT * FROM menu WHERE id = ?", (item["id"],))
        row = cur.fetchone()
        
        if not row:
            errors.append(f"Item ID {item['id']} tidak ditemukan")
            continue
        
        qty = item.get("qty", 1)
        if row["stock"] < qty:
            errors.append(f"{row['name']} stok tidak cukup (tersisa: {row['stock']}, diminta: {qty})")
            continue
        
        validated_items.append({
            "id": row["id"],
            "name": row["name"],
            "qty": qty,
            "price": row["price"],
            "subtotal": row["price"] * qty
        })
        total_price += row["price"] * qty
    
    if errors:
        conn.close()
        return json.dumps({
            "success": False,
            "errors": errors,
            "message": "Pesanan gagal karena beberapa item bermasalah"
        })
    
    if not validated_items:
        conn.close()
        return json.dumps({"success": False, "message": "Tidak ada item valid untuk dipesan"})
    
    # Generate order code
    order_code = "DT-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
    
    # Insert order
    cur.execute(
        "INSERT INTO orders (order_code, customer_name, items, total_price, status) VALUES (?,?,?,?,?)",
        (order_code, customer_name, json.dumps(validated_items), total_price, "processing")
    )
    
    # Update stock
    for item in validated_items:
        cur.execute(
            "UPDATE menu SET stock = stock - ? WHERE id = ?",
            (item["qty"], item["id"])
        )
    
    conn.commit()
    
    # Estimate time (2-3 min per item type, max 15 min)
    estimate_minutes = min(5 + len(validated_items) * 2, 15)
    
    result = {
        "success": True,
        "order_code": order_code,
        "customer_name": customer_name,
        "items": validated_items,
        "total_price": total_price,
        "status": "processing",
        "estimated_time": f"{estimate_minutes} menit",
        "message": f"Pesanan {order_code} berhasil diproses! Estimasi siap dalam {estimate_minutes} menit."
    }
    
    conn.close()
    return json.dumps(result)


@mcp.tool()
def get_order_status(order_code: str) -> str:
    """
    Cek status pesanan berdasarkan kode pesanan.
    
    Args:
        order_code: Kode pesanan (format: DT-XXXXXX)
    
    Returns:
        JSON detail pesanan dan status terkini
    """
    conn = get_connection()
    cur = conn.cursor()
    
    cur.execute("SELECT * FROM orders WHERE order_code = ?", (order_code.upper(),))
    row = cur.fetchone()
    conn.close()
    
    if not row:
        return json.dumps({"found": False, "message": f"Pesanan {order_code} tidak ditemukan"})
    
    items = json.loads(row["items"])
    return json.dumps({
        "found": True,
        "order_code": row["order_code"],
        "customer_name": row["customer_name"],
        "items": items,
        "total_price": row["total_price"],
        "status": row["status"],
        "created_at": row["created_at"]
    })


if __name__ == "__main__":
    # Run as standalone MCP server (stdio)
    mcp.run()