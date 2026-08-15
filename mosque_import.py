#!/usr/bin/env python3
"""
Imports a monthly prayer timetable from a mosque's public website into our
timetable.csv format. Only extracts factual timing data (Athan/Iqamah
times) - nothing else from the page is read or stored.

Currently supports aishamasjid.org.uk's /timetable/ page, which renders a
plain HTML table (Date, Fajr Athan/Iqamah, Sunrise, Dhuhr Athan/Iqamah,
Asr Athan/Iqamah, Maghrib, Isha Athan/Iqamah) for the current month. The
site has no month-selection URL (it's client-side JS state), so a fetch
always returns the current month - call this periodically (e.g. daily) to
stay current as months roll over.
"""
import csv
import os
import re
import urllib.request
from datetime import datetime
from html.parser import HTMLParser

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}


class _TimetableParser(HTMLParser):
    """Collects <h2>-style month/year heading text and the data table's rows
    of <td> cell text, ignoring markup/comments in between."""

    def __init__(self):
        super().__init__()
        self.in_table = False
        self.in_row = False
        self.in_cell = False
        self.in_thead = False
        self.rows = []
        self._cur_row = []
        self._cur_cell = ""
        self.heading_candidates = []
        self._collecting_heading = False
        self._cur_heading = ""

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self.in_table = True
        elif tag == "thead" and self.in_table:
            self.in_thead = True
        elif tag == "tbody" and self.in_table:
            self.in_thead = False
        elif tag == "tr" and self.in_table:
            self.in_row = True
            self._cur_row = []
        elif tag == "td" and self.in_row:
            self.in_cell = True
            self._cur_cell = ""
        elif tag == "h2":
            self._collecting_heading = True
            self._cur_heading = ""

    def handle_endtag(self, tag):
        if tag == "table":
            self.in_table = False
        elif tag == "tr":
            if self.in_row and not self.in_thead and self._cur_row:
                self.rows.append(self._cur_row)
            self.in_row = False
        elif tag == "td":
            if self.in_cell:
                self._cur_row.append(self._cur_cell.strip())
            self.in_cell = False
        elif tag == "h2":
            if self._collecting_heading:
                self.heading_candidates.append(self._cur_heading.strip())
            self._collecting_heading = False

    def handle_data(self, data):
        if self.in_cell:
            self._cur_cell += data
        if self._collecting_heading:
            self._cur_heading += data


def _find_month_year(heading_candidates):
    for h in heading_candidates:
        m = re.search(r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{4})",
                      h, re.IGNORECASE)
        if m:
            return _MONTHS[m.group(1).lower()], int(m.group(2))
    return None, None


TIME_RE = re.compile(r"^\d{1,2}:\d{2}$")


def _clean_time(cell):
    """Cells sometimes hold "13:30 / 14:30" (e.g. an extra Jumuah slot) -
    take the first valid HH:MM found."""
    m = re.search(r"\d{1,2}:\d{2}", cell or "")
    return m.group(0) if m else ""


def fetch_aisha_masjid_timetable(timeout=15):
    """Returns a list of row dicts matching our timetable.csv columns, for
    whichever month the site is currently showing. Raises on any failure -
    callers should catch and report, and must not let a failure here
    overwrite an existing good timetable.csv."""
    url = "https://aishamasjid.org.uk/timetable/"
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        html = r.read().decode("utf-8", errors="replace")

    parser = _TimetableParser()
    parser.feed(html)

    month, year = _find_month_year(parser.heading_candidates)
    if not month:
        # fall back to the current month if the heading wasn't found -
        # better than failing outright, since the site only ever shows
        # "this month" anyway
        now = datetime.now()
        month, year = now.month, now.year

    out = []
    for row in parser.rows:
        if len(row) < 11:
            continue
        day_text = row[0]
        m = re.search(r"(\d{1,2})\s*$", day_text)
        if not m:
            continue
        day = int(m.group(1))
        try:
            date = datetime(year, month, day)
        except ValueError:
            continue

        fajr, iqama_fajr = _clean_time(row[1]), _clean_time(row[2])
        dhuhr, iqama_dhuhr = _clean_time(row[4]), _clean_time(row[5])
        asr, iqama_asr = _clean_time(row[6]), _clean_time(row[7])
        maghrib = _clean_time(row[8])
        isha, iqama_isha = _clean_time(row[9]), _clean_time(row[10])

        if not (fajr and dhuhr and asr and maghrib and isha):
            continue

        out.append({
            "Date": date.strftime("%d/%m/%Y"),
            "Fajr": fajr, "Dhuhr": dhuhr, "Asr": asr, "Maghrib": maghrib, "Isha": isha,
            "Iqama_Fajr": iqama_fajr, "Iqama_Dhuhr": iqama_dhuhr, "Iqama_Asr": iqama_asr,
            "Iqama_Maghrib": maghrib, "Iqama_Isha": iqama_isha,
        })

    if not out:
        raise ValueError("no rows parsed - the site's page structure may have changed")
    return out


CSV_COLUMNS = ["Date", "Fajr", "Dhuhr", "Asr", "Maghrib", "Isha",
               "Iqama_Fajr", "Iqama_Dhuhr", "Iqama_Asr", "Iqama_Maghrib", "Iqama_Isha"]


def merge_into_timetable(new_rows, timetable_file="timetable.csv"):
    """Merges new_rows into the existing CSV by Date, keeping any existing
    dates the import doesn't cover (e.g. days from a previous manual upload
    or a different month) rather than replacing the whole file."""
    existing = {}
    if os.path.exists(timetable_file):
        try:
            with open(timetable_file) as f:
                for row in csv.DictReader(f):
                    d = (row.get("Date") or "").strip()
                    if d:
                        existing[d] = row
        except Exception:
            existing = {}

    for row in new_rows:
        existing[row["Date"]] = row

    def sort_key(d):
        try:
            return datetime.strptime(d, "%d/%m/%Y")
        except ValueError:
            return datetime.max

    ordered_dates = sorted(existing.keys(), key=sort_key)
    with open(timetable_file, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for d in ordered_dates:
            row = {col: existing[d].get(col, "") for col in CSV_COLUMNS}
            row["Date"] = d
            writer.writerow(row)

    return len(new_rows), len(ordered_dates)
