"""
FastAPI HTTP bridge - exposes MCP tools + OpenAI GPT chat endpoint
"""
import json
import os
import sys
from typing import Optional
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from openai import OpenAI
from dotenv import load_dotenv
load_dotenv()  # Load environment variables from .env file
# Add backend dir to path
sys.path.insert(0, os.path.dirname(__file__))
from database import init_db, get_connection

# Initialize DB
init_db()

app = FastAPI(title="DriveThru MCP API", version="1.0.0")

# CORS for frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# OpenAI client

client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))

# ─────────────────────────────────────────────
# MCP Tools definitions for Claude
# ─────────────────────────────────────────────

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "check_menu",
            "description": "Tampilkan daftar menu yang tersedia di restoran.",
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "description": "Kategori menu: 'all', 'burger', 'ayam', 'snack', 'minuman', 'paket'",
                        "default": "all"
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "check_stock",
            "description": "Cek ketersediaan stok untuk item yang ingin dipesan.",
            "parameters": {
                "type": "object",
                "properties": {
                    "item_names": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List nama item yang ingin dicek"
                    }
                },
                "required": ["item_names"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "place_order",
            "description": "Buat dan proses pesanan baru ke database.",
            "parameters": {
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "integer"},
                                "name": {"type": "string"},
                                "qty": {"type": "integer"},
                                "price": {"type": "integer"}
                            },
                            "required": ["id", "name", "qty", "price"]
                        },
                        "description": "List item yang dipesan"
                    },
                    "customer_name": {
                        "type": "string",
                        "description": "Nama pelanggan",
                        "default": "Pelanggan"
                    }
                },
                "required": ["items"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_order_status",
            "description": "Cek status pesanan berdasarkan kode pesanan.",
            "parameters": {
                "type": "object",
                "properties": {
                    "order_code": {
                        "type": "string",
                        "description": "Kode pesanan format DT-XXXXXX"
                    }
                },
                "required": ["order_code"]
            }
        }
    }
]

SYSTEM_PROMPT = """Kamu adalah Miko, asisten drive-thru ramah dari "Warung Cepat" - restoran cepat saji modern.

Tugasmu:
1. Sambut pelanggan dengan hangat
2. Bantu mereka memesan makanan/minuman
3. Selalu cek stok sebelum konfirmasi pesanan
4. Proses pesanan ke database jika stok tersedia
5. Berikan konfirmasi pesanan dengan kode dan estimasi waktu

Panduan:
- Gunakan Bahasa Indonesia yang ramah, casual, dan friendly (boleh pakai "kak", "nih", "ya")
- Jika item habis, langsung tawarkan alternatif yang mirip
- Selalu tampilkan total harga dalam format Rp. XX.XXX
- Setelah pesanan berhasil, tampilkan ringkasan pesanan yang jelas
- Jika pelanggan bertanya menu, gunakan check_menu tool
- Format harga: gunakan titik sebagai pemisah ribuan (Rp. 25.000)

Karakter: Ceria, efisien, helpful. Seperti kasir drive-thru yang profesional tapi tetap nyantai."""


def execute_tool(tool_name: str, tool_input: dict) -> str:
    """Execute MCP tool locally (direct DB access)"""
    import importlib.util
    import sys as _sys
    
    # Import mcp_server tools directly
    spec = importlib.util.spec_from_file_location(
        "mcp_server",
        os.path.join(os.path.dirname(__file__), "mcp_server.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    
    tool_fn = getattr(mod, tool_name, None)
    if tool_fn is None:
        return json.dumps({"error": f"Tool {tool_name} not found"})
    
    return tool_fn(**tool_input)


# ─────────────────────────────────────────────
# Models
# ─────────────────────────────────────────────

class ChatMessage(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    customer_name: Optional[str] = "Pelanggan"


# ─────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────

@app.get("/")
def root():
    return {"status": "ok", "service": "DriveThru MCP API"}


@app.get("/menu")
def get_menu(category: str = "all"):
    """Get full menu from DB"""
    conn = get_connection()
    cur = conn.cursor()
    if category == "all":
        cur.execute("SELECT * FROM menu ORDER BY category, name")
    else:
        cur.execute("SELECT * FROM menu WHERE category = ? ORDER BY name", (category,))
    rows = cur.fetchall()
    conn.close()
    return {"menu": [dict(r) for r in rows]}


@app.get("/orders")
def get_orders():
    """Get all orders"""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM orders ORDER BY created_at DESC LIMIT 50")
    rows = cur.fetchall()
    conn.close()
    orders = []
    for r in rows:
        o = dict(r)
        o["items"] = json.loads(o["items"])
        orders.append(o)
    return {"orders": orders}


@app.post("/chat")
async def chat(req: ChatRequest):
    """
    Main chat endpoint - runs agentic loop with OpenAI function calling
    """
    if not client.api_key:
        raise HTTPException(500, "OPENAI_API_KEY not set")

    system = SYSTEM_PROMPT + f"\n\nNama pelanggan saat ini: {req.customer_name}"
    messages = [{"role": "system", "content": system}]
    messages += [{"role": m.role, "content": m.content} for m in req.messages]

    max_iterations = 10
    iteration = 0

    while iteration < max_iterations:
        iteration += 1

        response = client.chat.completions.create(
            model="gpt-4o",
            max_tokens=2048,
            tools=TOOLS,
            messages=messages
        )

        msg = response.choices[0].message

        # Append assistant message (may include tool_calls)
        messages.append(msg)

        if response.choices[0].finish_reason == "tool_calls":
            for tc in msg.tool_calls:
                tool_input = json.loads(tc.function.arguments)
                result = execute_tool(tc.function.name, tool_input)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result
                })
        else:
            final_text = msg.content or ""

            # Scan for placed orders in tool results
            order_info = None
            for m in messages:
                if isinstance(m, dict) and m.get("role") == "tool":
                    try:
                        data = json.loads(m["content"])
                        if data.get("success") and data.get("order_code"):
                            order_info = {
                                "order_code": data["order_code"],
                                "total_price": data["total_price"],
                                "estimated_time": data.get("estimated_time")
                            }
                    except:
                        pass

            return {"reply": final_text, "order_info": order_info}

    return {"reply": "Maaf, ada gangguan teknis. Silakan coba lagi ya kak! 🙏", "order_info": None}


@app.get("/health")
def health():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) as c FROM menu")
    menu_count = cur.fetchone()["c"]
    cur.execute("SELECT COUNT(*) as c FROM orders")
    order_count = cur.fetchone()["c"]
    conn.close()
    return {
        "status": "healthy",
        "menu_items": menu_count,
        "total_orders": order_count
    }


if __name__ == "__main__":
    import uvicorn
    print("🚀 Starting DriveThru MCP API on http://localhost:8000")
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)