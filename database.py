
from uuid import uuid4

from sqlalchemy import (
    create_engine,
    Column,
    Integer,
    String,
    Text,
    ForeignKey,
    DateTime,
)
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.sql import func


# --------------------------------------------------
# DATABASE CONFIGURATION
# --------------------------------------------------

DATABASE_URL = "sqlite:///./novara.db"

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)

Base = declarative_base()


# --------------------------------------------------
# USERS
# --------------------------------------------------

class User(Base):
    __tablename__ = "users"

    id = Column(String, primary_key=True)
    email = Column(String(320), unique=True, nullable=False)
    name = Column(String(200), nullable=False)
    picture = Column(Text, nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


# --------------------------------------------------
# CHATS
# --------------------------------------------------

class Chat(Base):
    __tablename__ = "chats"

    id = Column(
        String,
        primary_key=True,
        default=lambda: str(uuid4()),
    )

    title = Column(
        String(200),
        nullable=False,
        default="New conversation",
    )

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    user_id = Column(
        String,
        ForeignKey("users.id"),
        nullable=True,
    )


# --------------------------------------------------
# CHAT MESSAGES
# --------------------------------------------------

class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id = Column(
        String,
        primary_key=True,
        default=lambda: str(uuid4()),
    )

    chat_id = Column(
        String,
        ForeignKey("chats.id"),
        nullable=False,
    )

    role = Column(String(20), nullable=False)
    content = Column(Text, nullable=False)

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


# --------------------------------------------------
# SUBSCRIPTIONS
# --------------------------------------------------

class Subscription(Base):
    __tablename__ = "subscriptions"

    id = Column(Integer, primary_key=True, autoincrement=True)

    user_id = Column(
        String,
        nullable=False,
        unique=True,
    )

    paypal_subscription_id = Column(
        String,
        unique=True,
        nullable=True,
    )

    plan_id = Column(String, nullable=False)
    status = Column(
        String,
        nullable=False,
        default="PENDING",
    )

    updated_at = Column(
        DateTime(timezone=True),
        nullable=True,
        onupdate=func.now(),
    )


# IMPORTANT:
# Do not call Base.metadata.create_all() here.
# This prevents this file from attempting to create
# or alter tables in your existing database.
