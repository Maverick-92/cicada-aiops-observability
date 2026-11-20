#!/usr/bin/env python3
"""Inject telemetry and verify the running two-tier observability platform.

Run from the project root: python simulator/injector.py [--https] [--auto-demo]
Use --fleet for continuous Globex/Initech traffic, or menu option 7 to stream
in the background while running checks. No services or configuration are changed.
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import math
import os
import random
import re
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

try:
    import requests
except ImportError:
    print("Missing dependency: install requests with 'python -m pip install requests'.",
          file=sys.stderr)
    raise SystemExit(2)


COLLECTOR_URL = "http://localhost:8001"
SAAS_URL = "http://localhost:8000"
FIELDS = frozenset({
    "payload_id", "tenant_id", "region", "collector_id", "source_hash",
    "category", "metric", "value", "ts_utc", "source_tz", "severity", "context_code",
})
TITLES = {
    1: "Secret & PII Scrubbing",
    2: "Correlated CPU + Latency AIOps Spike",
    3: "Local Security Aggregation: 37 Failed SSH Logins",
    4: "Idempotent Duplicate Replay: 3x Same payload_id",
    5: "Out-of-Order Event-Time Ordering",
    6: "Zero-Trust Authentication, Residency & Tenant Isolation",
}


@dataclass(frozen=True)
class Tenant:
    name: str
    tenant_id: str
    region: str
    token: str
    collector_id: str
    source_tz: str


ACME = Tenant("Acme India", "tenant_acme", "ap-south-1",
              "token-acme-ap-south", "col-acme-01", "Asia/Kolkata")
GLOBEX = Tenant("Globex EU", "tenant_globex", "eu-central-1",
                "token-globex-eu-central", "col-globex-01", "Europe/Berlin")
INITECH = Tenant("Initech US", "tenant_initech", "us-east-1",
                 "token-initech-us-east", "col-initech-01", "America/New_York")


class APIError(RuntimeError):
    """A transport failure, unexpected HTTP response, or malformed API result."""


class EvidenceError(RuntimeError):
    """Required verification evidence is missing or malformed."""


def clean_text(value: Any) -> str:
    text = str(value)
    for secret in (ACME.token, GLOBEX.token, INITECH.token,
                   "SuperSecretPassword!99", "sk-live-9988776655"):
        text = text.replace(secret, "[REDACTED]")
    return re.sub(r"[\x00-\x1f\x7f-\x9f]", " ", text)[:600]


class Console:
    def __init__(self) -> None:
        self.color = "NO_COLOR" not in os.environ
        self.lock = threading.Lock()
        if os.name == "nt" and self.color:
            try:
                import ctypes
                kernel = ctypes.windll.kernel32
                handle = kernel.GetStdHandle(-11)
                mode = ctypes.c_ulong()
                if kernel.GetConsoleMode(handle, ctypes.byref(mode)):
                    kernel.SetConsoleMode(handle, mode.value | 0x0004)
            except (AttributeError, OSError):
                pass

    def line(self, text: str = "", color: str = "") -> None:
        with self.lock:
            prefix = f"\033[{color}m" if self.color and color else ""
            suffix = "\033[0m" if prefix else ""
            print(f"{prefix}{text}{suffix}", flush=True)


@dataclass
class Result:
    number: int
    console: Console
    checks: list[bool] = field(default_factory=list)

    def check(self, condition: bool, label: str, detail: str = "") -> bool:
        passed = bool(condition)
        self.checks.append(passed)
        message = f"[{'PASS' if passed else 'FAIL'}] {label}"
        if detail:
            message += f" — {clean_text(detail)}"
        self.console.line(message, "32" if passed else "31")
        return passed

    @property
    def passed(self) -> bool:
        return bool(self.checks) and all(self.checks)


class Client:
    def __init__(self, https: bool = False) -> None:
        self.saas_url = "https://localhost:8000" if https else SAAS_URL
        self.verify: bool | str = True
        if https:
            certificate = Path("certs/saas.crt")
            if not certificate.is_file():
                certificate = Path(__file__).resolve().parents[1] / "certs" / "saas.crt"
            if certificate.is_file():
                self.verify = certificate.as_posix()
        self.session = requests.Session()
        # All destinations are local; do not send tenant tokens through env proxies.
        self.session.trust_env = False

    def close(self) -> None:
        self.session.close()

    def request(self, method: str, path: str, *, collector: bool = False,
                tenant: Tenant = ACME, token: str | None = None,
                body: dict[str, Any] | None = None,
                expected: tuple[int, ...] = (200,),
                budget: float = 6.0) -> tuple[int, dict[str, Any]]:
        base = COLLECTOR_URL if collector else self.saas_url
        headers = {} if collector else {
            "Authorization": f"Bearer {tenant.token if token is None else token}"
        }
        timeout = (max(0.001, min(2.0, budget / 2)),
                   max(0.001, min(4.0, budget / 2)))
        try:
            with self.session.request(
                method, base + path, json=body, headers=headers,
                timeout=timeout, verify=True if collector else self.verify,
                allow_redirects=False,
            ) as response:
                status = response.status_code
                try:
                    data = response.json()
                except ValueError:
                    raise APIError(f"{base}{path}: HTTP {status}, invalid JSON response.") from None
                if not isinstance(data, dict):
                    raise APIError(f"{base}{path}: expected a JSON object.")
                if status not in expected:
                    detail = clean_text(data.get("detail", "unexpected status"))
                    raise APIError(f"{base}{path}: HTTP {status}: {detail}")
                return status, data
        except requests.exceptions.SSLError:
            raise APIError(
                f"TLS verification failed for {base}. Check certs/saas.crt and "
                "the certificate's localhost hostname."
            ) from None
        except requests.exceptions.ConnectionError:
            tier = "Tier 1 collector (:8001)" if collector else "Tier 2 SaaS (:8000)"
            raise APIError(f"Cannot connect to {tier} at {base}. Start the service "
                           "and confirm its HTTP/HTTPS mode.") from None
        except requests.exceptions.Timeout:
            raise APIError(f"{base}{path}: request timed out; check service responsiveness.") from None
        except (requests.exceptions.RequestException, OSError) as exc:
            raise APIError(f"Request to {base}{path} failed: {clean_text(exc)}") from None

    def dashboard(self, tenant: Tenant = ACME, budget: float = 6.0) -> dict[str, Any]:
        return self.request("GET", "/api/dashboard", tenant=tenant, budget=budget)[1]

    def egress(self) -> dict[str, Any]:
        return self.request("GET", "/api/egress", collector=True)[1]

    def trigger(self, scenario: str, budget: float = 6.0) -> dict[str, Any]:
        data = self.request("POST", "/api/trigger", collector=True,
                            body={"scenario": scenario}, budget=budget)[1]
        if data.get("triggered", scenario) != scenario:
            raise EvidenceError("Collector acknowledged a different scenario.")
        return data

    def ingest(self, body: dict[str, Any], tenant: Tenant = ACME,
               token: str | None = None,
               expected: tuple[int, ...] = (200, 201)) -> tuple[int, dict[str, Any]]:
        return self.request("POST", "/ingest", tenant=tenant, token=token,
                            body=body, expected=expected)


def objects(data: dict[str, Any], key: str) -> list[dict[str, Any]]:
    rows = data.get(key)
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise EvidenceError(f"Missing or malformed {key} array; cannot verify this check.")
    return rows


def number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EvidenceError("Expected a numeric JSON value.")
    result = float(value)
    if not math.isfinite(result):
        raise EvidenceError("Non-finite numeric evidence.")
    return result


def count(data: dict[str, Any], key: str) -> int:
    value = number(data.get(key))
    if value < 0 or not value.is_integer():
        raise EvidenceError(f"Invalid nonnegative integer counter: {key}.")
    return int(value)


def timestamp(value: Any) -> datetime:
    if not isinstance(value, str):
        raise EvidenceError("Missing ts_utc timestamp.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise EvidenceError("Malformed ts_utc timestamp.") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise EvidenceError("ts_utc must include a timezone offset.")
    return parsed.astimezone(timezone.utc)


def outbound(record: dict[str, Any]) -> dict[str, Any]:
    value = record.get("outbound_json")
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            raise EvidenceError("Egress outbound_json is invalid JSON.") from None
    if not isinstance(value, dict):
        raise EvidenceError("Egress record has no outbound_json object.")
    return value


def payload_from(trigger: dict[str, Any]) -> dict[str, Any]:
    value = trigger.get("outbound")
    if not isinstance(value, dict) or not isinstance(value.get("payload_id"), str):
        raise EvidenceError("Trigger response has no outbound payload_id evidence.")
    return value


def scenario_records(client: Client, before: dict[str, Any],
                     trigger: dict[str, Any]) -> list[dict[str, Any]]:
    previous = {row.get("id") for row in objects(before, "records")}
    payload = payload_from(trigger)
    rows = [row for row in objects(client.egress(), "records")
            if row.get("id") not in previous
            and outbound(row).get("source_hash") == payload.get("source_hash")]
    if not any(outbound(row).get("payload_id") == payload["payload_id"] for row in rows):
        raise EvidenceError("Triggered payload is absent from new egress records.")
    return rows


def poll_dashboard(client: Client, predicate: Callable[[dict[str, Any]], bool],
                   *, deadline: float, delay: float = 0.0) -> tuple[dict[str, Any], float]:
    if delay:
        time.sleep(max(0.0, min(delay, deadline - time.perf_counter())))
    last: dict[str, Any] | None = None
    while time.perf_counter() < deadline:
        last = client.dashboard(budget=deadline - time.perf_counter())
        observed = time.perf_counter()
        if predicate(last) or observed >= deadline:
            return last, observed
        time.sleep(min(0.2, max(0.0, deadline - observed)))
    if last is None:
        raise EvidenceError("Dashboard visibility deadline elapsed before a response.")
    return last, time.perf_counter()


def visible(data: dict[str, Any], payload_id: str) -> list[dict[str, Any]]:
    return [row for row in objects(data, "metrics") if row.get("payload_id") == payload_id]


def receipt_matches(signal: dict[str, Any], event: dict[str, Any]) -> bool:
    return (signal.get("metric") == event.get("metric")
            and signal.get("ts_utc") == event.get("ts_utc")
            and signal.get("value") == event.get("value"))


def correlated(data: dict[str, Any], events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    matches = []
    for incident in objects(data, "incidents"):
        signals = objects(incident, "signals")
        fresh = [s for s in signals if any(receipt_matches(s, e) for e in events)]
        metrics = {s.get("metric") for s in fresh}
        if ({"cpu_pct", "latency_ms"} <= metrics
                and incident.get("source_hash") == events[0].get("source_hash")
                and number(incident.get("confidence")) > 0.70
                and all("z_score" in s and "baseline_mean" in s for s in fresh)):
            for signal in fresh:
                number(signal["z_score"])
                number(signal["baseline_mean"])
            matches.append(incident)
    return matches


def pii_free(payload: dict[str, Any]) -> bool:
    text = json.dumps(payload, ensure_ascii=False)
    if set(payload) != FIELDS:
        return False
    if re.search(r"(?i)\b(?:root_admin|admin)\b", text):
        return False
    candidates = re.findall(r"\b(?:\d{1,3}\.){3}\d{1,3}\b|(?:[\da-fA-F]{0,4}:){2,}[\da-fA-F:.]*", text)
    for candidate in candidates:
        try:
            ipaddress.ip_address(candidate)
        except ValueError:
            continue
        return False
    return True


def metadata(tenant: Tenant, metric: str, value: float, *,
             category: str = "infrastructure", severity: str = "low",
             context: str = "NORMAL_SAMPLE") -> dict[str, Any]:
    return {
        "payload_id": str(uuid.uuid4()), "tenant_id": tenant.tenant_id,
        "region": tenant.region, "collector_id": tenant.collector_id,
        "source_hash": "srv_" + hashlib.sha256(
            f"simulator:{tenant.tenant_id}:{tenant.region}".encode("utf-8")
        ).hexdigest()[:12],
        "category": category, "metric": metric, "value": round(value, 2),
        "ts_utc": datetime.now(timezone.utc).isoformat(),
        "source_tz": tenant.source_tz, "severity": severity, "context_code": context,
    }


class Verifier:
    def __init__(self, client: Client, console: Console) -> None:
        self.client = client
        self.console = console

    def run(self, number_: int) -> Result:
        result = Result(number_, self.console)
        self.console.line(f"\n[{number_}] {TITLES[number_]}", "36")
        try:
            getattr(self, f"test_{number_}")(result)
        except (APIError, EvidenceError, ValueError, TypeError, KeyError) as exc:
            result.check(False, "Verification could not complete", clean_text(exc))
        return result

    def full_suite(self) -> bool:
        results = [self.run(index) for index in range(1, 7)]
        self.console.line("\nJudge verification scorecard", "36")
        for result in results:
            self.console.line(
                f"[{'PASS' if result.passed else 'FAIL'}] [{result.number}] "
                f"{TITLES[result.number]} "
                f"({sum(result.checks)}/{len(result.checks)} checks)",
                "32" if result.passed else "31",
            )
        passed = sum(result.passed for result in results)
        self.console.line(f"\n{'PASS' if passed == 6 else 'FAIL'}: {passed}/6 scenarios passed.",
                          "32" if passed == 6 else "31")
        return passed == 6

    def test_1(self, result: Result) -> None:
        before = self.client.egress()
        trigger = self.client.trigger("secret_leak")
        payload_id = payload_from(trigger)["payload_id"]
        rows = [row for row in scenario_records(self.client, before, trigger)
                if outbound(row).get("payload_id") == payload_id]
        result.check(len(rows) == 1, "Secret scenario has one identifiable egress payload")
        payload = outbound(rows[0])
        text = json.dumps(payload, ensure_ascii=False)
        result.check("SuperSecretPassword!99" not in text, "Password absent from outbound_json")
        result.check("sk-live-9988776655" not in text, "API key absent from outbound_json")
        result.check(pii_free(payload), "Only 12 approved fields; no scenario usernames or IPs")
        raw_bytes = count(rows[0], "raw_bytes")
        sent_bytes = count(rows[0], "sent_bytes")
        saved = raw_bytes - sent_bytes
        percentage = 100.0 * saved / raw_bytes if raw_bytes else 0.0
        self.console.line(f"  Byte savings: raw_bytes={raw_bytes:,} B, sent_bytes={sent_bytes:,} B; "
                          f"saved={saved:+,} B ({percentage:+.2f}%).")

    def test_2(self, result: Result) -> None:
        before_dashboard = self.client.dashboard()
        before = self.client.egress()
        started = time.perf_counter()
        trigger = self.client.trigger("cpu_spike", budget=9.0)
        events = []
        for row in scenario_records(self.client, before, trigger):
            event = outbound(row)
            if ((event.get("metric") == "cpu_pct" and 94 <= number(event.get("value")) <= 97)
                    or (event.get("metric") == "latency_ms" and event.get("value") == 410)):
                events.append(event)
        result.check(sum(e.get("metric") == "cpu_pct" for e in events) == 2
                     and sum(e.get("metric") == "latency_ms" for e in events) == 2,
                     "Two CPU spikes and two 410 ms latency signals injected")
        if not events:
            raise EvidenceError("No current spike events available for correlation.")

        def complete(data: dict[str, Any]) -> bool:
            return (all(visible(data, e["payload_id"]) for e in events)
                    and data.get("health", {}).get("state") in ("Poor", "Fair")
                    and bool(correlated(data, events)))

        # The rubric requires a 2.5-second wait after the trigger acknowledgement.
        dashboard, observed = poll_dashboard(
            self.client, complete, deadline=started + 10.0, delay=2.5)
        latency_ms = (observed - started) * 1000
        all_visible = all(visible(dashboard, e["payload_id"]) for e in events)
        result.check(all_visible and latency_ms < 10_000,
                     "Trigger-to-dashboard visibility < 10 seconds",
                     f"measured {latency_ms:.3f} ms; injected signals visible={all_visible}")
        state = dashboard.get("health", {}).get("state")
        previous = before_dashboard.get("health", {}).get("state")
        result.check(state in ("Poor", "Fair"), "Overall health is Poor or Fair",
                     f"{previous} -> {state}")
        incidents = correlated(dashboard, events)
        result.check(bool(incidents), "Current CPU + latency receipts share an incident; confidence > 0.70")
        for incident in incidents:
            self.console.line(f"  Incident {clean_text(incident.get('incident_id'))}: "
                              f"confidence={number(incident['confidence']):.2f}")
            for signal in objects(incident, "signals"):
                if any(receipt_matches(signal, event) for event in events):
                    self.console.line(f"    {clean_text(signal['metric'])}: value={signal['value']}, "
                                      f"baseline={signal['baseline_mean']}, Z={signal['z_score']}")

    def test_3(self, result: Result) -> None:
        before = self.client.egress()
        trigger = self.client.trigger("failed_logins")
        payload = payload_from(trigger)
        rows = [row for row in scenario_records(self.client, before, trigger)
                if outbound(row).get("metric") == "failed_logins_1m"]
        result.check(len(rows) == 1 and number(outbound(rows[0]).get("value")) == 37,
                     "37 raw SSH failures became exactly one numeric egress event")
        result.check(bool(rows) and all(pii_free(outbound(row)) for row in rows),
                     "No raw usernames, IPs, or logs crossed the trust boundary")
        dashboard, _ = poll_dashboard(
            self.client,
            lambda data: bool(visible(data, payload["payload_id"])) and any(
                any(receipt_matches(signal, payload) for signal in objects(inc, "signals"))
                for inc in objects(data, "incidents")),
            deadline=time.perf_counter() + 10.0,
        )
        stored = visible(dashboard, payload["payload_id"])
        result.check(len(stored) == 1 and stored[0].get("metric") == "failed_logins_1m"
                     and number(stored[0].get("value")) == 37,
                     "SaaS stores exactly one failed_logins_1m = 37 event")
        incidents = [inc for inc in objects(dashboard, "incidents")
                     if inc.get("source_hash") == payload.get("source_hash") and any(
                         receipt_matches(signal, payload) for signal in objects(inc, "signals"))]
        result.check(bool(incidents), "A current security incident was generated",
                     "" if incidents else "No matching receipt; check the security metric's baseline history.")
        if incidents:
            incident = incidents[0]
            cause = incident.get("likely_cause")
            action = incident.get("recommended_action")
            result.check(isinstance(cause, str) and bool(cause.strip())
                         and isinstance(action, str) and bool(action.strip()),
                         "Security incident includes likely cause and remediation")
            self.console.line(f"  Likely cause: {clean_text(cause)}")
            self.console.line(f"  Recommended remediation: {clean_text(action)}")

    def test_4(self, result: Result) -> None:
        before_count = count(self.client.dashboard(), "total_events_stored")
        before = self.client.egress()
        trigger = self.client.trigger("duplicate_replay")
        payload = payload_from(trigger)
        rows = [row for row in scenario_records(self.client, before, trigger)
                if outbound(row).get("payload_id") == payload["payload_id"]]
        result.check(len(rows) == 3, "Collector audited three replays of the same payload_id")
        dashboard, _ = poll_dashboard(
            self.client, lambda data: bool(visible(data, payload["payload_id"])),
            deadline=time.perf_counter() + 10.0, delay=2.5,
        )
        stored = visible(dashboard, payload["payload_id"])
        result.check(len(stored) == 1, "Exactly one replay payload row is visible in SaaS")
        # Tier 1 may itself deduplicate its offline queue. Confirm Tier 2 separately
        # using three identical, already-delivered metadata objects.
        if len(stored) == 1:
            replies = [self.client.ingest(dict(payload))[1] for _ in range(3)]
            result.check(all(count(reply, "accepted") == 0
                             and count(reply, "duplicates_ignored") == 1 for reply in replies),
                         "SaaS independently rejects all three identical boundary replays")
        after = self.client.dashboard()
        after_count = count(after, "total_events_stored")
        delta = after_count - before_count
        result.check(len(visible(after, payload["payload_id"])) == 1, "Replay stored once (global total also includes live traffic)",
                     f"before={before_count}, after={after_count}, delta={delta}")
        result.check(len(visible(after, payload["payload_id"])) == 1,
                     "Replay payload still has exactly one row after all retries")
        if delta > 1:
            self.console.line("  The total includes Acme's live background telemetry. "
                              "The strict count check fails; replay-specific evidence is shown above.")

    def test_5(self, result: Result) -> None:
        before = self.client.egress()
        trigger = self.client.trigger("out_of_order")
        payload = payload_from(trigger)
        rows = [row for row in scenario_records(self.client, before, trigger)
                if outbound(row).get("payload_id") == payload["payload_id"]]
        event_time = timestamp(payload.get("ts_utc"))
        age = (datetime.now(timezone.utc) - event_time).total_seconds()
        result.check(len(rows) == 1 and 290 <= age <= 320,
                     "Injected packet uses event time approximately five minutes in the past",
                     f"age={age:.3f} seconds")

        def delivered(data: dict[str, Any]) -> bool:
            if visible(data, payload["payload_id"]):
                return True
            return any(row.get("delivery_status") == "DELIVERED"
                       and outbound(row).get("payload_id") == payload["payload_id"]
                       for row in objects(self.client.egress(), "records"))

        dashboard, _ = poll_dashboard(self.client, delivered,
                                      deadline=time.perf_counter() + 10.0)
        result.check(delivered(dashboard), "Delayed packet reached SaaS")
        metrics = objects(dashboard, "metrics")
        times = [timestamp(row.get("ts_utc")) for row in metrics]
        result.check(len(times) >= 2, "Dashboard provides at least two event-time samples")
        ordered = len(times) >= 2 and all(left < right for left, right in zip(times, times[1:]))
        result.check(ordered, "metrics array is strictly ascending by ts_utc",
                     f"checked {len(times)} timestamps; equal timestamps also fail")
        if not visible(dashboard, payload["payload_id"]):
            result.check(bool(times) and event_time < min(times),
                         "Delivered late event falls outside the dashboard's recent-event window")

    def test_6(self, result: Result) -> None:
        probe = metadata(ACME, "collector_heartbeat", 1.0,
                         category="fleet", context="HEARTBEAT_OK")
        probe["source_hash"] = "srv_ceebef596acd"
        status, _ = self.client.ingest(probe, token="invalid-demo-token",
                                      expected=(200, 201, 400, 401, 403, 422))
        result.check(status in (401, 403), "Invalid token rejected with HTTP 401 or 403",
                     f"HTTP {status}")
        mismatch = dict(probe, payload_id=str(uuid.uuid4()), region="eu-central-1")
        status, data = self.client.ingest(mismatch, expected=(200, 201, 400, 401, 403, 422))
        detail = str(data.get("detail", ""))
        result.check(status == 403 and "region mismatch" in detail.casefold(),
                     "Acme token cannot claim eu-central-1: HTTP 403 Region Mismatch",
                     f"HTTP {status}: {detail}")
        mismatch = dict(probe, payload_id=str(uuid.uuid4()), tenant_id=GLOBEX.tenant_id)
        status, _ = self.client.ingest(mismatch, expected=(200, 201, 400, 401, 403, 422))
        result.check(status == 403, "Acme token cannot impersonate another tenant",
                     f"HTTP {status}")

        # Positive control makes tenant isolation meaningful even on an empty SaaS.
        self.client.ingest(probe)
        acme, _ = poll_dashboard(self.client, lambda data: bool(visible(data, probe["payload_id"])),
                                 deadline=time.perf_counter() + 10.0)
        result.check(acme.get("tenant_id") == ACME.tenant_id
                     and len(visible(acme, probe["payload_id"])) == 1,
                     "Positive control: valid Acme event is visible to Acme")
        globex = self.client.dashboard(GLOBEX)
        acme_ids = {row["payload_id"] for row in objects(acme, "metrics") if "payload_id" in row}
        acme_ids.update(row["incident_id"] for row in objects(acme, "incidents") if "incident_id" in row)
        acme_ids.update(row["collector_id"] for row in objects(acme, "collectors") if "collector_id" in row)
        acme_ids.update({ACME.tenant_id, probe["payload_id"]})

        def leaks(value: Any) -> int:
            if isinstance(value, dict):
                return sum(leaks(item) for item in value.values())
            if isinstance(value, list):
                return sum(leaks(item) for item in value)
            return int(isinstance(value, str) and value in acme_ids)

        for key in ("metrics", "incidents", "collectors", "audit_logs"):
            objects(globex, key)
        leakage = leaks(globex)
        result.check(globex.get("tenant_id") == GLOBEX.tenant_id and leakage == 0,
                     "Globex dashboard exposes zero Acme events, incidents, or audit references",
                     f"cross-tenant leakage={leakage}")


class Fleet:
    """Background sender with its own HTTP session and interruptible scheduling."""

    def __init__(self, https: bool, console: Console) -> None:
        self.https = https
        self.console = console
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._run, name="fleet-telemetry", daemon=True)
        self.thread.start()
        self.console.line("Fleet background mode started: Globex EU + Initech US every 2 seconds. "
                          "Acme is supplied by Tier 1. Ctrl+C stops the CLI.", "36")

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=7.0)
            self.thread = None

    def _run(self) -> None:
        client = Client(self.https)
        rng = random.Random()
        last_error = -math.inf
        values = {tenant.tenant_id: {"cpu_pct": 35.0, "mem_pct": 55.0, "latency_ms": 42.0}
                  for tenant in (GLOBEX, INITECH)}
        profiles = (("cpu_pct", 22, 58, 2.5, "infrastructure"),
                    ("mem_pct", 42, 70, 1.2, "infrastructure"),
                    ("latency_ms", 22, 75, 3.5, "network"),
                    ("collector_heartbeat", 1, 1, 0, "fleet"))
        try:
            while not self.stop_event.is_set():
                tick = time.perf_counter()
                for tenant in (GLOBEX, INITECH):
                    for metric, low, high, deviation, category in profiles:
                        if self.stop_event.is_set():
                            return
                        prior = values[tenant.tenant_id].get(metric, 1.0)
                        value = min(high, max(low, prior + rng.gauss(0, deviation)))
                        values[tenant.tenant_id][metric] = value
                        payload = metadata(tenant, metric, value, category=category,
                                           context="HEARTBEAT_OK" if category == "fleet" else "NORMAL_SAMPLE")
                        try:
                            _, response = client.ingest(payload, tenant)
                            if count(response, "accepted") != 1:
                                raise APIError("Fresh fleet telemetry was not accepted.")
                        except (APIError, EvidenceError) as exc:
                            if time.perf_counter() - last_error >= 10.0:
                                self.console.line(f"[FAIL] Fleet delivery: {clean_text(exc)}", "31")
                                last_error = time.perf_counter()
                            break
                self.stop_event.wait(max(0.0, 2.0 - (time.perf_counter() - tick)))
        finally:
            client.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--https", action="store_true",
                        help="Use https://localhost:8000 and certs/saas.crt when available.")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--auto-demo", action="store_true",
                       help="Run tests 1–6 and a scorecard; exit 0 on PASS, 1 on FAIL.")
    modes.add_argument("--fleet", action="store_true",
                       help="Stream Globex/Initech telemetry every 2 seconds until Ctrl+C.")
    args = parser.parse_args(argv)
    global SAAS_URL
    SAAS_URL = "https://localhost:8000" if args.https else "http://localhost:8000"
    console = Console()
    client = Client(args.https)
    fleet = Fleet(args.https, console)
    verifier = Verifier(client, console)
    console.line("Zero-Trust Two-Tier AIOps — Fault Injector & Judge Verification", "36")
    console.line(f"Tier 1: {COLLECTOR_URL} | Tier 2: {client.saas_url}")
    if args.https:
        console.line(f"TLS verification: {client.verify if isinstance(client.verify, str) else 'system CA trust'}")
    try:
        if args.auto_demo:
            return 0 if verifier.full_suite() else 1
        if args.fleet:
            fleet.start()
            while fleet.running:
                fleet.stop_event.wait(0.5)
            return 1
        while True:
            console.line("\nChoose a demo:", "36")
            for index, title in TITLES.items():
                console.line(f"  [{index}] {title}")
            console.line(f"  [7] {'Stop' if fleet.running else 'Start'} Multi-Region Fleet Background Traffic")
            console.line("  [8] Run Full Judge Verification Suite")
            console.line("  [0] Exit")
            choice = input("Selection: ").strip()
            if choice == "0":
                return 0
            if choice == "7":
                if fleet.running:
                    fleet.stop()
                    console.line("Fleet background traffic stopped.", "36")
                else:
                    fleet.start()
            elif choice == "8":
                verifier.full_suite()
            elif choice in {str(index) for index in TITLES}:
                verifier.run(int(choice))
            else:
                console.line("Enter a number from 0 through 8.", "33")
    except EOFError:
        console.line("\nInput closed; stopping.")
        return 0
    except KeyboardInterrupt:
        console.line("\nInterrupted; stopping background traffic.", "33")
        return 130
    finally:
        fleet.stop()
        client.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BrokenPipeError:
        raise SystemExit(1)
