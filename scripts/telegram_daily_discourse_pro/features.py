import json
from collections import Counter
from dataclasses import dataclass, field

from detectors import (
    count_matches,
    extract_regions,
    is_mixed_war_context,
    is_relevant_energy_confirmation,
    is_relevant_russia_drone_activity,
    is_relevant_russia_pre_signal,
    is_russian_drone_air_defense,
    is_russian_strike_in_ukraine,
    is_target_energy_attack,
    is_ukrainian_strike_in_russia_general,
    is_war_related,
    summarize_regions,
    DRONE_PATTERNS,
    EXPLOSION_OR_FIRE_PATTERNS,
    AIRPORT_PATTERNS,
    UNCERTAINTY_PATTERNS,
    ENERGY_PATTERNS,
    REFINERY_OR_OIL_DEPOT_PATTERNS,
    OTHER_ENERGY_INFRA_PATTERNS,
)
from shared.text_utils import clean_text


@dataclass
class DailyFeatures:
    feature_date: object
    source: str

    war_total_messages: int = 0
    war_russian_strike_in_ukraine_messages: int = 0
    war_ukrainian_strike_in_russia_messages: int = 0
    war_mixed_context_messages: int = 0

    pre_russia_messages: int = 0
    pre_russia_drone_mentions: int = 0
    pre_russia_drone_air_defense_messages: int = 0
    pre_russia_airport_closure_mentions: int = 0
    pre_russia_uncertainty_mentions: int = 0
    pre_russia_top_regions_json: str = "[]"

    energy_attack_messages: int = 0
    energy_confirmation_messages: int = 0
    energy_explosion_or_fire_mentions: int = 0
    energy_target_messages: int = 0
    energy_refinery_or_oil_depot_messages: int = 0
    energy_other_infra_messages: int = 0
    energy_top_regions_json: str = "[]"

    feature_explanation_json: str = "{}"

    _pre_region_counter: Counter = field(default_factory=Counter)
    _energy_region_counter: Counter = field(default_factory=Counter)
    _explanations: dict = field(default_factory=lambda: {
        "pre_signal_examples": [],
        "energy_examples": [],
        "air_defense_examples": [],
    })

    def consume_message(self, text: str):
        t = clean_text(text)
        if not t:
            return

        # Part 1
        if is_war_related(t):
            self.war_total_messages += 1

        if is_russian_strike_in_ukraine(t):
            self.war_russian_strike_in_ukraine_messages += 1
        elif is_ukrainian_strike_in_russia_general(t):
            self.war_ukrainian_strike_in_russia_messages += 1
        elif is_mixed_war_context(t):
            self.war_mixed_context_messages += 1

        # Part 2
        if is_relevant_russia_pre_signal(t):
            self.pre_russia_messages += 1
            self.pre_russia_drone_mentions += count_matches(t, DRONE_PATTERNS)
            self.pre_russia_airport_closure_mentions += count_matches(t, AIRPORT_PATTERNS)
            self.pre_russia_uncertainty_mentions += count_matches(t, UNCERTAINTY_PATTERNS)

            if len(self._explanations["pre_signal_examples"]) < 5:
                self._explanations["pre_signal_examples"].append(t[:180])

            for r in extract_regions(t):
                self._pre_region_counter[r] += 1

        if is_russian_drone_air_defense(t):
            self.pre_russia_drone_air_defense_messages += 1
            if len(self._explanations["air_defense_examples"]) < 5:
                self._explanations["air_defense_examples"].append(t[:180])

        # Part 3
        if is_target_energy_attack(t):
            self.energy_attack_messages += 1
            self.energy_target_messages += 1
            self.energy_explosion_or_fire_mentions += count_matches(t, EXPLOSION_OR_FIRE_PATTERNS)

            if count_matches(t, ENERGY_PATTERNS) > 0:
                pass

            if count_matches(t, REFINERY_OR_OIL_DEPOT_PATTERNS) > 0:
                self.energy_refinery_or_oil_depot_messages += 1

            if count_matches(t, OTHER_ENERGY_INFRA_PATTERNS) > 0:
                self.energy_other_infra_messages += 1

            if len(self._explanations["energy_examples"]) < 5:
                self._explanations["energy_examples"].append(t[:180])

            for r in extract_regions(t):
                self._energy_region_counter[r] += 1

        if is_relevant_energy_confirmation(t):
            self.energy_confirmation_messages += 1

    def finalize(self):
        self.pre_russia_top_regions_json = json.dumps(
            summarize_regions(self._pre_region_counter, top_n=3),
            ensure_ascii=False,
        )

        self.energy_top_regions_json = json.dumps(
            summarize_regions(self._energy_region_counter, top_n=3),
            ensure_ascii=False,
        )

        self.feature_explanation_json = json.dumps(
            self._explanations,
            ensure_ascii=False,
        )
