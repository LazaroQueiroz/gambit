"""Saved progress with Leitner-box spaced repetition."""

from __future__ import annotations

import json
import os
from datetime import date, timedelta
from pathlib import Path

from .engine import DATA_HOME

INTERVALS = [0, 1, 3, 7, 14, 30, 60]  # days until review for each box
MASTERED_BOX = 4


class Progress:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or DATA_HOME / "progress.json"
        self.data = {"lines": {}, "days": {}, "quiz": {"right": 0, "total": 0}}
        try:
            loaded = json.loads(self.path.read_text())
            self.data.update(loaded)
        except (OSError, ValueError):
            pass

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_suffix(".tmp")
            temp.write_text(json.dumps(self.data, indent=1))
            os.replace(temp, self.path)
        except OSError:
            pass

    def record_drill(self, key: str, name: str, side: str, right: int, total: int, today: date | None = None) -> None:
        today = today or date.today()
        entry = self.data["lines"].setdefault(key, {"name": name, "side": side, "box": 0, "attempts": 0,
                                                    "clean": 0, "right": 0, "total": 0, "due": ""})
        entry["attempts"] += 1
        entry["right"] += right
        entry["total"] += total
        entry["side"] = side
        perfect = total > 0 and right == total
        if perfect:
            entry["clean"] += 1
            entry["box"] = min(entry["box"] + 1, len(INTERVALS) - 1)
        else:
            entry["box"] = max(entry["box"] - 2, 0)
        entry["due"] = (today + timedelta(days=INTERVALS[entry["box"]])).isoformat()
        entry["last"] = today.isoformat()
        day = self.data["days"].setdefault(today.isoformat(), {"drills": 0, "right": 0, "total": 0})
        day["drills"] += 1
        day["right"] += right
        day["total"] += total
        self.save()

    def record_quiz(self, correct: bool) -> None:
        self.data["quiz"]["total"] += 1
        self.data["quiz"]["right"] += int(correct)
        self.save()

    def due(self, today: date | None = None) -> list[tuple[str, dict]]:
        today_text = (today or date.today()).isoformat()
        items = [(k, v) for k, v in self.data["lines"].items() if v["due"] <= today_text]
        return sorted(items, key=lambda kv: (kv[1]["due"], kv[1]["box"]))

    def streak(self, today: date | None = None) -> int:
        day = today or date.today()
        if day.isoformat() not in self.data["days"]:
            day -= timedelta(days=1)
        count = 0
        while day.isoformat() in self.data["days"]:
            count += 1
            day -= timedelta(days=1)
        return count

    def summary(self) -> dict:
        lines = self.data["lines"]
        right = sum(v["right"] for v in lines.values())
        total = sum(v["total"] for v in lines.values())
        families: dict[str, list[int]] = {}
        for entry in lines.values():
            family = entry["name"].split(":")[0]
            bucket = families.setdefault(family, [0, 0])
            bucket[0] += entry["right"]
            bucket[1] += entry["total"]
        weak = sorted((v for v in lines.values() if v["total"]), key=lambda v: v["right"] / v["total"])[:5]
        return {
            "studied": len(lines),
            "mastered": sum(1 for v in lines.values() if v["box"] >= MASTERED_BOX),
            "due": len(self.due()),
            "right": right,
            "total": total,
            "families": families,
            "weak": weak,
            "streak": self.streak(),
            "quiz": self.data["quiz"],
        }
