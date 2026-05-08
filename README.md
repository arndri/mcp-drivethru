# 🍔 Warung Cepat — Drive Thru Chat
<img width="1214" height="759" alt="image" src="https://github.com/user-attachments/assets/e51a22e6-d3c6-4ca4-8c60-e8ab4ef5f780" />


Simulasi drive-thru berbasis chat menggunakan **FastMCP + FastAPI +  AI**.

## Arsitektur

```
frontend/index.html   →  Browser (Chat UI)
        ↕ HTTP (REST)
backend/main.py       →  FastAPI (HTTP Bridge)
        ↕ Direct call
backend/mcp_server.py →  FastMCP (Tools: check_menu, check_stock, place_order)
        ↕ SQLite
backend/drivethru.db  →  Database (menu + orders)
```

## Struktur File

```
drivethru/
├── backend/
│   ├── database.py      # DB init & koneksi SQLite
│   ├── mcp_server.py    # FastMCP tools (standalone MCP server)
│   └── main.py          # FastAPI HTTP server (bridge ke tools)
├── frontend/
│   └── index.html       # Chat UI (buka di browser)
└── README.md
```

## Setup & Jalankan

### 1. Install dependencies

```bash
pip install fastmcp fastapi uvicorn openai
```

### 2. Set API Key

```bash
export OPENAI_API_KEY="sk-..."
```

Atau buat file `.env` di folder `backend/`:
```
OPENAI_API_KEY=sk-...
```

### 3. Jalankan Backend

```bash
cd backend
python main.py
```

Server berjalan di: `http://localhost:8000`

### 4. Buka Frontend

Buka file `frontend/index.html` di browser.

> Atau pakai live server: `npx serve frontend/`

---

## API Endpoints

| Method | Path | Deskripsi |
|--------|------|-----------|
| `POST` | `/chat` | Chat endpoint utama (agentic loop) |
| `GET`  | `/menu` | Ambil semua menu |
| `GET`  | `/orders` | Lihat semua pesanan |
| `GET`  | `/health` | Health check |

### POST /chat

```json
{
  "messages": [
    {"role": "user", "content": "Mau pesan 1 Burger Klasik dan Cola"}
  ],
  "customer_name": "Budi"
}
```

---

## MCP Tools

| Tool | Deskripsi |
|------|-----------|
| `check_menu` | Tampilkan menu tersedia (filter per kategori) |
| `check_stock` | Cek stok item sebelum pesan |
| `place_order` | Buat pesanan & kurangi stok |
| `get_order_status` | Cek status pesanan by kode |

---

## Flow Chat

```
User: "Mau pesan burger dan cola"
  → Claude: check_stock(["burger", "cola"])
  → Stok OK
  → Claude: place_order([{id:1, name:"Burger Klasik", qty:1}, ...])
  → DB: INSERT orders, UPDATE menu stock
  → Claude: "Pesanan DT-ABC123 berhasil! Total Rp. 35.000, estimasi 7 menit 🎉"
```

---

## Database Schema

**menu** — stok & harga  
**orders** — riwayat pesanan dengan kode unik DT-XXXXXX
