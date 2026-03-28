"""Wikidata collector via SPARQL endpoint.

Fetches person birth data from https://query.wikidata.org/sparql
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Iterator, Optional

import requests

logger = logging.getLogger(__name__)

SPARQL_URL = "https://query.wikidata.org/sparql"
USER_AGENT = "NumerologyResearch/1.0 (academic research)"

# Wikidata has a 60s query timeout; use date range filters (faster than YEAR/MONTH functions).
BATCH_QUERY = """
SELECT ?person ?personLabel ?dob ?pobLabel ?occupationLabel ?dod ?codLabel
WHERE {{
  ?person wdt:P31 wd:Q5 .
  ?person wdt:P569 ?dob .
  FILTER(?dob >= "{date_from}"^^xsd:dateTime && ?dob < "{date_to}"^^xsd:dateTime)
  OPTIONAL {{ ?person wdt:P19 ?pob . }}
  OPTIONAL {{ ?person wdt:P106 ?occupation . }}
  OPTIONAL {{ ?person wdt:P570 ?dod . }}
  OPTIONAL {{ ?person wdt:P509 ?cod . }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en,zh" . }}
}}
LIMIT {limit} OFFSET {offset}
"""

# Lighter query for just birth dates + names (faster, less timeout risk)
LIGHT_QUERY = """
SELECT ?person ?personLabel ?dob
WHERE {{
  ?person wdt:P31 wd:Q5 .
  ?person wdt:P569 ?dob .
  FILTER(?dob >= "{date_from}"^^xsd:dateTime && ?dob < "{date_to}"^^xsd:dateTime)
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en,zh" . }}
}}
LIMIT {limit} OFFSET {offset}
"""


@dataclass
class WikidataPerson:
    """Parsed person record from Wikidata."""

    qid: str  # e.g. "Q937"
    name: str
    birth_date: str  # ISO: YYYY-MM-DD
    birth_year: int
    birth_month: Optional[int]
    birth_day: Optional[int]
    birth_place: Optional[str]
    occupations: list[str]
    death_date: Optional[str]
    cause_of_death: Optional[str]


class WikidataCollector:
    """Collects person birth data from Wikidata via SPARQL."""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Accept": "application/sparql-results+json",
            }
        )

    def _query(self, sparql: str) -> list[dict]:
        """Execute a SPARQL query and return bindings."""
        resp = self.session.get(
            SPARQL_URL,
            params={"query": sparql},
            timeout=90,
        )
        if resp.status_code == 429:
            logger.warning("Rate limited, sleeping 60s...")
            time.sleep(60)
            resp = self.session.get(
                SPARQL_URL,
                params={"query": sparql},
                timeout=90,
            )
        resp.raise_for_status()
        data = resp.json()
        return data.get("results", {}).get("bindings", [])

    def _parse_date(
        self, date_str: str
    ) -> tuple[Optional[int], Optional[int], Optional[int]]:
        """Parse Wikidata date string like '1879-03-14T00:00:00Z'."""
        if not date_str:
            return None, None, None
        # Handle both positive and negative years
        date_part = date_str.split("T")[0]
        parts = date_part.lstrip("+").lstrip("-").split("-")
        try:
            year = int(parts[0])
            if date_str.startswith("-"):
                year = -year
            month = int(parts[1]) if len(parts) > 1 and parts[1] != "00" else None
            day = int(parts[2]) if len(parts) > 2 and parts[2] != "00" else None
            return year, month, day
        except (ValueError, IndexError):
            return None, None, None

    def _extract_qid(self, uri: str) -> str:
        """Extract QID from Wikidata URI."""
        return uri.rsplit("/", 1)[-1] if "/" in uri else uri

    def collect_month(
        self,
        year: int,
        month: int,
        light: bool = False,
        page_size: int = 5000,
    ) -> Iterator[WikidataPerson]:
        """Collect persons born in a specific year+month with pagination.

        Args:
            year: Birth year
            month: Birth month (1-12)
            light: If True, only fetch name + DOB (faster)
            page_size: Results per SPARQL page
        """
        template = LIGHT_QUERY if light else BATCH_QUERY
        offset = 0
        persons: dict[str, WikidataPerson] = {}

        # Build date range
        date_from = f"{year:04d}-{month:02d}-01T00:00:00Z"
        if month == 12:
            date_to = f"{year + 1:04d}-01-01T00:00:00Z"
        else:
            date_to = f"{year:04d}-{month + 1:02d}-01T00:00:00Z"

        while True:
            sparql = template.format(
                date_from=date_from,
                date_to=date_to,
                limit=page_size,
                offset=offset,
            )
            try:
                bindings = self._query(sparql)
            except Exception as e:
                logger.error(f"SPARQL failed {year}-{month:02d} offset={offset}: {e}")
                break

            if not bindings:
                break

            for row in bindings:
                uri = row.get("person", {}).get("value", "")
                qid = self._extract_qid(uri)
                if not qid:
                    continue

                if qid not in persons:
                    dob = row.get("dob", {}).get("value", "")
                    yr, mo, dy = self._parse_date(dob)
                    if yr is None:
                        continue

                    dod_val = row.get("dod", {}).get("value")
                    cod_val = row.get("codLabel", {}).get("value")
                    pob_val = row.get("pobLabel", {}).get("value")

                    iso_date = f"{yr:04d}"
                    if mo:
                        iso_date += f"-{mo:02d}"
                    if dy:
                        iso_date += f"-{dy:02d}"

                    persons[qid] = WikidataPerson(
                        qid=qid,
                        name=row.get("personLabel", {}).get("value", qid),
                        birth_date=iso_date,
                        birth_year=yr,
                        birth_month=mo,
                        birth_day=dy,
                        birth_place=pob_val,
                        occupations=[],
                        death_date=dod_val.split("T")[0] if dod_val else None,
                        cause_of_death=cod_val,
                    )

                occ = row.get("occupationLabel", {}).get("value")
                if occ and occ not in persons[qid].occupations:
                    persons[qid].occupations.append(occ)

            if len(bindings) < page_size:
                break
            offset += page_size
            time.sleep(1)

        yield from persons.values()

    def collect(
        self,
        start_year: int = 1800,
        end_year: int = 2010,
        light: bool = False,
    ) -> Iterator[WikidataPerson]:
        """Collect all persons born in year range, batched by month.

        Args:
            start_year: First year to collect
            end_year: Last year (exclusive)
            light: If True, only fetch name + DOB
        """
        for y in range(start_year, end_year):
            for m in range(1, 13):
                logger.info(f"Collecting Wikidata {y}-{m:02d}...")
                count = 0
                for person in self.collect_month(y, m, light=light):
                    yield person
                    count += 1
                if count > 0:
                    logger.info(f"  -> {count} persons")
                time.sleep(0.5)
