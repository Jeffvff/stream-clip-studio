from __future__ import annotations

from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

UTC = timezone.utc


def absolute_time(value: datetime, zone: ZoneInfo) -> datetime | None:
    early = value.replace(tzinfo=zone, fold=0)
    late = value.replace(tzinfo=zone, fold=1)
    # Do not plan an ambiguous or nonexistent local time on a DST transition.
    if early.utcoffset() != late.utcoffset():
        return None
    return early


def build_slots(config: dict, now: datetime, count: int, reserved: set[str]) -> list[datetime]:
    zone = ZoneInfo(config["timezone"])
    current = now.astimezone(UTC)
    earliest = current + timedelta(minutes=int(config.get("lead_minutes", 20)))
    latest = current + timedelta(days=int(config.get("horizon_days", 10)))
    first = date.fromisoformat(str(config["start_date"]))
    days = set(config.get("weekdays", range(7)))
    if not days or any(type(day) is not int or not 0 <= day <= 6 for day in days):
        raise ValueError("weekdays usa 0 para segunda e 6 para domingo")
    daily = int(config["per_day"])
    if daily < 1 or count < 0 or earliest >= latest:
        raise ValueError("Limites do calendário inválidos")
    raw_times = config.get("times") or []
    if raw_times and config.get("interval_minutes") is not None:
        raise ValueError("Escolha times ou interval_minutes")
    if raw_times:
        times = sorted({parse_time(value) for value in raw_times})
        if len(times) < daily:
            raise ValueError("Horários distintos insuficientes para per_day")
    else:
        start = parse_time(config.get("first_time", "12:00"))
        interval = int(config.get("interval_minutes", 0))
        if interval < 1:
            raise ValueError("Informe horários ou um intervalo positivo")
        first_minute = start.hour * 60 + start.minute
        times = [time(*divmod(minutes, 60)) for minutes in range(first_minute, 1440, interval)]
        if len(times) < daily:
            raise ValueError("O intervalo e a hora inicial excedem o limite de um dia")
    occupied_days = Counter(datetime.fromisoformat(value).astimezone(zone).date() for value in reserved)
    result = []
    day = max(first, earliest.astimezone(zone).date())
    final_day = latest.astimezone(zone).date()
    while day <= final_day and len(result) < count:
        if day.weekday() in days:
            used = occupied_days[day]
            for hour in times:
                local = absolute_time(datetime.combine(day, hour), zone)
                if local is None:
                    continue
                utc = local.astimezone(UTC)
                if (utc.isoformat() not in reserved and used < daily
                        and earliest <= utc <= latest and len(result) < count):
                    result.append(local)
                    used += 1
        day += timedelta(days=1)
    return result


def parse_time(value: str) -> time:
    parsed = time.fromisoformat(str(value))
    if parsed.tzinfo or parsed.second or parsed.microsecond:
        raise ValueError("Use horários no formato HH:MM")
    return parsed
