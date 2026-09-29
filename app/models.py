from datetime import datetime, timezone

from sqlalchemy import (Boolean, Column, DateTime, ForeignKey, Index, Integer, String, Table, Text, UniqueConstraint,
                        func, text)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def _now():
    return datetime.now(timezone.utc)


item_vibes = Table(
    "item_vibes",
    Base.metadata,
    Column("item_id", ForeignKey("items.id", ondelete="CASCADE"), primary_key=True),
    Column("vibe_id", ForeignKey("vibes.id", ondelete="CASCADE"), primary_key=True),
)

item_genres = Table(
    "item_genres",
    Base.metadata,
    Column("item_id", ForeignKey("items.id", ondelete="CASCADE"), primary_key=True),
    Column("genre_id", ForeignKey("genres.id", ondelete="CASCADE"), primary_key=True),
)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    __table_args__ = (Index("ux_users_username_lower", func.lower(username), unique=True),)


class Item(Base):
    """A thing in someone's library. Only kind="album" exists today; movies etc. come later."""

    __tablename__ = "items"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(32), default="album", index=True)
    title: Mapped[str] = mapped_column(String(500))
    creator: Mapped[str] = mapped_column(String(500), default="")  # artist for albums
    year: Mapped[int | None] = mapped_column(Integer, index=True)
    cover_file: Mapped[str | None] = mapped_column(String(255))
    spotify_url: Mapped[str | None] = mapped_column(String(500))
    external_id: Mapped[str | None] = mapped_column(String(64))  # MusicBrainz release-group id
    notes: Mapped[str | None] = mapped_column(Text)
    # Genres were looked up on MusicBrainz or set by hand, so triage mustn't replace them with a lookup
    genres_checked: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    vibes: Mapped[list["Vibe"]] = relationship(secondary=item_vibes, order_by="Vibe.name")
    genres: Mapped[list["Genre"]] = relationship(secondary=item_genres, order_by="Genre.name")


class Vibe(Base):
    __tablename__ = "vibes"
    __table_args__ = (UniqueConstraint("user_id", "name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(64))
    color: Mapped[str] = mapped_column(String(7), default="#8a5cd6")


class Genre(Base):
    """Genres are a shared vocabulary (lowercase names); which ones show up is per library."""

    __tablename__ = "genres"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)


class InviteCode(Base):
    """Lets someone create an account while sign-up is closed. `max_uses` None means unlimited."""

    __tablename__ = "invite_codes"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    note: Mapped[str] = mapped_column(String(200), default="")
    max_uses: Mapped[int | None] = mapped_column(Integer)
    uses: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    def expired(self, now: datetime | None = None) -> bool:
        return self.expires_at is not None and _aware(self.expires_at) <= (now or _now())

    def used_up(self) -> bool:
        return self.max_uses is not None and self.uses >= self.max_uses

    def usable(self) -> bool:
        return not self.expired() and not self.used_up()


def _aware(dt: datetime) -> datetime:
    """SQLite hands datetimes back without a timezone; they're stored as UTC."""
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
