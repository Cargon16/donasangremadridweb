#!/usr/bin/env python3
"""Fetch blood reserve levels and mobile donation points in Madrid from donarsangre.org.

Uses only the Python standard library so it runs anywhere with zero dependencies.
"""

from __future__ import annotations

import json
import re
import ssl
import sys
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.request import Request, urlopen

USER_AGENT = "Mozilla/5.0 (compatible; VenaAltruistaMadridBot/1.0; +https://cargon16.github.io/donasangremadridweb/)"
RESERVES_URL = "https://www.donarsangre.org/"
MOBILE_POINTS_URL = "https://www.donarsangre.org/proximos-puntos-moviles/"
OUTPUT_PATH = Path(__file__).resolve().parent.parent / "data" / "madrid-status.json"


def fetch_html(url: str) -> str:
    req = Request(url, headers={"User-Agent": USER_AGENT})
    ctx = ssl.create_default_context()
    try:
        with urlopen(req, timeout=20, context=ctx) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except ssl.SSLError:
        unverified = ssl._create_unverified_context()
        with urlopen(req, timeout=20, context=unverified) as resp:
            return resp.read().decode("utf-8", errors="replace")


class BloodLevelParser(HTMLParser):
    """Parse ul.semafor-list > li items containing h3 (group/status) and p (message)."""

    def __init__(self) -> None:
        super().__init__()
        self.in_semafor_ul = False
        self.ul_depth = 0
        self.in_li = False
        self.capture_tag: str | None = None
        self.current_classes = ""
        self.current_h3: list[str] = []
        self.current_p: list[str] = []
        self.levels: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_dict = dict(attrs)
        classes = attr_dict.get("class") or ""

        if tag == "ul":
            if self.in_semafor_ul:
                self.ul_depth += 1
            elif "semafor-list" in classes.split():
                self.in_semafor_ul = True
                self.ul_depth = 1
                return

        if not self.in_semafor_ul:
            return

        if tag == "li" and self.ul_depth == 1:
            self.in_li = True
            self.current_classes = ""
            self.current_h3 = []
            self.current_p = []
        elif self.in_li and tag in ("h3", "p"):
            self.capture_tag = tag
            if tag == "h3":
                self.current_classes = classes

    def handle_endtag(self, tag: str) -> None:
        if not self.in_semafor_ul:
            return

        if tag == "ul":
            self.ul_depth -= 1
            if self.ul_depth <= 0:
                self.in_semafor_ul = False
                self.ul_depth = 0
        elif tag in ("h3", "p") and self.capture_tag == tag:
            self.capture_tag = None
        elif tag == "li" and self.in_li:
            group = re.sub(r"\s+", " ", "".join(self.current_h3)).strip()
            message = re.sub(r"\s+", " ", "".join(self.current_p)).strip()
            if group and message:
                status = "success"
                if "has-background-danger" in self.current_classes:
                    status = "danger"
                elif "has-background-warning" in self.current_classes:
                    status = "warning"
                self.levels.append({"group": group, "message": message, "status": status})
            self.in_li = False

    def handle_data(self, data: str) -> None:
        if self.capture_tag == "h3":
            self.current_h3.append(data)
        elif self.capture_tag == "p":
            self.current_p.append(data)


class MobilePointsParser(HTMLParser):
    """Parse ol.punts-mobils > li date groups and ul.columns > li.column points."""

    def __init__(self) -> None:
        super().__init__()
        self.in_ol = False
        self.ol_depth = 0
        self.in_date_li = False
        self.in_time = False
        self.date_buf: list[str] = []

        self.in_columns_ul = False
        self.in_column_li = False
        self.in_h3 = False
        self.in_p = False
        self.h3_buf: list[str] = []
        self.p_buf: list[str] = []
        self.current_ps: list[list[str]] = []

        self.current_items: list[dict] = []
        self.date_groups: list[dict] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_dict = dict(attrs)
        classes = (attr_dict.get("class") or "").split()

        if tag == "ol":
            if self.in_ol:
                self.ol_depth += 1
            elif "punts-mobils" in classes:
                self.in_ol = True
                self.ol_depth = 1
                return

        if not self.in_ol:
            return

        if tag == "li" and not self.in_columns_ul and self.ol_depth == 1:
            self.in_date_li = True
            self.date_buf = []
            self.current_items = []
        elif self.in_date_li and tag == "time":
            self.in_time = True
        elif self.in_date_li and tag == "ul" and "columns" in classes:
            self.in_columns_ul = True
        elif self.in_columns_ul and tag == "li" and "column" in classes:
            self.in_column_li = True
            self.h3_buf = []
            self.current_ps = []
        elif self.in_column_li and tag == "h3":
            self.in_h3 = True
        elif self.in_column_li and tag == "p":
            self.in_p = True
            self.p_buf = []
        elif self.in_p and tag == "br":
            self.p_buf.append("||")

    def handle_endtag(self, tag: str) -> None:
        if not self.in_ol:
            return

        if tag == "ol":
            self.ol_depth -= 1
            if self.ol_depth <= 0:
                self.in_ol = False
                self.ol_depth = 0
        elif tag == "time" and self.in_time:
            self.in_time = False
        elif tag == "h3" and self.in_h3:
            self.in_h3 = False
        elif tag == "p" and self.in_p:
            self.in_p = False
            raw = "".join(self.p_buf)
            parts = [re.sub(r"\s+", " ", part).strip() for part in raw.split("||")]
            self.current_ps.append([p for p in parts if p])
        elif tag == "li" and self.in_column_li:
            self.in_column_li = False
            city = re.sub(r"\s+", " ", "".join(self.h3_buf)).strip()
            if self.current_ps:
                address = " ".join(self.current_ps[0]).strip()
                times = self.current_ps[-1] if len(self.current_ps) > 1 else []
                if city or address:
                    self.current_items.append(
                        {"city": city, "address": address, "times": times}
                    )
        elif tag == "ul" and self.in_columns_ul:
            self.in_columns_ul = False
        elif tag == "li" and self.in_date_li and not self.in_columns_ul:
            self.in_date_li = False
            raw_date = re.sub(r"\s+", " ", "".join(self.date_buf)).strip()
            formatted_date = re.sub(r"(\d)(de)", r"\1 \2", raw_date)
            if formatted_date:
                formatted_date = formatted_date[0].upper() + formatted_date[1:]
            if formatted_date and self.current_items:
                self.date_groups.append(
                    {"date": formatted_date, "items": self.current_items}
                )

    def handle_data(self, data: str) -> None:
        if self.in_time:
            self.date_buf.append(data)
        elif self.in_h3:
            self.h3_buf.append(data)
        elif self.in_p:
            self.p_buf.append(data)


def main() -> int:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    existing: dict = {}
    if OUTPUT_PATH.exists():
        try:
            existing = json.loads(OUTPUT_PATH.read_text(encoding="utf-8"))
        except Exception:
            existing = {}

    levels = existing.get("levels", [])
    mobile_points = existing.get("mobile_points", [])

    try:
        parser = BloodLevelParser()
        parser.feed(fetch_html(RESERVES_URL))
        if parser.levels:
            levels = parser.levels
    except Exception as exc:
        print(f"Warning: could not fetch blood levels: {exc}", file=sys.stderr)

    try:
        parser = MobilePointsParser()
        parser.feed(fetch_html(MOBILE_POINTS_URL))
        if parser.date_groups:
            mobile_points = parser.date_groups
    except Exception as exc:
        print(f"Warning: could not fetch mobile points: {exc}", file=sys.stderr)

    if not levels and not mobile_points:
        print("Error: no data available from source or cache.", file=sys.stderr)
        return 1

    payload = {
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "https://www.donarsangre.org/",
        "levels": levels,
        "mobile_points": mobile_points,
    }

    OUTPUT_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Saved {len(levels)} blood levels and {len(mobile_points)} date groups to {OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
