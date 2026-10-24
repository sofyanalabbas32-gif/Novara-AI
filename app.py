
import os
import base64
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy import select
from authlib.integrations.starlette_client import OAuth

from database import (
    Base,
    engine,
    SessionLocal,
    Chat,
    ChatMessage,
    Subscription,
)

load_dotenv()

app = FastAPI(title="Novara AI")

APP_URL = os.getenv(
    "APP_URL",
    "https://novara-ai-1.onrender.com"
).rstrip("/")

GOOGLE_REDIRECT_URI = (
    f"{APP_URL}/auth/google/callback"
)

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GROQ_MODEL = os.getenv(
    "GROQ_MODEL",
    "qwen/qwen3.8-27b"
).strip()

SESSION_SECRET = os.getenv("SESSION_SECRET", "").strip()

PAYPAL_MODE = os.getenv("PAYPAL_MODE", "live").lower().strip()
PAYPAL_CLIENT_ID = os.getenv("PAYPAL_CLIENT_ID", "").strip()
PAYPAL_CLIENT_SECRET = os.getenv("PAYPAL_CLIENT_SECRET", "").strip()
PAYPAL_PLAN_ID = os.getenv("PAYPAL_PLAN_ID", "").strip()

PAYPAL_API = (
    "https://api-m.sandbox.paypal.com"
    if PAYPAL_MODE == "sandbox"
    else "https://api-m.paypal.com"
)

if not SESSION_SECRET:
    raise RuntimeError("SESSION_SECRET is missing.")

app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    same_site="lax",
    https_only=True,
)

app.mount(
    "/static",
    StaticFiles(directory="static"),
    name="static",
)

templates = Jinja2Templates(directory="templates")

oauth = OAuth()

oauth.register(
    name="google",
    client_id=os.getenv("GOOGLE_CLIENT_ID", "").strip(),
    client_secret=os.getenv("GOOGLE_CLIENT_SECRET", "").strip(),
    server_metadata_url=(
        "https://accounts.google.com/"
        ".well-known/openid-configuration"
    ),
    client_kwargs={"scope": "openid email profile"},
)


@app.on_event("startup")
def startup():
    print("DATABASE CHECK: starting table creation")

    print("DATABASE URL TYPE:", engine.url.get_backend_name())
    print("TABLES BEFORE:", list(Base.metadata.tables.keys()))

    Base.metadata.create_all(bind=engine)

    from sqlalchemy import inspect
    inspector = inspect(engine)
    print("TABLES AFTER:", inspector.get_table_names())

    if "chats" not in inspector.get_table_names():
        raise RuntimeError(
            "DATABASE ERROR: chats table was not created"
        )

    print("DATABASE CHECK: chats table exists")


def now():
    return datetime.now(ZoneInfo("Europe/London"))


def current_user(request: Request):
    user = request.session.get("user")

    if not user or not user.get("id"):
        raise HTTPException(
            status_code=401,
            detail="Please sign in."
        )

    return user


def owned_chat(db, chat_id, user_id):
    chat = db.scalar(
        select(Chat).where(
            Chat.id == chat_id,
            Chat.user_id == user_id
        )
    )

    if not chat:
        raise HTTPException(
            status_code=404,
            detail="Chat not found."
        )

    return chat


class ChatRequest(BaseModel):
    message: str = Field(default="", max_length=12000)
    chat_id: str | None = None
    image_data: str | None = None


class VerifyRequest(BaseModel):
    subscription_id: str = Field(min_length=5, max_length=200)


def needs_search(query):
    q = query.lower()

    keywords = (
        "latest", "today", "right now", "currently",
        "current", "weather", "news", "2026",
        "president of", "prime minister of",
        "leader of", "ceo of", "search the web",
        "search online", "look it up", "stock price",
        "exchange rate", "who won", "live score",
    )

    return any(word in q for word in keywords)


def search_web(query):
    key = os.getenv("TAVILY_API_KEY", "").strip()

    if not key:
        return "Web search is not configured."

    try:
        response = httpx.post(
            "https://api.tavily.com/search",
            json={
                "api_key": key,
                "query": query,
                "search_depth": "basic",
                "max_results": 5,
            },
            timeout=20,
        )

        response.raise_for_status()

        results = response.json().get("results", [])

        return "\n\n".join(
            f"Title: {item.get('title', '')}\n"
            f"URL: {item.get('url', '')}\n"
            f"Summary: {item.get('content', '')}"
            for item in results
        ) or "No relevant web results found."

    except (httpx.HTTPError, ValueError) as exc:
        print("Web search error:", type(exc).__name__)
        return "Web search is temporarily unavailable."


async def paypal_token(client):
    if not all((
        PAYPAL_CLIENT_ID,
        PAYPAL_CLIENT_SECRET,
        PAYPAL_PLAN_ID,
    )):
        raise HTTPException(
            status_code=503,
            detail="PayPal configuration is incomplete."
        )

    response = await client.post(
        f"{PAYPAL_API}/v1/oauth2/token",
        auth=(PAYPAL_CLIENT_ID, PAYPAL_CLIENT_SECRET),
        data={"grant_type": "client_credentials"},
        timeout=25,
    )

    if response.is_error:
        print("PayPal authentication failed:", response.status_code)
        raise HTTPException(
            status_code=502,
            detail="PayPal authentication failed."
        )

    token = response.json().get("access_token")

    if not token:
        raise HTTPException(
            status_code=502,
            detail="PayPal returned no access token."
        )

    return token


async def paypal_subscription(subscription_id):
    async with httpx.AsyncClient(timeout=25) as client:
        token = await paypal_token(client)

        response = await client.get(
            f"{PAYPAL_API}/v1/billing/subscriptions/"
            f"{subscription_id}",
            headers={"Authorization": f"Bearer {token}"},
        )

    if response.is_error:
        raise HTTPException(
            status_code=502,
            detail="Could not verify subscription with PayPal."
        )

    return response.json()


def save_subscription(user_id, data):
    sub_id = data.get("id")
    plan_id = data.get("plan_id")
    status = (data.get("status") or "UNKNOWN").upper()

    if not sub_id or not plan_id:
        raise HTTPException(
            status_code=502,
            detail="PayPal returned incomplete subscription details."
        )

    with SessionLocal() as db:
        existing = db.scalar(
            select(Subscription).where(
                Subscription.user_id == user_id
            )
        )

        other = db.scalar(
            select(Subscription).where(
                Subscription.paypal_subscription_id == sub_id
            )
        )

        if other and str(other.user_id) != str(user_id):
            raise HTTPException(
                status_code=409,
                detail="Subscription is linked to another account."
            )

        if existing:
            existing.paypal_subscription_id = sub_id
            existing.plan_id = plan_id
            existing.status = status
            existing.updated_at = now()
        else:
            db.add(Subscription(
                user_id=user_id,
                paypal_subscription_id=sub_id,
                plan_id=plan_id,
                status=status,
                updated_at=now(),
            ))

        db.commit()


@app.get("/")
async def home(request: Request):
    if not request.session.get("user"):
        return RedirectResponse("/login", status_code=303)

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"user": request.session["user"]},
    )


@app.get("/login")
async def login_page(request: Request):
    if request.session.get("user"):
        return RedirectResponse("/", status_code=303)

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"user": None},
    )


@app.get("/login/google")
async def login_google(request: Request):
    if not os.getenv("GOOGLE_CLIENT_ID") or not os.getenv("GOOGLE_CLIENT_SECRET"):
        raise HTTPException(
            status_code=503,
            detail="Google login is not configured."
        )

    return await oauth.google.authorize_redirect(
        request,
        GOOGLE_REDIRECT_URI,
    )


@app.get("/auth/google/callback")
async def google_callback(request: Request):
    try:
        token = await oauth.google.authorize_access_token(request)

        info = token.get("userinfo")

        if not info:
            info = await oauth.google.userinfo(token=token)

        if not info or not info.get("sub") or not info.get("email_verified"):
            raise HTTPException(
                status_code=403,
                detail="A verified Google account is required."
            )

        request.session.clear()
        request.session["user"] = {
            "id": str(info["sub"]),
            "email": info.get("email", ""),
            "name": info.get("name", "User"),
            "picture": info.get("picture", ""),
        }

        return RedirectResponse("/", status_code=303)

    except HTTPException:
        raise
    except Exception as exc:
        print("Google login error:", type(exc).__name__)
        return HTMLResponse(
            "<h2>Google login failed</h2>"
            "<a href='/login'>Try again</a>",
            status_code=400,
        )


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login")


@app.get("/api/auth/me")
async def auth_me(request: Request):
    return {"user": current_user(request)}


@app.get("/api/chats")
async def list_chats(request: Request):
    user = current_user(request)

    with SessionLocal() as db:
        chats = db.scalars(
            select(Chat)
            .where(Chat.user_id == user["id"])
            .order_by(Chat.updated_at.desc())
        ).all()

        return [
            {"id": c.id, "title": c.title}
            for c in chats
        ]


@app.post("/api/chats")
async def create_chat(request: Request):
    user = current_user(request)

    with SessionLocal() as db:
        chat = Chat(
            user_id=user["id"],
            title="New conversation",
            created_at=now(),
            updated_at=now(),
        )

        db.add(chat)
        db.commit()
        db.refresh(chat)

        return {
            "id": chat.id,
            "title": chat.title,
            "messages": [],
        }


@app.get("/api/chats/{chat_id}")
async def read_chat(chat_id: str, request: Request):
    user = current_user(request)

    with SessionLocal() as db:
        chat = owned_chat(db, chat_id, user["id"])

        messages = db.scalars(
            select(ChatMessage)
            .where(ChatMessage.chat_id == chat.id)
            .order_by(ChatMessage.created_at, ChatMessage.id)
        ).all()

        return {
            "id": chat.id,
            "title": chat.title,
            "messages": [
                {"role": m.role, "content": m.content}
                for m in messages
            ],
        }


@app.delete("/api/chats/{chat_id}")
async def delete_chat(chat_id: str, request: Request):
    user = current_user(request)

    with SessionLocal() as db:
        chat = owned_chat(db, chat_id, user["id"])

        db.query(ChatMessage).filter(
            ChatMessage.chat_id == chat.id
        ).delete()

        db.delete(chat)
        db.commit()

    return {"ok": True}


@app.post("/chat")
async def chat(request: Request, data: ChatRequest):
    user = current_user(request)

    if not data.message.strip() and not data.image_data:
        raise HTTPException(
            status_code=400,
            detail="Type a message or attach an image."
        )

    if not GROQ_API_KEY:
        raise HTTPException(
            status_code=503,
            detail="GROQ_API_KEY is missing."
        )

    image = data.image_data

    if image:
        allowed = (
            "data:image/png;base64,",
            "data:image/jpeg;base64,",
            "data:image/webp;base64,",
            "data:image/gif;base64,",
        )

        if not image.startswith(allowed):
            raise HTTPException(
                status_code=400,
                detail="Use PNG, JPG, WEBP or GIF."
            )

        if len(image) > 8_000_000:
            raise HTTPException(
                status_code=413,
                detail="Image is too large."
            )

        try:
            base64.b64decode(image.split(",", 1)[1], validate=True)
        except Exception:
            raise HTTPException(
                status_code=400,
                detail="Invalid image data."
            )

    message = data.message.strip() or "What is in this image?"
    stored = message + (" [Image attached]" if image else "")
    web_results = search_web(message) if needs_search(message) else "No web search needed."

    with SessionLocal() as db:
        if data.chat_id:
            saved_chat = owned_chat(db, data.chat_id, user["id"])
            if saved_chat.title == "New conversation":
                saved_chat.title = message[:197]
            saved_chat.updated_at = now()
        else:
            saved_chat = Chat(
                user_id=user["id"],
                title=message[:197],
                created_at=now(),
                updated_at=now(),
            )
            db.add(saved_chat)
            db.commit()
            db.refresh(saved_chat)

        db.add(ChatMessage(
            chat_id=saved_chat.id,
            role="user",
            content=stored,
            created_at=now(),
        ))
        db.commit()

        recent = db.scalars(
            select(ChatMessage)
            .where(ChatMessage.chat_id == saved_chat.id)
            .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
            .limit(40)
        ).all()

        messages = [{
            "role": "system",
            "content": (
                "You are Novara AI, a helpful and honest assistant. "
                f"Today's UK date is {now().strftime('%d %B %Y')}. "
                "Use supplied web results for current facts. Include relevant URLs. "
                "Never invent sources. If search failed, say so.\n\n"
                f"WEB RESULTS:\n{web_results}"
            ),
        }]

        for item in reversed(recent):
            if item.role not in ("user", "assistant"):
                continue

            if image and item.role == "user" and item.content == stored:
                content = [
                    {"type": "text", "text": message},
                    {"type": "image_url", "image_url": {"url": image}},
                ]
            else:
                content = item.content.replace(
                    " [Image attached]",
                    " (image attached earlier)"
                )

            messages.append({
                "role": item.role,
                "content": content,
            })

        try:
            async with httpx.AsyncClient(timeout=90) as client:
                response = await client.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
                    json={
                        "model": GROQ_MODEL,
                        "messages": messages,
                        "temperature": 0.7,
                        "max_completion_tokens": 4096,
                    },
                )

            if response.is_error:
                print("Groq error:", response.status_code, response.text[:500])
                raise HTTPException(
                    status_code=502,
                    detail="Groq failed. Check the API key, model and usage limits."
                )

            choices = response.json().get("choices", [])
            reply = choices[0]["message"]["content"].strip() if choices else ""

            if not reply:
                raise HTTPException(
                    status_code=502,
                    detail="Groq returned an empty answer."
                )

            db.add(ChatMessage(
                chat_id=saved_chat.id,
                role="assistant",
                content=reply,
                created_at=now(),
            ))

            saved_chat.updated_at = now()
            db.commit()

            return {
                "reply": reply,
                "chat_id": saved_chat.id,
                "title": saved_chat.title,
            }

        except HTTPException:
            raise
        except Exception as exc:
            db.rollback()
            print("Chat error:", type(exc).__name__)
            raise HTTPException(
                status_code=502,
                detail="Novara could not get an answer. Try again."
            )


@app.get("/api/paypal/config")
async def paypal_config(request: Request):
    current_user(request)

    if not PAYPAL_CLIENT_ID or not PAYPAL_PLAN_ID:
        raise HTTPException(
            status_code=503,
            detail="PayPal is not configured."
        )

    return {
        "client_id": PAYPAL_CLIENT_ID,
        "plan_id": PAYPAL_PLAN_ID,
        "mode": PAYPAL_MODE,
    }


@app.post("/api/paypal/create-subscription")
async def create_subscription(request: Request):
    user = current_user(request)

    async with httpx.AsyncClient(timeout=25) as client:
        token = await paypal_token(client)

        response = await client.post(
            f"{PAYPAL_API}/v1/billing/subscriptions",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Prefer": "return=representation",
            },
            json={
                "plan_id": PAYPAL_PLAN_ID,
                "custom_id": str(user["id"]),
                "application_context": {
                    "brand_name": "Novara AI",
                    "locale": "en-GB",
                    "shipping_preference": "NO_SHIPPING",
                    "user_action": "SUBSCRIBE_NOW",
                    "return_url": f"{APP_URL}/?paypal=success",
                    "cancel_url": f"{APP_URL}/?paypal=cancelled",
                },
            },
        )

    if response.is_error:
        print("PayPal create error:", response.status_code, response.text[:500])
        raise HTTPException(
            status_code=502,
            detail="PayPal could not create checkout. Check your plan ID and mode."
        )

    result = response.json()
    approval = next(
        (link["href"] for link in result.get("links", [])
         if link.get("rel") == "approve"),
        None,
    )

    if not result.get("id") or not approval:
        raise HTTPException(
            status_code=502,
            detail="PayPal returned no approval link."
        )

    save_subscription(
        user["id"],
        {
            "id": result["id"],
            "plan_id": result.get("plan_id", PAYPAL_PLAN_ID),
            "status": result.get("status", "APPROVAL_PENDING"),
        },
    )

    return {
        "subscription_id": result["id"],
        "approval_url": approval,
        "status": result.get("status", "APPROVAL_PENDING"),
    }


@app.post("/api/paypal/verify-subscription")
async def verify_subscription(data: VerifyRequest, request: Request):
    user = current_user(request)
    remote = await paypal_subscription(data.subscription_id)

    if str(remote.get("custom_id", "")) != str(user["id"]):
        raise HTTPException(
            status_code=403,
            detail="This subscription does not belong to your account."
        )

    if remote.get("plan_id") != PAYPAL_PLAN_ID:
        raise HTTPException(
            status_code=403,
            detail="This is not the Novara Plus plan."
        )

    save_subscription(user["id"], remote)

    status = (remote.get("status") or "UNKNOWN").upper()

    return {
        "subscription_id": remote.get("id"),
        "status": status,
        "plus_active": status == "ACTIVE",
    }


@app.get("/api/paypal/status")
async def paypal_status(request: Request):
    user = current_user(request)

    with SessionLocal() as db:
        saved = db.scalar(
            select(Subscription).where(
                Subscription.user_id == user["id"]
            )
        )

        if not saved or not saved.paypal_subscription_id:
            return {"status": "NONE", "plus_active": False}

        subscription_id = saved.paypal_subscription_id

    remote = await paypal_subscription(subscription_id)

    if str(remote.get("custom_id", "")) != str(user["id"]):
        raise HTTPException(
            status_code=403,
            detail="Subscription ownership verification failed."
        )

    if remote.get("plan_id") != PAYPAL_PLAN_ID:
        raise HTTPException(
            status_code=403,
            detail="Subscription plan verification failed."
        )

    save_subscription(user["id"], remote)
    status = (remote.get("status") or "UNKNOWN").upper()

    return {
        "subscription_id": remote.get("id"),
        "status": status,
        "plus_active": status == "ACTIVE",
    }
