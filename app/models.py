from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Table, Text, UniqueConstraint
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
