from datetime import timedelta, timezone

API_ID = 35287345
API_HASH = "31107dcdd98ff1f5632340aba1ca19c5"
CHANNEL_USERNAME = "astrapress"
SESSION_NAME = "astra_session"

DB_DSN = "postgresql://postgres.bzrrkrqqsfiwejrjiijs:Ori1999cohen1@aws-1-ap-northeast-1.pooler.supabase.com:5432/postgres?sslmode=require"

LOCAL_TZ = timezone(timedelta(hours=3))

ONLY_RUSSIA = True
UPDATE_WINDOW_HOURS = 48

# Normalized schema table names (new architecture)
EVENTS_TABLE   = "attack_events"
TARGETS_TABLE  = "targets"
FEATURES_TABLE  = "daily_features"
ET_TABLE        = "event_targets"
EU_TABLE       = "event_updates"

ENABLE_TRANSLATION = True
TRANSLATION_TARGET = "iw"

# How many parsed message bundles to prefetch while you review
PREFETCH_QUEUE_SIZE = 20

REVIEW_MODE = "manual"   # "manual" or "blind"
ONLY_CRITICAL_UPDATES = True
