"""Service calendars, exceptions, recurring schedules, and stop offsets."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

from ..config import GeneratorConfig
from ..ids import entity_id
from .common import GenerationContext, add_seconds, utc_from_local


@dataclass
class ServiceData:
    calendars: List[dict]
    exceptions: List[dict]
    schedules: List[dict]
    stop_times: List[dict]
    calendar_by_id: Dict[str, dict]
    schedule_by_id: Dict[str, dict]
    schedules_by_pattern: Dict[str, List[dict]]
    stop_times_by_schedule: Dict[str, List[dict]]

    def calendar_active(self, service_id: str, service_date: date) -> bool:
        calendar = self.calendar_by_id[service_id]
        day = service_date.isoformat()
        if not calendar['valid_from'] <= day < calendar['valid_to']:
            return False
        exceptions = [row for row in self.exceptions
                      if row['service_id'] == service_id and row['exception_date'] == day]
        # A REMOVE is never bypassed by a competing ADD on the same date.
        if any(row['action'] == 'REMOVE' for row in exceptions):
            return False
        if any(row['action'] == 'ADD' for row in exceptions):
            return True
        return bool(calendar[('monday', 'tuesday', 'wednesday', 'thursday',
                              'friday', 'saturday', 'sunday')[service_date.weekday()]])

    def compatible_schedules(self, pattern_id: str, service_date: date, *, historical=False, active_ids=None) -> List[dict]:
        day = service_date.isoformat()
        return [row for row in self.schedules_by_pattern.get(pattern_id, [])
                if (row['service_id'] in active_ids if active_ids is not None else self.calendar_active(row['service_id'], service_date))
                and (historical or (row['valid_from'] <= day
                     and (row['valid_to'] is None or day < row['valid_to'])))]

    def calendar_for_date(self, service_date: date, event_ids: Optional[List[str]] = None) -> str:
        def by_type(day_type: str) -> str:
            for calendar in self.calendar_by_id.values():
                if calendar["calendar_day_type"] == day_type:
                    return calendar["service_id"]
            raise KeyError(day_type)

        weekday = service_date.weekday()
        if event_ids:
            return by_type("SPECIAL")
        if weekday >= 5:
            return by_type("WEEKEND")
        if service_date.month in (1, 4) and service_date.day in (1, 10, 20):
            return by_type("HOLIDAY")
        return by_type("WEEKDAY")

    def active_schedule(self, pattern_id: str, service_date: date) -> Optional[dict]:
        for schedule in self.schedules_by_pattern.get(pattern_id, []):
            start = date.fromisoformat(schedule["valid_from"])
            end = date.fromisoformat(schedule["valid_to"]) if schedule["valid_to"] else None
            if start <= service_date and (end is None or service_date < end):
                return schedule
        candidates = self.schedules_by_pattern.get(pattern_id, [])
        return candidates[0] if candidates else None


def _calendar_specs(config: GeneratorConfig) -> List[Tuple[str, str, str, Tuple[bool, ...]]]:
    if config.is_smoke:
        return [
            ("CAL_WEEKDAY", "Synthetic Weekday Calendar", "WEEKDAY", (True, True, True, True, True, False, False)),
            ("CAL_WEEKEND", "Synthetic Weekend Calendar", "WEEKEND", (False, False, False, False, False, True, True)),
            ("CAL_HOLIDAY", "Synthetic Holiday Calendar", "HOLIDAY", (False, False, False, False, False, False, False)),
            ("CAL_SPECIAL", "Synthetic Special Event Calendar", "SPECIAL", (True, True, True, True, True, True, True)),
        ]
    # Twelve distinct calendar variants retain the approved multiple-calendar
    # design while keeping each calendar's flags explicit.
    names = ["WEEKDAY", "WEEKEND", "HOLIDAY", "SEASONAL", "SPECIAL", "EARLY", "LATE", "EXPRESS", "FEEDER", "SOCIAL", "WINTER", "SUMMER"]
    result = []
    for index, name in enumerate(names):
        if name == "WEEKEND":
            flags = (False, False, False, False, False, True, True)
        elif name == "HOLIDAY":
            flags = (False, False, False, False, False, False, False)
        else:
            flags = (True, True, True, True, True, index % 2 == 0, index % 2 == 1)
        result.append((f"CAL_{name}", f"Synthetic {name.title()} Calendar {index + 1}", name, flags))
    return result


def build_service(ctx: GenerationContext, network, context_events: List[dict]) -> ServiceData:
    config = ctx.config
    calendar_rows: List[dict] = []
    calendar_by_natural: Dict[str, dict] = {}
    for natural, name, day_type, flags in _calendar_specs(config):
        service_id = entity_id("ServiceCalendar", natural, namespace=config.identity_namespace)
        valid_from = config.history_start - timedelta(days=60)
        valid_to = config.history_end + timedelta(days=1)
        row = ctx.base("service_calendar", utc_from_local(valid_from, 0), ingestion_time=utc_from_local(valid_from, 60))
        row.update({
            "service_id": service_id,
            "service_name": name,
            "timezone": config.timezone_name,
            "valid_from": valid_from.isoformat(),
            "valid_to": valid_to.isoformat(),
            "monday": flags[0], "tuesday": flags[1], "wednesday": flags[2], "thursday": flags[3],
            "friday": flags[4], "saturday": flags[5], "sunday": flags[6],
            "calendar_day_type": day_type,
            "published_at_utc": utc_from_local(valid_from - timedelta(days=1), 0),
        })
        calendar_rows.append(row)
        calendar_by_natural[natural] = row
    calendar_by_id = {row["service_id"]: row for row in calendar_rows}

    # One exception per context event provides observable date-specific service
    # changes without inventing a contradictory add/remove pair.
    exception_rows: List[dict] = []
    for index, event in enumerate(context_events):
        service_day = date.fromisoformat(event["starts_at_utc"][:10])
        natural_calendar = "CAL_SPECIAL"
        service_id = calendar_by_natural[natural_calendar]["service_id"]
        exception_id = entity_id("ServiceException", {"calendar": natural_calendar, "date": service_day.isoformat(), "index": index}, namespace=config.identity_namespace)
        published = add_seconds(event["announced_at_utc"], 60)
        row = ctx.base("service_exceptions", published, ingestion_time=published)
        row.update({
            "exception_id": exception_id,
            "service_id": service_id,
            "exception_date": service_day.isoformat(),
            "action": "ADD" if event["event_type"] in {"FESTIVAL", "SPORT", "COMMUNITY_EVENT"} else "REMOVE",
            "day_type_override": "SPECIAL" if event["event_type"] in {"FESTIVAL", "SPORT", "COMMUNITY_EVENT"} else "WEATHER",
            "context_event_id": event["context_event_id"],
            "published_at_utc": published,
        })
        exception_rows.append(row)

    # Production design calls for approximately 240 date-specific exceptions;
    # add bounded synthetic holiday/calendar exceptions after the observable
    # context-linked rows without inventing contradictory overrides.
    target_exceptions = config.target_scale.get("service_exceptions", 0 if config.is_smoke else 240)
    if not config.is_smoke and len(exception_rows) < target_exceptions:
        existing_keys = {(row["service_id"], row["exception_date"]) for row in exception_rows}
        candidate_day = config.history_start
        calendar_ids = [calendar_by_natural[name]["service_id"] for name in ("CAL_WEEKDAY", "CAL_WEEKEND", "CAL_HOLIDAY", "CAL_SPECIAL")]
        extra_index = 0
        while len(exception_rows) < target_exceptions and candidate_day <= config.history_end:
            service_id = calendar_ids[extra_index % len(calendar_ids)]
            key = (service_id, candidate_day.isoformat())
            if key not in existing_keys:
                exception_id = entity_id("ServiceException", {"calendar": service_id, "date": candidate_day.isoformat(), "extra": extra_index}, namespace=config.identity_namespace)
                published = utc_from_local(candidate_day - timedelta(days=7), 0)
                row = ctx.base("service_exceptions", published, ingestion_time=published)
                row.update({"exception_id": exception_id, "service_id": service_id, "exception_date": candidate_day.isoformat(), "action": "ADD" if extra_index % 3 else "REMOVE", "day_type_override": "HOLIDAY" if extra_index % 3 else "SPECIAL", "context_event_id": None, "published_at_utc": published})
                exception_rows.append(row)
                existing_keys.add(key)
            candidate_day += timedelta(days=17)
            extra_index += 1

    schedule_rows: List[dict] = []
    stop_time_rows: List[dict] = []
    schedules_by_pattern: Dict[str, List[dict]] = {}
    stop_times_by_schedule: Dict[str, List[dict]] = {}
    schedule_by_id: Dict[str, dict] = {}
    target_schedules = config.target_scale.get("schedules", 14 if config.is_smoke else 2600)
    # Smoke has two recurring departure templates per pattern.  Production
    # scales the same construction to the approved schedule target.
    pattern_list = network.patterns
    slots = [("CAL_WEEKDAY", 7 * 3600), ("CAL_WEEKEND", 7 * 3600), ("CAL_HOLIDAY", 7 * 3600), ("CAL_SPECIAL", 7 * 3600), ("CAL_WEEKDAY", 17 * 3600), ("CAL_WEEKEND", 17 * 3600), ("CAL_SPECIAL", 17 * 3600)] if config.is_smoke else [( ("CAL_SPECIAL", "CAL_WEEKDAY", "CAL_WEEKEND", "CAL_SEASONAL")[index % 4], 6 * 3600 + (index % 8) * 900) for index in range(max(2, target_schedules // max(1, len(pattern_list))))]
    for pattern_index, pattern in enumerate(pattern_list):
        pattern_schedules: List[dict] = []
        for slot_index, (service_natural, offset) in enumerate(slots):
            if not config.is_smoke and len(schedule_rows) >= target_schedules:
                break
            natural = {"pattern_id": pattern["pattern_id"], "service_id": calendar_by_natural[service_natural]["service_id"], "departure_offset_sec": offset, "version": 1}
            schedule_id = entity_id("Schedule", natural, namespace=config.identity_namespace)
            service_id = calendar_by_natural[service_natural]["service_id"]
            published = add_seconds(pattern["published_at_utc"], 60)
            row = ctx.base("schedules", published, ingestion_time=published)
            row.update({
                "schedule_id": schedule_id,
                "pattern_id": pattern["pattern_id"],
                "service_id": service_id,
                "departure_offset_sec": offset,
                "valid_from": pattern["valid_from"],
                "valid_to": pattern["valid_to"],
                "published_at_utc": published,
                "schedule_version": 1,
            })
            schedule_rows.append(row)
            pattern_schedules.append(row)
            schedule_by_id[schedule_id] = row
            route_stops = network.route_stops_by_pattern[pattern["pattern_id"]]
            for route_stop in route_stops:
                sequence = int(route_stop["stop_sequence"])
                if sequence == 1:
                    arrival_offset = 0
                    departure_offset = 0
                else:
                    arrival_offset = (sequence - 1) * 600 + (sequence % 3) * 20
                    departure_offset = arrival_offset + 20 + (sequence % 2) * 10
                natural_sst = {"schedule_id": schedule_id, "stop_sequence": sequence}
                sst_id = entity_id("ScheduleStopTime", natural_sst, namespace=config.identity_namespace)
                sst = ctx.base("schedule_stop_times", published, ingestion_time=published)
                sst.update({
                    "schedule_stop_time_id": sst_id,
                    "schedule_id": schedule_id,
                    "route_stop_id": route_stop["route_stop_id"],
                    "stop_sequence": sequence,
                    "arrival_offset_sec": arrival_offset,
                    "departure_offset_sec": departure_offset,
                })
                stop_time_rows.append(sst)
                stop_times_by_schedule.setdefault(schedule_id, []).append(sst)
        schedules_by_pattern[pattern["pattern_id"]] = pattern_schedules

    # If a production target asks for more schedules than pattern slots create,
    # add explicit frequency versions for the first patterns while preserving
    # immutable snapshots.  This branch is not used by the smoke profile.
    if not config.is_smoke and len(schedule_rows) < target_schedules:
        for extra_index in range(len(schedule_rows), target_schedules):
            pattern = pattern_list[extra_index % len(pattern_list)]
            offset = 5 * 3600 + (extra_index % 12) * 1200
            natural = {"pattern_id": pattern["pattern_id"], "departure_offset_sec": offset, "version": extra_index + 1}
            schedule_id = entity_id("Schedule", natural, namespace=config.identity_namespace)
            service_id = calendar_by_natural["CAL_SPECIAL"]["service_id"]
            published = add_seconds(pattern["published_at_utc"], 120)
            row = ctx.base("schedules", published, ingestion_time=published)
            row.update({
                "schedule_id": schedule_id, "pattern_id": pattern["pattern_id"], "service_id": service_id,
                "departure_offset_sec": offset, "valid_from": pattern["valid_from"], "valid_to": pattern["valid_to"],
                "published_at_utc": published, "schedule_version": extra_index + 1,
            })
            schedule_rows.append(row)
            schedule_by_id[schedule_id] = row
            schedules_by_pattern.setdefault(pattern["pattern_id"], []).append(row)
            for route_stop in network.route_stops_by_pattern[pattern["pattern_id"]]:
                sequence = int(route_stop["stop_sequence"])
                arrival = 0 if sequence == 1 else (sequence - 1) * 600
                departure = 0 if sequence == 1 else arrival + 30
                sst_id = entity_id("ScheduleStopTime", {"schedule_id": schedule_id, "stop_sequence": sequence}, namespace=config.identity_namespace)
                sst = ctx.base("schedule_stop_times", published, ingestion_time=published)
                sst.update({"schedule_stop_time_id": sst_id, "schedule_id": schedule_id, "route_stop_id": route_stop["route_stop_id"], "stop_sequence": sequence, "arrival_offset_sec": arrival, "departure_offset_sec": departure})
                stop_time_rows.append(sst)
                stop_times_by_schedule.setdefault(schedule_id, []).append(sst)

    ctx.scenario("new_schedules", sum(1 for row in schedule_rows if date.fromisoformat(row["valid_from"]) >= date(2025, 7, 1)))
    ctx.evidence("service", {
        "calendar_count": len(calendar_rows),
        "exception_count": len(exception_rows),
        "schedule_count": len(schedule_rows),
        "schedule_stop_time_count": len(stop_time_rows),
        "calendar_ids": [row["service_id"] for row in calendar_rows],
    })
    return ServiceData(
        calendars=calendar_rows,
        exceptions=exception_rows,
        schedules=schedule_rows,
        stop_times=stop_time_rows,
        calendar_by_id=calendar_by_id,
        schedule_by_id=schedule_by_id,
        schedules_by_pattern=schedules_by_pattern,
        stop_times_by_schedule=stop_times_by_schedule,
    )
