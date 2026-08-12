#!/usr/bin/env python3
"""
garmin_sync.py — Baja datos de salud desde Garmin Connect y los guarda
en JSON (uno por día) y en un CSV acumulado, listos para que el
dashboard de Cowork los lea.

Requisitos:
    pip install garminconnect

Uso:
    # Primera vez (o si el token vence): pide usuario/clave
    python garmin_sync.py --days 30

    # Corridas siguientes: reusa el token guardado en ~/.garminconnect
    python garmin_sync.py --days 1

Variables de entorno opcionales (evitan que te pida user/clave):
    GARMIN_EMAIL
    GARMIN_PASSWORD

Salida:
    data/daily/YYYY-MM-DD.json   -> snapshot completo de ese día
    data/garmin_history.csv      -> una fila por día, acumulado (append/update)
"""

import argparse
import csv
import getpass
import json
import os
import time
from datetime import date, timedelta
from pathlib import Path

from garminconnect import Garmin

TOKEN_DIR = os.path.expanduser("~/.garminconnect")
OUTPUT_DIR = Path("data")
DAILY_DIR = OUTPUT_DIR / "daily"
CSV_PATH = OUTPUT_DIR / "garmin_history.csv"
JS_DATA_PATH = OUTPUT_DIR / "garmin_data.js"

CSV_FIELDS = [
    "date",
    "steps",
    "resting_hr",
    "avg_stress",
    "sleep_hours",
    "sleep_score",
    "body_weight_kg",
    "body_fat_pct",
    "hrv_status",
    "calories_total",
    "active_minutes",
]


def login() -> Garmin:
    """Loguea reusando el token guardado si existe; si no, pide credenciales."""
    email = os.environ.get("GARMIN_EMAIL")
    password = os.environ.get("GARMIN_PASSWORD")

    client = Garmin()
    try:
        client.login(TOKEN_DIR)  # intenta reusar el token cacheado
        return client
    except Exception:
        pass  # no hay token válido, login completo

    if not email:
        email = input("Garmin email: ")
    if not password:
        password = getpass.getpass("Garmin password: ")

    client = Garmin(email, password)
    client.login()
    Path(TOKEN_DIR).mkdir(parents=True, exist_ok=True)
    client.garth.dump(TOKEN_DIR)  # guarda el token para la próxima corrida
    return client


def safe_get(fn, default=None):
    try:
        return fn()
    except Exception as e:
        print(f"  aviso: no se pudo traer un dato ({e})")
        return default


def fetch_day(client: Garmin, day: date) -> dict:
    d = day.isoformat()
    print(f"Bajando {d}...")

    stats = safe_get(lambda: client.get_stats(d), {}) or {}
    sleep = safe_get(lambda: client.get_sleep_data(d), {}) or {}
    hrv = safe_get(lambda: client.get_hrv_data(d), {}) or {}
    body = safe_get(lambda: client.get_body_composition(d), {}) or {}

    daily_sleep_dto = sleep.get("dailySleepDTO", {}) if isinstance(sleep, dict) else {}
    sleep_seconds = daily_sleep_dto.get("sleepTimeSeconds")
    body_composition = (body.get("totalAverage") if isinstance(body, dict) else {}) or {}

    record = {
        "date": d,
        "steps": stats.get("totalSteps"),
        "resting_hr": stats.get("restingHeartRate"),
        "avg_stress": stats.get("averageStressLevel"),
        "sleep_hours": round(sleep_seconds / 3600, 2) if sleep_seconds else None,
        "sleep_score": daily_sleep_dto.get("sleepScores", {}).get("overall", {}).get("value")
        if isinstance(daily_sleep_dto.get("sleepScores"), dict)
        else None,
        "body_weight_kg": (
            round(body_composition["weight"] / 1000, 1)
            if body_composition.get("weight")
            else None
        ),
        "body_fat_pct": body_composition.get("bodyFat"),
        "hrv_status": hrv.get("hrvSummary", {}).get("status") if isinstance(hrv, dict) else None,
        "calories_total": stats.get("totalKilocalories"),
        "active_minutes": stats.get("activeSeconds", 0) // 60 if stats.get("activeSeconds") else None,
    }
    return record


def write_daily_json(record: dict):
    DAILY_DIR.mkdir(parents=True, exist_ok=True)
    path = DAILY_DIR / f"{record['date']}.json"
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False))


def update_csv(records: list[dict]):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    existing = {}
    if CSV_PATH.exists():
        with open(CSV_PATH, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                existing[row["date"]] = row

    for r in records:
        existing[r["date"]] = {k: ("" if r.get(k) is None else r.get(k)) for k in CSV_FIELDS}

    with open(CSV_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for d in sorted(existing.keys()):
            writer.writerow(existing[d])

    return [existing[d] for d in sorted(existing.keys())]


NUMERIC_FIELDS = {
    "steps",
    "resting_hr",
    "avg_stress",
    "sleep_hours",
    "sleep_score",
    "body_weight_kg",
    "body_fat_pct",
    "calories_total",
    "active_minutes",
}


def write_js_data(all_rows: list[dict]):
    """Vuelca el historial completo como JS embebido, para que el dashboard
    (Dashboard Salud - Manu.html) lo lea con <script src="..."> sin depender
    de fetch()/CORS, sea que se abra como archivo local o servido."""
    cleaned = []
    for row in all_rows:
        item = {}
        for k in CSV_FIELDS:
            v = row.get(k, "")
            if v == "":
                item[k] = None
            elif k in NUMERIC_FIELDS:
                try:
                    item[k] = float(v) if "." in str(v) else int(v)
                except ValueError:
                    item[k] = None
            else:
                item[k] = v
        cleaned.append(item)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    js = "// generado automáticamente por garmin_sync.py — no editar a mano\n"
    js += "const GARMIN_DAILY = " + json.dumps(cleaned, ensure_ascii=False, indent=2) + ";\n"
    JS_DATA_PATH.write_text(js, encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="Sincroniza datos de Garmin Connect")
    parser.add_argument("--days", type=int, default=1, help="Cuántos días hacia atrás bajar (incluye hoy)")
    args = parser.parse_args()

    client = login()
    print(f"Login OK como {client.get_full_name() if hasattr(client, 'get_full_name') else 'usuario Garmin'}")

    today = date.today()
    records = []
    for i in range(args.days):
        day = today - timedelta(days=i)
        record = fetch_day(client, day)
        write_daily_json(record)
        records.append(record)
        if i < args.days - 1:
            time.sleep(0.4)  # pacing suave para no saturar la API de Garmin en backfills largos

    all_rows = update_csv(records)
    write_js_data(all_rows)
    print(f"\nListo. {len(records)} día(s) guardados en {OUTPUT_DIR.resolve()}")
    print(f"  - JSON por día: {DAILY_DIR}/YYYY-MM-DD.json")
    print(f"  - CSV acumulado: {CSV_PATH}")
    print(f"  - JS para el dashboard: {JS_DATA_PATH}")


if __name__ == "__main__":
    main()
