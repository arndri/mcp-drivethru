"""
FastAPI HTTP bridge - exposes MCP tools + OpenAI GPT chat endpoint
"""
import json
import os
import sys
from typing import Optional
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator
from openai import OpenAI
from dotenv import load_dotenv
load_dotenv()  # Load environment variables from .env file
# Add backend dir to path
sys.path.insert(0, os.path.dirname(__file__))
from database import init_db, get_connection
from agent.config import AgentSettings
from agent.components import OpenAIChatModel
from agent.harness import AgentHarness, AgentTask

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

# OpenAI client. Keep app startup working even when only /health or /menu is needed.
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

# Models
# ─────────────────────────────────────────────

class ChatMessage(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    customer_name: str
    strategy: Optional[str] = None
    approvals: list[str] = Field(default_factory=list)

    @field_validator("customer_name")
    @classmethod
    def validate_customer_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("customer_name is required")
        return cleaned


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
    Main chat endpoint - delegates runtime control to the Agent Harness.
    """
    if client is None:
        raise HTTPException(500, "OPENAI_API_KEY not set")

    settings = AgentSettings()
    harness = AgentHarness(OpenAIChatModel(client, settings.model), settings=settings)
    task = AgentTask(
        messages=[{"role": m.role, "content": m.content} for m in req.messages],
        customer_name=req.customer_name,
        strategy=req.strategy,
        approvals=set(req.approvals),
    )
    return await harness.run(task)

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
