from datetime import timezone
from telethon import TelegramClient

from .config import API_ID, API_HASH, CHANNEL_USERNAME, SESSION_NAME


def build_client():
    return TelegramClient(SESSION_NAME, API_ID, API_HASH)


async def iter_messages(client, start_dt_utc, end_dt_utc_exclusive):
    entity = await client.get_entity(CHANNEL_USERNAME)

    async for msg in client.iter_messages(entity, offset_date=start_dt_utc, reverse=True):
        if not msg.date:
            continue

        dt_utc = msg.date.astimezone(timezone.utc)

        if dt_utc < start_dt_utc:
            continue

        if dt_utc >= end_dt_utc_exclusive:
            break

        yield msg
