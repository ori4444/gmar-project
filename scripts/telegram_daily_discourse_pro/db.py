import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2
from psycopg2.extras import RealDictCursor

from shared.config import DB_DSN

TABLE_NAME = "daily_source_discourse_features"

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS daily_source_discourse_features (
    feature_date DATE NOT NULL,
    source TEXT NOT NULL,

    war_total_messages INTEGER NOT NULL DEFAULT 0,
    war_russian_strike_in_ukraine_messages INTEGER NOT NULL DEFAULT 0,
    war_ukrainian_strike_in_russia_messages INTEGER NOT NULL DEFAULT 0,
    war_mixed_context_messages INTEGER NOT NULL DEFAULT 0,

    pre_russia_messages INTEGER NOT NULL DEFAULT 0,
    pre_russia_drone_mentions INTEGER NOT NULL DEFAULT 0,
    pre_russia_drone_air_defense_messages INTEGER NOT NULL DEFAULT 0,
    pre_russia_airport_closure_mentions INTEGER NOT NULL DEFAULT 0,
    pre_russia_uncertainty_mentions INTEGER NOT NULL DEFAULT 0,
    pre_russia_top_regions_json JSONB,

    energy_attack_messages INTEGER NOT NULL DEFAULT 0,
    energy_confirmation_messages INTEGER NOT NULL DEFAULT 0,
    energy_explosion_or_fire_mentions INTEGER NOT NULL DEFAULT 0,
    energy_target_messages INTEGER NOT NULL DEFAULT 0,
    energy_refinery_or_oil_depot_messages INTEGER NOT NULL DEFAULT 0,
    energy_other_infra_messages INTEGER NOT NULL DEFAULT 0,
    energy_top_regions_json JSONB,

    feature_explanation_json JSONB,

    inserted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    PRIMARY KEY (feature_date, source)
);
"""


def get_conn():
    return psycopg2.connect(DB_DSN, cursor_factory=RealDictCursor)


def ensure_schema(conn):
    with conn.cursor() as cur:
        cur.execute(CREATE_SQL)
    conn.commit()


def upsert_features(conn, features):
    """Insert or update a DailyFeatures row (upsert on primary key)."""
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO daily_source_discourse_features (
                    feature_date, source,
                    war_total_messages, war_russian_strike_in_ukraine_messages,
                    war_ukrainian_strike_in_russia_messages, war_mixed_context_messages,
                    pre_russia_messages, pre_russia_drone_mentions,
                    pre_russia_drone_air_defense_messages, pre_russia_airport_closure_mentions,
                    pre_russia_uncertainty_mentions, pre_russia_top_regions_json,
                    energy_attack_messages, energy_confirmation_messages,
                    energy_explosion_or_fire_mentions, energy_target_messages,
                    energy_refinery_or_oil_depot_messages, energy_other_infra_messages,
                    energy_top_regions_json, feature_explanation_json
                ) VALUES (
                    %(feature_date)s, %(source)s,
                    %(war_total_messages)s, %(war_russian_strike_in_ukraine_messages)s,
                    %(war_ukrainian_strike_in_russia_messages)s, %(war_mixed_context_messages)s,
                    %(pre_russia_messages)s, %(pre_russia_drone_mentions)s,
                    %(pre_russia_drone_air_defense_messages)s, %(pre_russia_airport_closure_mentions)s,
                    %(pre_russia_uncertainty_mentions)s, %(pre_russia_top_regions_json)s,
                    %(energy_attack_messages)s, %(energy_confirmation_messages)s,
                    %(energy_explosion_or_fire_mentions)s, %(energy_target_messages)s,
                    %(energy_refinery_or_oil_depot_messages)s, %(energy_other_infra_messages)s,
                    %(energy_top_regions_json)s, %(feature_explanation_json)s
                )
                ON CONFLICT (feature_date, source) DO UPDATE SET
                    war_total_messages = EXCLUDED.war_total_messages,
                    war_russian_strike_in_ukraine_messages = EXCLUDED.war_russian_strike_in_ukraine_messages,
                    war_ukrainian_strike_in_russia_messages = EXCLUDED.war_ukrainian_strike_in_russia_messages,
                    war_mixed_context_messages = EXCLUDED.war_mixed_context_messages,
                    pre_russia_messages = EXCLUDED.pre_russia_messages,
                    pre_russia_drone_mentions = EXCLUDED.pre_russia_drone_mentions,
                    pre_russia_drone_air_defense_messages = EXCLUDED.pre_russia_drone_air_defense_messages,
                    pre_russia_airport_closure_mentions = EXCLUDED.pre_russia_airport_closure_mentions,
                    pre_russia_uncertainty_mentions = EXCLUDED.pre_russia_uncertainty_mentions,
                    pre_russia_top_regions_json = EXCLUDED.pre_russia_top_regions_json,
                    energy_attack_messages = EXCLUDED.energy_attack_messages,
                    energy_confirmation_messages = EXCLUDED.energy_confirmation_messages,
                    energy_explosion_or_fire_mentions = EXCLUDED.energy_explosion_or_fire_mentions,
                    energy_target_messages = EXCLUDED.energy_target_messages,
                    energy_refinery_or_oil_depot_messages = EXCLUDED.energy_refinery_or_oil_depot_messages,
                    energy_other_infra_messages = EXCLUDED.energy_other_infra_messages,
                    energy_top_regions_json = EXCLUDED.energy_top_regions_json,
                    feature_explanation_json = EXCLUDED.feature_explanation_json,
                    updated_at = NOW()
                """,
                {
                    "feature_date": features.feature_date,
                    "source": features.source,
                    "war_total_messages": features.war_total_messages,
                    "war_russian_strike_in_ukraine_messages": features.war_russian_strike_in_ukraine_messages,
                    "war_ukrainian_strike_in_russia_messages": features.war_ukrainian_strike_in_russia_messages,
                    "war_mixed_context_messages": features.war_mixed_context_messages,
                    "pre_russia_messages": features.pre_russia_messages,
                    "pre_russia_drone_mentions": features.pre_russia_drone_mentions,
                    "pre_russia_drone_air_defense_messages": features.pre_russia_drone_air_defense_messages,
                    "pre_russia_airport_closure_mentions": features.pre_russia_airport_closure_mentions,
                    "pre_russia_uncertainty_mentions": features.pre_russia_uncertainty_mentions,
                    "pre_russia_top_regions_json": features.pre_russia_top_regions_json,
                    "energy_attack_messages": features.energy_attack_messages,
                    "energy_confirmation_messages": features.energy_confirmation_messages,
                    "energy_explosion_or_fire_mentions": features.energy_explosion_or_fire_mentions,
                    "energy_target_messages": features.energy_target_messages,
                    "energy_refinery_or_oil_depot_messages": features.energy_refinery_or_oil_depot_messages,
                    "energy_other_infra_messages": features.energy_other_infra_messages,
                    "energy_top_regions_json": features.energy_top_regions_json,
                    "feature_explanation_json": features.feature_explanation_json,
                },
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
