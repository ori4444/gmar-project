from dataclasses import dataclass
from datetime import date
from typing import Optional


@dataclass
class ParsedEvent:
    """
    Parsed representation of a single attack event extracted from a Telegram message.

    Fields marked 'transient' are used during the review workflow but are not
    written to the database.
    """
    attack_date:          date
    area:                 str
    target_type:          str            # pipe-separated facility types
    combined_strike:      bool
    drone_scale:          Optional[str]
    air_defense_active:   bool
    shutdown:             bool
    fire:                 bool
    explosions_reported:  int
    hit_confirmed:        bool
    report_type:          str
    damage_level:         str
    confidence:           int            # 0-100 reliability score
    primary_attack:       bool           # transient — not stored in DB
    is_update_like:       bool           # transient — not stored in DB

    def to_db_dict(self, source_text: str = "") -> dict:
        return {
            "attack_date":         self.attack_date,
            "area":                self.area,
            "target_type":         self.target_type,
            "combined_strike":     self.combined_strike,
            "drone_scale":         self.drone_scale,
            "air_defense_active":  self.air_defense_active,
            "shutdown":            self.shutdown,
            "fire":                self.fire,
            "explosions_reported": self.explosions_reported,
            "hit_confirmed":       self.hit_confirmed,
            "report_type":         self.report_type,
            "damage_level":        self.damage_level,
            "confidence":          self.confidence,
            "source":              source_text,
        }


# Backward-compat alias — existing code that references ParsedAttack still works
ParsedAttack = ParsedEvent
