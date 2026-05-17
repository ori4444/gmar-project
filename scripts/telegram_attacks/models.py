from dataclasses import dataclass
from datetime import date
from typing import Optional


@dataclass
class ParsedAttack:
    attack_date: date
    area: str
    targets_names: str
    target_type: str
    attack_type: str
    combined_strike: bool
    drone_scale: Optional[str]
    repeated_attack: Optional[bool]
    air_defense_active: bool
    shutdown: bool
    fire: bool
    explosions_reported: int
    hit_confirmed: bool
    report_type: str
    damage_level: str
    primary_attack: bool
    is_update_like: bool

    def to_db_dict(self, source_text: str) -> dict:
        return {
            "attack_date": self.attack_date,
            "area": self.area,
            "targets_names": self.targets_names,
            "target_type": self.target_type,
            "attack_type": self.attack_type,
            "combined_strike": self.combined_strike,
            "drone_scale": self.drone_scale,
            "repeated_attack": self.repeated_attack,
            "air_defense_active": self.air_defense_active,
            "shutdown": self.shutdown,
            "fire": self.fire,
            "explosions_reported": self.explosions_reported,
            "hit_confirmed": self.hit_confirmed,
            "report_type": self.report_type,
            "damage_level": self.damage_level,
            "source": source_text,
        }