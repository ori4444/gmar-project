from datetime import timedelta, timezone
from telethon import TelegramClient

from .config import API_ID, API_HASH, CHANNEL_USERNAME, SESSION_NAME


def build_client():
    return TelegramClient(SESSION_NAME, API_ID, API_HASH)


async def iter_messages(client, start_dt_utc, end_dt_utc_exclusive, channel=CHANNEL_USERNAME):
    entity = await client.get_entity(channel)

    # Subtract 1 s so Telethon's offset_date binary search doesn't land
    # just past the boundary and miss the first few real messages.
    safe_offset = start_dt_utc - timedelta(seconds=1)

    async for msg in client.iter_messages(entity, offset_date=safe_offset, reverse=True):
        if not msg.date:
            continue

        dt_utc = msg.date.astimezone(timezone.utc)

        if dt_utc < start_dt_utc:
            continue

        if dt_utc >= end_dt_utc_exclusive:
            break

        yield msg
