
import os
import base64
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import Request
from sqlalchemy import select
from sqlalchemy.orm import Session
from authlib.integrations.starlette_client import OAuth

from database import SessionLocal, Chat, ChatMessage

load_dotenv()

app = FastAPI(title="Novara AI")

# --------------------------------------------------
# CONFIGURATION
# --------------------------------------------------

GOOGLE_REDIRECT_URI = (
    "https://novara-ai-1.onrender.com/auth/google/callback"
)

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GROQ_MODEL = os.getenv("GROQ_MODEL", "qwen/qwen3.8-27b").strip()
SESSION_SECRET = os.getenv("SESSION_SECRET", "").strip()

if not SESSION_SECRET:
    raise RuntimeError("SESSION_SECRET is missing from your environment.")

if not os.getenv("GOOGLE_CLIENT_ID", "").strip():
    print("WARNING: GOOGLE_CLIENT_ID is missing.")

if not os.getenv("GOOGLE_CLIENT_SECRET", "").strip():
    print("WARNING: GOOGLE_CLIENT_SECRET is missing.")

app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    same_site="lax",
    https_only=True,
)

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

oauth = OAuth()
oauth.register(
    name="google",
    client_id=os.getenv("GOOGLE_CLIENT_ID", "").strip(),
    client_secret=os.getenv("GOOGLE_CLIENT_SECRET", "").strip(),
    server_metadata_url=(
        "https://accounts.google.com/.well-known/openid-configuration"
    ),
    client_kwargs={"scope": "openid email profile"},
)


# --------------------------------------------------
# DATE AND WEB SEARCH
# --------------------------------------------------

def get_current_date() -> str:
    return datetime.now(
        ZoneInfo("Europe/London")
    ).strftime("%d %B %Y")


def needs_web_search(query: str) -> bool:
    query = query.lower().strip()

    explicit_search_phrases = (
        "search the web",
        "search online",
        "search the internet",
        "browse the web",
        "browse online",
        "look it up",
        "look this up",
        "look it up online",
        "find it online",
        "find online",
        "check online",
        "check the web",
        "check the internet",
        "use the internet",
        "use web search",
        "search for",
        "google this",
        "google search",
        "find sources",
        "give me sources",
        "search the latest",
        "can you search",
        "please search",
        "search for me",
    )

    if any(phrase in query for phrase in explicit_search_phrases):
        return True

    current_information_phrases = (
        "right now",
        "currently",
        "current",
        "latest",
        "today",
        "tonight",
        "this week",
        "this month",
        "this year",
        "recent news",
        "breaking news",
        "weather",
        "news about",
        "stock price",
        "share price",
        "exchange rate",
        "release date",
        "who won",
        "live score",
        "president of",
        "prime minister of",
        "leader of",
        "ceo of",
        "latest version",
        "latest update",
        "what happened",
        "recently announced",
        "2026",
    )

    return any(
        phrase in query for phrase in current_information_phrases
    )


def search_web(query: str) -> str:
    tavily_key = os.getenv("TAVILY_API_KEY", "").strip()

    if not tavily_key:
        return (
            "Web search is unavailable because TAVILY_API_KEY "
            "is not configured."
        )

    try:
        response = httpx.post(
            "https://api.tavily.com/search",
            json={
                "api_key": tavily_key,
                "query": query,
                "search_depth": "basic",
                "max_results": 5,
            },
            timeout=20.0,
        )
        response.raise_for_status()

        results = response.json().get("results", [])

        if not results:
            return "No relevant web results were found."

        return "\n\n".join(
            f"Title: {item.get('title', '')}\n"
            f"URL: {item.get('url', '')}\n"
            f"Information: {item.get('content', '')}"
            for item in results
        )

    except httpx.HTTPError as exc:
        print(
            f"Tavily search request failed: {type(exc).__name__}"
        )
        return (
            "Web search failed because the search service "
            "could not be reached."
        )

    except (ValueError, TypeError, KeyError) as exc:
        print(
            "Tavily response could not be processed: "
            f"{type(exc).__name__}"
        )
        return "Web search returned an unreadable response."


# --------------------------------------------------
# REQUEST MODELS
# --------------------------------------------------

class ChatRequest(BaseModel):
    message: str = Field(default="", max_length=12000)
    history: list[dict] = Field(default_factory=list)
    chat_id: str | None = None
    image_data: str | None = None


# --------------------------------------------------
# AUTHENTICATION AND CHAT HELPERS
# --------------------------------------------------

def current_user(request: Request):
    user = request.session.get("user")

    if not user:
        raise HTTPException(
            status_code=401,
            detail="Please sign in.",
        )

    return user


def owned_chat(db: Session, chat_id: str, user_id: str):
    chat = db.scalar(
        select(Chat).where(
            Chat.id == chat_id,
            Chat.user_id == user_id,
        )
    )

    if chat is None:
        raise HTTPException(
            status_code=404,
            detail="Chat not found.",
        )

    return chat


@app.get("/api/auth/me")
async def auth_me(request: Request):
    return {"user": current_user(request)}


# --------------------------------------------------
# WEBSITE AND GOOGLE LOGIN
# --------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    if not request.session.get("user"):
        return RedirectResponse("/login", status_code=303)

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"user": request.session["user"]},
    )


@app.get("/login", response_class=HTMLResponse)
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
    if not os.getenv("GOOGLE_CLIENT_ID", "").strip() or not os.getenv(
        "GOOGLE_CLIENT_SECRET", ""
    ).strip():
        raise HTTPException(
            status_code=503,
            detail="Google login is not configured.",
        )

    return await oauth.google.authorize_redirect(
        request,
        GOOGLE_REDIRECT_URI,
    )


@app.get("/auth/google/callback", name="auth_callback")
async def auth_callback(request: Request):
    try:
        token = await oauth.google.authorize_access_token(request)
        info = token.get("userinfo")

        if not info:
            info = await oauth.google.userinfo(token=token)

        if (
            not info
            or not info.get("sub")
            or not info.get("email_verified")
        ):
            raise HTTPException(
                status_code=403,
                detail=(
                    "Use a Google account with a verified email address."
                ),
            )

        request.session.clear()
        request.session["user"] = {
            "id": info["sub"],
            "email": info.get("email", ""),
            "name": info.get("name", "User"),
            "picture": info.get("picture", ""),
        }

        return RedirectResponse("/", status_code=303)

    except HTTPException:
        raise

    except Exception as exc:
        print(
            "Google sign-in failed: "
            f"{type(exc).__name__}: {str(exc)[:1000]}"
        )

        return HTMLResponse(
            content=(
                "<!doctype html>"
                "<html><head><title>Google sign-in failed</title></head>"
                "<body style='font-family:Arial,sans-serif;"
                "max-width:600px;margin:60px auto;padding:20px'>"
                "<h2>Google sign-in failed</h2>"
                "<p>Please try again. If the problem continues, "
                "check the server logs.</p>"
                "<a href='/login'>Back to login</a>"
                "</body></html>"
            ),
            status_code=400,
        )


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login")


# --------------------------------------------------
# CHAT HISTORY
# --------------------------------------------------

@app.get("/api/chats")
async def list_chats(request: Request):
    user = current_user(request)

    with SessionLocal() as db:
        chats = db.scalars(
            select(Chat)
            .where(Chat.user_id == user["id"])
            .order_by(Chat.created_at.desc())
        ).all()

        return [
            {"id": chat.id, "title": chat.title}
            for chat in chats
        ]


@app.post("/api/chats")
async def create_chat(request: Request):
    user = current_user(request)
    now = datetime.now(ZoneInfo("Europe/London"))

    with SessionLocal() as db:
        chat = Chat(
            user_id=user["id"],
            title="New conversation",
            created_at=now,
            updated_at=now,
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
async def get_chat(chat_id: str, request: Request):
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
                {"role": msg.role, "content": msg.content}
                for msg in messages
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


# --------------------------------------------------
# GROQ AI CHAT
# --------------------------------------------------

@app.post("/chat")
async def chat(request: Request, data: ChatRequest):
    user = current_user(request)

    if not data.message.strip() and not data.image_data:
        raise HTTPException(
            status_code=400,
            detail="Type a message or attach an image.",
        )

    if not GROQ_API_KEY:
        raise HTTPException(
            status_code=503,
            detail="Groq API key is missing. Configure GROQ_API_KEY.",
        )

    image_url = data.image_data

    if image_url:
        valid_prefixes = (
            "data:image/png;base64,",
            "data:image/jpeg;base64,",
            "data:image/webp;base64,",
            "data:image/gif;base64,",
        )

        if not image_url.startswith(valid_prefixes):
            raise HTTPException(
                status_code=400,
                detail="Please upload a PNG, JPG, WEBP, or GIF image.",
            )

        if len(image_url) > 8_000_000:
            raise HTTPException(
                status_code=413,
                detail="Image too large. Choose a smaller image.",
            )

        try:
            encoded = image_url.split(",", 1)[1]
            base64.b64decode(encoded, validate=True)
        except Exception:
            raise HTTPException(
                status_code=400,
                detail="The uploaded image data is invalid.",
            )

    display_text = data.message.strip() or "What is in this image?"
    stored_text = display_text + (
        " [Image attached]" if image_url else ""
    )

    now = datetime.now(ZoneInfo("Europe/London"))
    today = get_current_date()

    if needs_web_search(display_text):
        web_results = search_web(display_text)
    else:
        web_results = "No web search was needed for this question."

    with SessionLocal() as db:
        if data.chat_id is None:
            saved_chat = Chat(
                user_id=user["id"],
                title=display_text[:197]
                + ("…" if len(display_text) > 197 else ""),
                created_at=now,
                updated_at=now,
            )
            db.add(saved_chat)
            db.commit()
            db.refresh(saved_chat)

        else:
            saved_chat = owned_chat(
                db,
                data.chat_id,
                user["id"],
            )

            if saved_chat.title == "New conversation":
                saved_chat.title = display_text[:197]

            saved_chat.updated_at = now
            db.commit()

        db.add(
            ChatMessage(
                chat_id=saved_chat.id,
                role="user",
                content=stored_text,
                created_at=now,
            )
        )
        db.commit()

        messages = [
            {
                "role": "system",
                "content": (
                    "You are Novara AI, a helpful, friendly and honest AI assistant. "
                    f"Today's UK date is {today}. "
                    "You have access to web search results supplied by your application. "
                    "When web search results are provided, use them to answer the user's "
                    "question and include relevant source URLs. "
                    "If the user explicitly asks you to search, browse, look something "
                    "up online, or find information on the web, use the supplied search "
                    "results. Never claim that you cannot browse the internet when "
                    "search results are available. Do not invent search results, sources, "
                    "or URLs. If the search fails or returns no results, clearly explain "
                    "that the search did not succeed. For current or changing information, "
                    "prefer reliable web results over memory. Be honest about uncertainty.\n\n"
                    f"WEB SEARCH RESULTS:\n{web_results}"
                ),
            }
        ]

        previous_messages = db.scalars(
            select(ChatMessage)
            .where(ChatMessage.chat_id == saved_chat.id)
            .order_by(
                ChatMessage.created_at.desc(),
                ChatMessage.id.desc(),
            )
            .limit(40)
        ).all()

        for item in reversed(previous_messages):
            if item.role not in ("user", "assistant"):
                continue

            if (
                item.role == "user"
                and image_url
                and item.content == stored_text
            ):
                content = [
                    {"type": "text", "text": display_text},
                    {
                        "type": "image_url",
                        "image_url": {"url": image_url},
                    },
                ]
                messages.append(
                    {"role": "user", "content": content}
                )

            else:
                content = item.content.replace(
                    " [Image attached]",
                    " (an image was attached in an earlier message)",
                )
                messages.append(
                    {"role": item.role, "content": content}
                )

        try:
            async with httpx.AsyncClient(timeout=90.0) as client:
                response = await client.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {GROQ_API_KEY}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": GROQ_MODEL,
                        "messages": messages,
                        "temperature": 0.7,
                        "max_completion_tokens": 4096,
                    },
                )

            if response.is_error:
                print(
                    f"Groq API error {response.status_code}: "
                    f"{response.text[:1500]}"
                )
                raise HTTPException(
                    status_code=502,
                    detail=(
                        "Groq could not respond. Check your API key, "
                        "model name, and usage limits."
                    ),
                )

            result = response.json()
            choices = result.get("choices", [])

            if not choices:
                print("Groq returned no choices.")
                raise HTTPException(
                    status_code=502,
                    detail="Groq returned no answer. Please try again.",
                )

            reply = (
                choices[0].get("message", {}).get("content") or ""
            ).strip()

            if not reply:
                raise HTTPException(
                    status_code=502,
                    detail="The AI returned an empty response.",
                )

            db.add(
                ChatMessage(
                    chat_id=saved_chat.id,
                    role="assistant",
                    content=reply,
                    created_at=datetime.now(ZoneInfo("Europe/London")),
                )
            )

            saved_chat.updated_at = datetime.now(
                ZoneInfo("Europe/London")
            )
            db.commit()

            return {
                "reply": reply,
                "chat_id": saved_chat.id,
            }

        except HTTPException:
            raise

        except Exception as exc:
            db.rollback()
            print(
                "Groq request failed: "
                f"{type(exc).__name__}: {str(exc)[:1000]}"
            )
            raise HTTPException(
                status_code=502,
                detail=(
                    "Novara couldn't get an AI response. "
                    "Try again shortly."
                ),
            )
