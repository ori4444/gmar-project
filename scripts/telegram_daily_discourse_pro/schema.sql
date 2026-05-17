CREATE TABLE IF NOT EXISTS daily_source_discourse_features (
    feature_date DATE NOT NULL,
    source TEXT NOT NULL,

    -- Part 1: overall war context
    war_total_messages INTEGER NOT NULL DEFAULT 0,
    war_russian_strike_in_ukraine_messages INTEGER NOT NULL DEFAULT 0,
    war_ukrainian_strike_in_russia_messages INTEGER NOT NULL DEFAULT 0,
    war_mixed_context_messages INTEGER NOT NULL DEFAULT 0,

    -- Part 2: strong-filtered Russian pre-signals
    pre_russia_messages INTEGER NOT NULL DEFAULT 0,
    pre_russia_drone_mentions INTEGER NOT NULL DEFAULT 0,
    pre_russia_drone_air_defense_messages INTEGER NOT NULL DEFAULT 0,
    pre_russia_airport_closure_mentions INTEGER NOT NULL DEFAULT 0,
    pre_russia_uncertainty_mentions INTEGER NOT NULL DEFAULT 0,
    pre_russia_top_regions_json JSONB,

    -- Part 3: strong-filtered Russian energy attacks
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
