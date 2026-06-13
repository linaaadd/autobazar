import json
import os
from datetime import datetime, timedelta
from typing import Optional

import aiosqlite

DB_PATH = os.environ.get("DB_PATH", "/data/autobazar.db")


async def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS listings (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id             INTEGER NOT NULL,
                username            TEXT,
                phone               TEXT,
                engine              TEXT,
                turbo               TEXT,
                status              TEXT    DEFAULT 'active',
                created_at          TEXT,
                expires_at          TEXT,
                make                TEXT,
                model               TEXT,
                year                INTEGER,
                mileage             INTEGER,
                price               INTEGER,
                currency            TEXT    DEFAULT 'EUR',
                fuel                TEXT,
                transmission        TEXT,
                city                TEXT,
                description         TEXT,
                photo_ids           TEXT,
                channel_message_ids TEXT
            )
        """)
        # Migration: add phone column if table existed before
        for col in ("phone TEXT", "engine TEXT", "turbo TEXT"):
            try:
                await db.execute(f"ALTER TABLE listings ADD COLUMN {col}")
            except Exception:
                pass
        await db.commit()


async def create_listing(data: dict) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        now = datetime.now().isoformat()
        expires = (datetime.now() + timedelta(days=30)).isoformat()
        cursor = await db.execute(
            """
            INSERT INTO listings
                (user_id, username, phone, engine, turbo, status, created_at, expires_at,
                 make, model, year, mileage, price, currency,
                 fuel, transmission, city, description, photo_ids)
            VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["user_id"], data.get("username"), data.get("phone", ""),
                data.get("engine", ""), data.get("turbo", ""),
                now, expires,
                data["make"], data["model"], data["year"], data["mileage"],
                data["price"], data.get("currency", "EUR"), data["fuel"],
                data["transmission"], data["city"],
                data["description"], json.dumps(data["photo_ids"]),
            ),
        )
        await db.commit()
        return cursor.lastrowid


async def get_listing(listing_id: int) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM listings WHERE id = ?", (listing_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None


async def get_user_listings(user_id: int) -> list:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM listings WHERE user_id = ? AND status != 'deleted' ORDER BY created_at DESC",
            (user_id,),
        )
        return [dict(r) for r in await cursor.fetchall()]


async def update_listing_channel_msgs(listing_id: int, message_ids: list):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE listings SET channel_message_ids = ? WHERE id = ?",
            (json.dumps(message_ids), listing_id),
        )
        await db.commit()


async def extend_listing(listing_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        expires = (datetime.now() + timedelta(days=30)).isoformat()
        await db.execute(
            "UPDATE listings SET expires_at = ?, status = 'active' WHERE id = ?",
            (expires, listing_id),
        )
        await db.commit()


async def delete_listing(listing_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE listings SET status = 'deleted' WHERE id = ?", (listing_id,)
        )
        await db.commit()


async def get_expired_listings() -> list:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        now = datetime.now().isoformat()
        cursor = await db.execute(
            "SELECT * FROM listings WHERE status = 'active' AND expires_at < ?",
            (now,),
        )
        return [dict(r) for r in await cursor.fetchall()]


async def search_listings(make=None, model=None, price_max=None, city=None) -> list:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        query = "SELECT * FROM listings WHERE status = 'active'"
        params = []
        if make:
            query += " AND LOWER(make) LIKE ?"
            params.append(f"%{make.lower()}%")
        if model:
            query += " AND LOWER(model) LIKE ?"
            params.append(f"%{model.lower()}%")
        if price_max:
            query += " AND price <= ?"
            params.append(price_max)
        if city:
            query += " AND LOWER(city) LIKE ?"
            params.append(f"%{city.lower()}%")
        query += " ORDER BY created_at DESC LIMIT 20"
        cursor = await db.execute(query, params)
        return [dict(r) for r in await cursor.fetchall()]
