"""작은 JSON 파일 저장소. 상태는 모두 state_dir 아래에 남는다."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


class JsonStore:
    def __init__(self, path: Path, default: Any):
        self.path = path
        self.default = default

    def load(self) -> Any:
        if not self.path.exists():
            return json.loads(json.dumps(self.default))
        return json.loads(self.path.read_text(encoding="utf-8"))

    def save(self, data: Any) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)


class PriceHistory:
    """종목별 일별 종가(그날 마지막으로 본 가격)를 기록한다."""

    def __init__(self, state_dir: Path):
        self.store = JsonStore(state_dir / "prices.json", {})

    def record(self, code: str, date: str, price: int) -> None:
        data = self.store.load()
        data.setdefault(code, {})[date] = price
        self.store.save(data)

    def merge(self, code: str, closes: dict[str, int]) -> None:
        data = self.store.load()
        series = data.setdefault(code, {})
        for date, price in closes.items():
            series.setdefault(date, price)
        self.store.save(data)

    def closes(self, code: str) -> list[int]:
        series = self.store.load().get(code, {})
        return [series[d] for d in sorted(series)]


class DailyCounter:
    """하루 단위로 초기화되는 카운터 (주문 수, Jev 호출 수)."""

    def __init__(self, state_dir: Path):
        self.store = JsonStore(state_dir / "daily.json", {})

    def get(self, date: str, key: str) -> int:
        return self.store.load().get(date, {}).get(key, 0)

    def incr(self, date: str, key: str) -> int:
        data = self.store.load()
        data = {date: data.get(date, {})}  # 지난 날짜 정리
        data[date][key] = data[date].get(key, 0) + 1
        self.store.save(data)
        return data[date][key]
