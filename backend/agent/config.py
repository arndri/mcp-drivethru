import os
from dataclasses import dataclass


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
                        "default": "all",
                    }
                },
            },
        },
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
                        "description": "List nama item yang ingin dicek",
                    }
                },
                "required": ["item_names"],
            },
        },
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
                                "price": {"type": "integer"},
                            },
                            "required": ["id", "name", "qty", "price"],
                        },
                        "description": "List item yang dipesan",
                    },
                    "customer_name": {
                        "type": "string",
                        "description": "Nama pelanggan",
                    },
                },
                "required": ["items"],
            },
        },
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
                        "description": "Kode pesanan format DT-XXXXXX",
                    }
                },
                "required": ["order_code"],
            },
        },
    },
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

Kontrol runtime:
- Kamu boleh meminta tools, tetapi Agent Harness yang memutuskan izin, batas eksekusi, retry, dan verifikasi.
- Jika tool gagal atau verifikasi gagal, baca observasi harness dan perbaiki rencana.

Karakter: Ceria, efisien, helpful. Seperti kasir drive-thru yang profesional tapi tetap nyantai."""


@dataclass(frozen=True)
class AgentSettings:
    model: str = os.environ.get("DRIVETHRU_MODEL", "gpt-4o")
    max_iterations: int = int(os.environ.get("AGENT_MAX_ITERATIONS", "10"))
    max_tool_calls: int = int(os.environ.get("AGENT_MAX_TOOL_CALLS", "12"))
    max_retries: int = int(os.environ.get("AGENT_MAX_RETRIES", "2"))
    tool_timeout_seconds: float = float(os.environ.get("AGENT_TOOL_TIMEOUT_SECONDS", "5"))
    require_order_approval: bool = os.environ.get("AGENT_REQUIRE_ORDER_APPROVAL", "0") == "1"
    default_strategy: str = os.environ.get("AGENT_LOOP_STRATEGY", "verify_repair")

