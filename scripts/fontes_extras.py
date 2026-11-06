"""Fontes além do BCB: IBGE, PMI Brasil, FOMC e BLS.

Cada função devolve uma lista de dicionários com data já em
America/Sao_Paulo. Se a fonte oficial falhar, quem chama o módulo
reaproveita o cache da rodada anterior.
"""

from __future__ import annotations

import html
import json
import re
import ssl
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from html.parser import HTMLParser
from zoneinfo import ZoneInfo

SAO_PAULO = ZoneInfo("America/Sao_Paulo")
NEW_YORK = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")

UID_DOMAIN = "calendario-economico"

MONTHS_PT = (
    "",
    "janeiro",
    "fevereiro",
    "março",
    "abril",
    "maio",
    "junho",
    "julho",
    "agosto",
    "setembro",
    "outubro",
    "novembro",
    "dezembro",
)

MONTHS_EN = {
    "january": 1,
    "jan": 1,
    "february": 2,
    "feb": 2,
    "march": 3,
    "mar": 3,
    "april": 4,
    "apr": 4,
    "may": 5,
    "june": 6,
    "jun": 6,
    "july": 7,
    "jul": 7,
    "august": 8,
    "aug": 8,
    "september": 9,
    "sep": 9,
    "sept": 9,
    "october": 10,
    "oct": 10,
    "november": 11,
    "nov": 11,
    "december": 12,
    "dec": 12,
}

IBGE_URL = "https://servicodados.ibge.gov.br/api/v3/calendario/"
FED_URL = "https://www.federalreserve.gov/json/calendar.json"
FED_PAGE = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
BLS_CPI_URL = "https://www.bls.gov/schedule/news_release/cpi.htm"
BLS_EMP_URL = "https://www.bls.gov/schedule/news_release/empsit.htm"
BLS_ICS_URL = "https://www.bls.gov/schedule/news_release/bls.ics"
PMI_URL = "https://www.pmi.spglobal.com/Public/Release/ReleaseDates"

BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
POLITE_UA = (
    "calendario-economico/1.0 "
    "(+https://github.com/cvsezefredo-sudo/CALENDARIO-ECONOMICO)"
)

# Rótulos curtos. A ordem importa: o alias mais específico vem antes.
IBGE_LABELS = (
    ("indice-nacional-de-precos-ao-consumidor-amplo-15", "IPCA-15"),
    ("indice-nacional-de-precos-ao-consumidor-amplo-especial", "IPCA-E"),
    ("indice-nacional-de-precos-ao-consumidor-amplo", "IPCA"),
    ("indice-nacional-de-precos-ao-consumidor", "INPC"),
    ("contas-nacionais-trimestrais", "PIB trimestral"),
    ("pesquisa-nacional-por-amostra-de-domicilios-continua-trimestral", "PNAD Contínua (trimestral)"),
    ("pesquisa-nacional-por-amostra-de-domicilios-continua-mensal", "PNAD Contínua"),
    ("pnad-continua-trimestral", "PNAD Contínua (trimestral)"),
    ("pesquisa-industrial-mensal-producao-fisica-regional", "Produção industrial regional (PIM)"),
    ("pesquisa-industrial-mensal-producao-fisica-brasil", "Produção industrial (PIM)"),
    ("pesquisa-mensal-de-comercio", "Vendas no varejo (PMC)"),
    ("pesquisa-mensal-de-servicos", "Volume de serviços (PMS)"),
    ("indice-de-precos-ao-produtor", "IPP"),
    ("sistema-nacional-de-pesquisa-de-custos-e-indices-da-construcao-civil", "SINAPI"),
)


class FetchError(RuntimeError):
    pass


def http_get(url: str, timeout: int = 40, attempts: int = 3, browser: bool = False) -> bytes:
    last: Exception | None = None
    agent = BROWSER_UA if browser else POLITE_UA
    for attempt in range(attempts):
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": agent,
                "Accept": "text/calendar, application/json, text/html;q=0.9, */*;q=0.8",
                "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                status = getattr(response, "status", 200)
                data = response.read()
                if status != 200:
                    raise FetchError(f"HTTP {status}")
                return data
        except (urllib.error.URLError, TimeoutError, FetchError, ssl.SSLError) as exc:
            last = exc
            if attempt + 1 < attempts:
                time.sleep(1.2 * (attempt + 1))
    assert last is not None
    raise FetchError(str(last))


def uid(prefix: str, *parts: object) -> str:
    raw = "-".join(str(part) for part in (prefix, *parts))
    slug = re.sub(r"[^a-z0-9]+", "-", raw.lower()).strip("-") or "evento"
    return f"{slug}@{UID_DOMAIN}"


def month_label(year: int, month: int) -> str:
    if 1 <= month <= 12:
        return f"{MONTHS_PT[month]}/{year}"
    return str(year)


def previous_month(day: date) -> tuple[int, int]:
    if day.month == 1:
        return day.year - 1, 12
    return day.year, day.month - 1


def window(today: date) -> tuple[date, date]:
    return date(today.year - 1, 1, 1), date(today.year + 2, 12, 31)


def in_window(day: date, today: date) -> bool:
    start, end = window(today)
    return start <= day <= end


def plain(value: str) -> str:
    value = html.unescape(html.unescape(value or ""))
    value = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def event(
    *,
    uid_value: str,
    tag: str,
    summary: str,
    description: str,
    start: datetime,
    end: datetime | None = None,
    url: str = "",
    all_day: bool = False,
) -> dict:
    if start.tzinfo is None:
        start = start.replace(tzinfo=SAO_PAULO)
    else:
        start = start.astimezone(SAO_PAULO)
    if end is not None:
        if end.tzinfo is None:
            end = end.replace(tzinfo=SAO_PAULO)
        else:
            end = end.astimezone(SAO_PAULO)
    return {
        "uid": uid_value,
        "tag": tag,
        "summary": re.sub(r"\s+", " ", summary).strip(),
        "description": description.strip(),
        "start": start.isoformat(),
        "end": end.isoformat() if end else "",
        "url": url,
        "all_day": all_day,
    }


def _ibge_label(item: dict) -> str:
    alias = (item.get("alias_produto") or "").casefold()
    titulo = plain(item.get("titulo") or item.get("nome_produto") or "")
    for needle, label in IBGE_LABELS:
        if needle in alias:
            return label
    titulo = re.sub(r"\s*#\w+", "", titulo)
    return titulo[:80] or "Divulgação"


def _ibge_reference(item: dict) -> str:
    year_1 = int(item.get("ano_referencia_inicio") or 0)
    month_1 = int(item.get("mes_referencia_inicio") or 0)
    year_2 = int(item.get("ano_referencia_fim") or 0)
    month_2 = int(item.get("mes_referencia_fim") or 0)
    quarters = {1: "1º trimestre", 4: "2º trimestre", 7: "3º trimestre", 10: "4º trimestre"}
    if year_1 and month_1 and year_2 and month_2 and (year_1, month_1) != (year_2, month_2):
        if year_1 == year_2 and month_1 in quarters and month_2 == month_1 + 2:
            return f"{quarters[month_1]}/{year_1}"
        if 1 <= month_1 <= 12 and 1 <= month_2 <= 12:
            return f"{month_label(year_1, month_1)} a {month_label(year_2, month_2)}"
    if year_1 and 1 <= month_1 <= 12:
        return month_label(year_1, month_1)
    if year_1:
        return str(year_1)
    return ""


def _parse_ibge_clock(value: str) -> datetime | None:
    value = (value or "").strip()
    for fmt in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y"):
        try:
            parsed = datetime.strptime(value, fmt)
        except ValueError:
            continue
        # A API publica o instante em UTC. 12:00 UTC é 09:00 em Brasília.
        if fmt == "%d/%m/%Y":
            parsed = parsed.replace(hour=12)
        return parsed.replace(tzinfo=UTC)
    return None


def ibge_items_to_events(items: list[dict], today: date) -> list[dict]:
    events: list[dict] = []
    seen: set[str] = set()
    for item in items:
        stamp = _parse_ibge_clock(str(item.get("data_divulgacao") or ""))
        if stamp is None:
            continue
        local = stamp.astimezone(SAO_PAULO)
        if not in_window(local.date(), today):
            continue
        release_id = item.get("id")
        key = str(release_id or f"{item.get('titulo')}-{stamp.isoformat()}")
        if key in seen:
            continue
        seen.add(key)
        label = _ibge_label(item)
        reference = _ibge_reference(item)
        summary = f"{label} – {reference}" if reference else label
        product = item.get("produto_id") or ""
        source = IBGE_URL if not product else f"{IBGE_URL}{product}"
        link = plain(str(item.get("link") or ""))
        if not link.lower().startswith("http"):
            link = source
        kind = plain(str(item.get("tipo") or "Divulgação"))
        description = (
            f"{kind} do IBGE: {plain(str(item.get('titulo') or label))}."
            + (f"\nReferência: {reference}." if reference else "")
            + "\nHorário de Brasília, convertido do UTC publicado na API de calendário do IBGE "
            "(12:00 UTC corresponde a 09:00 em Brasília)."
            + f"\nFonte: {source}"
        )
        events.append(
            event(
                uid_value=uid("ibge", key),
                tag="IBGE",
                summary=summary,
                description=description,
                start=local,
                end=local + timedelta(minutes=30),
                url=link,
            )
        )
    return events


def fetch_ibge(today: date | None = None) -> list[dict]:
    today = today or datetime.now(SAO_PAULO).date()
    start, end = window(today)
    items: list[dict] = []
    page = 1
    total_pages = 1
    while page <= total_pages:
        query = (
            f"{IBGE_URL}?qtd=100&de={start.isoformat()}&ate={end.isoformat()}&page={page}"
        )
        payload = json.loads(http_get(query, timeout=30, attempts=2).decode("utf-8"))
        if not isinstance(payload, dict):
            raise FetchError("resposta do IBGE sem objeto JSON")
        total_pages = int(payload.get("totalPages") or 1)
        batch = payload.get("items") or []
        if not isinstance(batch, list):
            raise FetchError("lista de divulgações do IBGE inválida")
        items.extend(batch)
        page += 1
        if page > 40:
            break
    events = ibge_items_to_events(items, today)
    if not events:
        raise FetchError("IBGE não devolveu divulgações no período")
    return events


def _fed_clock(value: str) -> tuple[int, int]:
    text = (value or "").strip().lower().replace(".", "")
    match = re.search(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", text)
    if not match:
        return 14, 0
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    meridiem = match.group(3) or ""
    if meridiem == "pm" and hour < 12:
        hour += 12
    if meridiem == "am" and hour == 12:
        hour = 0
    if not meridiem and hour < 8:
        hour += 12
    return hour, minute


def _fed_day(month: str, days: object) -> date | None:
    match = re.fullmatch(r"(\d{4})-(\d{2})", str(month or "").strip())
    if not match:
        return None
    numbers = [int(piece) for piece in re.findall(r"\d+", str(days or ""))]
    if not numbers:
        return None
    try:
        return date(int(match.group(1)), int(match.group(2)), numbers[-1])
    except ValueError:
        return None


def fed_payload_to_events(payload: dict, today: date) -> list[dict]:
    events: list[dict] = []
    seen: set[str] = set()
    for item in payload.get("events") or []:
        title = (item.get("title") or "").strip()
        if title not in {"FOMC Meeting", "FOMC Minutes"}:
            continue
        day = _fed_day(str(item.get("month") or ""), item.get("days"))
        if day is None or not in_window(day, today):
            continue
        hour, minute = _fed_clock(str(item.get("time") or "2:00 p.m."))
        start_et = datetime(day.year, day.month, day.day, hour, minute, tzinfo=NEW_YORK)
        local = start_et.astimezone(SAO_PAULO)
        decision = title == "FOMC Meeting"
        kind = "decisao" if decision else "ata"
        key = f"{kind}-{day.isoformat()}"
        if key in seen:
            continue
        seen.add(key)
        summary = "Decisão de juros (FOMC)" if decision else "Ata do FOMC"
        detail = plain(str(item.get("description") or ""))
        lowered = detail.casefold()
        if decision:
            note = "Decisão de juros no segundo dia da reunião do FOMC."
            if "press conference" in lowered:
                note += " Há coletiva de imprensa."
            if "projection" in lowered:
                note += " Esta reunião inclui as projeções econômicas."
        else:
            note = "Publicação da ata do FOMC."
            if detail:
                note += " " + detail + "."
        clock = local.strftime("%H:%M")
        description = (
            f"{note}\nHorário original: {hour:02d}:{minute:02d} no Leste dos EUA "
            f"(America/New_York), {clock} em Brasília. "
            "O horário de verão dos EUA entra nessa conversão."
            f"\nFonte: {FED_PAGE}"
        )
        events.append(
            event(
                uid_value=uid("fed", key),
                tag="Fed",
                summary=summary,
                description=description,
                start=local,
                end=local + timedelta(minutes=30),
                url=FED_PAGE,
            )
        )
    return events


def fetch_fed(today: date | None = None) -> list[dict]:
    today = today or datetime.now(SAO_PAULO).date()
    raw = http_get(FED_URL, timeout=40, attempts=3)
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    payload = json.loads(raw.decode("utf-8"))
    events = fed_payload_to_events(payload, today)
    if not events:
        raise FetchError("calendário do Fed sem reuniões do FOMC")
    return events


class _Rows(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag == "tr":
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"td", "th"} and self._cell is not None and self._row is not None:
            self._row.append(re.sub(r"\s+", " ", "".join(self._cell)).strip())
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if any(self._row):
                self.rows.append(self._row)
            self._row = None

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)


def _english_date(value: str) -> date | None:
    cleaned = value.replace(".", " ").replace(",", " ")
    parts = [part for part in cleaned.split() if part]
    if len(parts) < 3:
        return None
    month = MONTHS_EN.get(parts[0].casefold())
    if month is None:
        return None
    try:
        return date(int(parts[2]), month, int(parts[1]))
    except ValueError:
        return None


def _english_month(value: str) -> tuple[int, int] | None:
    parts = [part for part in value.replace(".", " ").split() if part]
    if len(parts) < 2:
        return None
    month = MONTHS_EN.get(parts[0].casefold())
    if month is None:
        return None
    try:
        return int(parts[-1]), month
    except ValueError:
        return None


def _english_time(value: str) -> tuple[int, int] | None:
    match = re.search(r"(\d{1,2}):(\d{2})\s*(AM|PM)?", value, flags=re.IGNORECASE)
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2))
    meridiem = (match.group(3) or "").upper()
    if meridiem == "PM" and hour < 12:
        hour += 12
    if meridiem == "AM" and hour == 12:
        hour = 0
    return hour, minute


def parse_bls_schedule_html(html: str, kind: str, today: date) -> list[dict]:
    parser = _Rows()
    parser.feed(html)
    parser.close()
    if kind == "cpi":
        label = "CPI dos EUA"
        page = BLS_CPI_URL
    else:
        label = "Payroll dos EUA"
        page = BLS_EMP_URL
    events: list[dict] = []
    for row in parser.rows:
        if len(row) < 3:
            continue
        reference = _english_month(row[0])
        release = _english_date(row[1])
        clock = _english_time(row[2])
        if reference is None or release is None or clock is None:
            continue
        if not in_window(release, today):
            continue
        start_et = datetime(release.year, release.month, release.day, clock[0], clock[1], tzinfo=NEW_YORK)
        local = start_et.astimezone(SAO_PAULO)
        ref_year, ref_month = reference
        summary = f"{label} – {month_label(ref_year, ref_month)}"
        description = (
            f"{label}, mês de referência {month_label(ref_year, ref_month)}."
            f"\nHorário original: {clock[0]:02d}:{clock[1]:02d} no Leste dos EUA, "
            f"{local.strftime('%H:%M')} em Brasília."
            f"\nFonte: {page}"
        )
        events.append(
            event(
                uid_value=uid("bls", kind, release.isoformat()),
                tag="BLS",
                summary=summary,
                description=description,
                start=local,
                end=local + timedelta(minutes=30),
                url=page,
            )
        )
    return events


def _unfold_ics(text: str) -> list[str]:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    logical: list[str] = []
    for line in text.split("\n"):
        if not line:
            continue
        if line[0] in " \t" and logical:
            logical[-1] += line[1:]
        else:
            logical.append(line)
    return logical


def parse_bls_ics(text: str, today: date) -> list[dict]:
    events: list[dict] = []
    current: dict[str, str] | None = None
    for line in _unfold_ics(text):
        upper = line.upper()
        if upper == "BEGIN:VEVENT":
            current = {}
            continue
        if upper == "END:VEVENT":
            if current:
                built = _bls_ics_event(current, today)
                if built is not None:
                    events.append(built)
            current = None
            continue
        if current is None or ":" not in line:
            continue
        name, value = line.split(":", 1)
        current[name.split(";", 1)[0].upper()] = value
    return events


def _bls_ics_event(props: dict[str, str], today: date) -> dict | None:
    summary = props.get("SUMMARY", "").replace("\\,", ",").strip()
    if summary == "Consumer Price Index":
        kind, label, page = "cpi", "CPI dos EUA", BLS_CPI_URL
    elif summary == "Employment Situation":
        kind, label, page = "empsit", "Payroll dos EUA", BLS_EMP_URL
    else:
        return None
    match = re.search(r"(\d{8})T(\d{6})", props.get("DTSTART", ""))
    if not match:
        return None
    try:
        naive = datetime.strptime(match.group(1) + match.group(2), "%Y%m%d%H%M%S")
    except ValueError:
        return None
    local = naive.replace(tzinfo=NEW_YORK).astimezone(SAO_PAULO)
    if not in_window(local.date(), today):
        return None
    ref_year, ref_month = previous_month(local.date())
    description = (
        f"{label}. O mês de referência ({month_label(ref_year, ref_month)}) "
        "foi calculado como o mês anterior à divulgação, porque o .ics do BLS "
        "não traz essa informação."
        f"\nHorário original no Leste dos EUA, {local.strftime('%H:%M')} em Brasília."
        f"\nFonte: {BLS_ICS_URL}"
    )
    return event(
        uid_value=uid("bls", "cpi" if kind == "cpi" else "empsit", local.date().isoformat()),
        tag="BLS",
        summary=f"{label} – {month_label(ref_year, ref_month)}",
        description=description,
        start=local,
        end=local + timedelta(minutes=30),
        url=page,
    )


def merge_bls(primary: list[dict], extra: list[dict]) -> list[dict]:
    merged = {item["uid"]: item for item in extra}
    for item in primary:
        merged[item["uid"]] = item
    return list(merged.values())


def fetch_bls(today: date | None = None) -> list[dict]:
    today = today or datetime.now(SAO_PAULO).date()
    errors: list[str] = []
    html_events: list[dict] = []
    try:
        cpi_html = http_get(BLS_CPI_URL, timeout=40, attempts=2, browser=True).decode("utf-8", "replace")
        emp_html = http_get(BLS_EMP_URL, timeout=40, attempts=2, browser=True).decode("utf-8", "replace")
        html_events = parse_bls_schedule_html(cpi_html, "cpi", today) + parse_bls_schedule_html(
            emp_html, "empsit", today
        )
    except (FetchError, UnicodeError, ValueError) as exc:
        errors.append(f"páginas: {exc}")
    ics_events: list[dict] = []
    try:
        ics = http_get(BLS_ICS_URL, timeout=40, attempts=2, browser=True).decode("utf-8", "replace")
        if "BEGIN:VCALENDAR" not in ics.upper():
            raise FetchError("resposta sem VCALENDAR")
        ics_events = parse_bls_ics(ics, today)
    except (FetchError, UnicodeError, ValueError) as exc:
        errors.append(f"ics: {exc}")
    events = merge_bls(html_events, ics_events)
    if not events:
        detail = "; ".join(errors) or "sem eventos"
        raise FetchError(detail)
    return events


def easter(year: int) -> date:
    """Domingo de Páscoa, calendário gregoriano."""
    century, year_mod = divmod(year, 100)
    epact_century = century // 4
    century_mod = century % 4
    lunar_correction = (century + 8) // 25
    century_correction = (century - lunar_correction + 1) // 3
    paschal = (19 * (year % 19) + century - epact_century - century_correction + 15) % 30
    weekday_century = year_mod // 4
    weekday_mod = year_mod % 4
    sunday = (32 + 2 * century_mod + 2 * weekday_century - paschal - weekday_mod) % 7
    moon = (year % 19 + 11 * paschal + 22 * sunday) // 451
    month = (paschal + sunday - 7 * moon + 114) // 31
    day = (paschal + sunday - 7 * moon + 114) % 31 + 1
    return date(year, month, day)


def brazil_holidays(year: int) -> set[date]:
    sunday = easter(year)
    carnival_tuesday = sunday - timedelta(days=47)
    holidays = {
        date(year, 1, 1),
        date(year, 4, 21),
        date(year, 5, 1),
        date(year, 9, 7),
        date(year, 10, 12),
        date(year, 11, 2),
        date(year, 11, 15),
        date(year, 12, 25),
        carnival_tuesday - timedelta(days=1),
        carnival_tuesday,
        sunday - timedelta(days=2),
        sunday + timedelta(days=60),
    }
    return holidays


def nth_business_day(year: int, month: int, index: int) -> date:
    holidays = brazil_holidays(year)
    cursor = date(year, month, 1)
    found = 0
    while True:
        if cursor.weekday() < 5 and cursor not in holidays:
            found += 1
            if found == index:
                return cursor
        cursor += timedelta(days=1)


def _month_add(year: int, month: int, offset: int) -> tuple[int, int]:
    number = year * 12 + (month - 1) + offset
    return number // 12, number % 12 + 1


class _PmiList(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.entries: list[tuple[str, str, str, str]] = []
        self._year = ""
        self._day = ""
        self._clock = ""
        self._bucket: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        classes = " ".join(value or "" for key, value in attrs if key.lower() == "class")
        if "listHeading" in classes:
            self._bucket = "year"
            self._text = []
        elif "listSubHeading" in classes:
            self._bucket = "day"
            self._text = []
        elif "releaseTitle" in classes:
            self._bucket = "title"
            self._text = []
        elif "releaseDate" in classes:
            self._bucket = "time"
            self._text = []

    def handle_endtag(self, tag: str) -> None:
        if self._bucket is None or tag.lower() not in {"span", "div"}:
            return
        text = re.sub(r"\s+", " ", "".join(self._text)).strip()
        if self._bucket == "year" and re.fullmatch(r"20\d{2}", text):
            self._year = text
        elif self._bucket == "day" and text:
            self._day = text
        elif self._bucket == "time" and text:
            self._clock = text
        elif self._bucket == "title" and text and self._year and self._day:
            self.entries.append((self._year, self._day, self._clock, text))
        self._bucket = None
        self._text = []

    def handle_data(self, data: str) -> None:
        if self._bucket is not None:
            self._text.append(data)


def parse_pmi_html(html: str, today: date) -> list[dict]:
    parser = _PmiList()
    parser.feed(html)
    parser.close()
    events: list[dict] = []
    seen: set[str] = set()
    for year_text, day_text, clock, title in parser.entries:
        lowered = title.casefold()
        if "brazil" not in lowered or "pmi" not in lowered or "flash" in lowered:
            continue
        if "manufacturing" in lowered:
            kind = "industrial"
            label = "PMI Industrial Brasil"
        elif "services" in lowered:
            kind = "servicos"
            label = "PMI de Serviços Brasil"
        else:
            continue
        release = _pmi_date(year_text, day_text)
        if release is None or not in_window(release, today):
            continue
        hour, minute = _utc_clock(clock)
        start = datetime(release.year, release.month, release.day, hour, minute, tzinfo=UTC)
        local = start.astimezone(SAO_PAULO)
        ref_year, ref_month = previous_month(release)
        key = f"{kind}-{ref_year}-{ref_month:02d}"
        if key in seen:
            continue
        seen.add(key)
        events.append(
            _pmi_event(
                key=key,
                label=label,
                ref_year=ref_year,
                ref_month=ref_month,
                local=local,
                estimated=False,
            )
        )
    return events


def _pmi_date(year_text: str, day_text: str) -> date | None:
    try:
        year = int(year_text)
    except ValueError:
        return None
    parts = day_text.replace(",", " ").split()
    if len(parts) < 2:
        return None
    month = MONTHS_EN.get(parts[0].casefold())
    if month is None:
        return None
    try:
        return date(year, month, int(parts[1]))
    except ValueError:
        return None


def _utc_clock(value: str) -> tuple[int, int]:
    match = re.search(r"(\d{1,2}):(\d{2})", value or "")
    if not match:
        return 13, 0
    return int(match.group(1)), int(match.group(2))


def _pmi_event(
    *,
    key: str,
    label: str,
    ref_year: int,
    ref_month: int,
    local: datetime,
    estimated: bool,
) -> dict:
    reference = month_label(ref_year, ref_month)
    if estimated:
        description = (
            f"{label}, referência {reference}. "
            "Data estimada pela regra publicada pela S&P Global: "
            "PMI industrial no 1º dia útil do mês e PMI de serviços no 3º dia útil, "
            "por volta das 10:00 em Brasília (13:00 UTC). "
            "O calendário oficial não listou este mês. "
            "A conta de dia útil pula fim de semana e feriado nacional "
            "(inclui carnaval, sexta-feira santa e Corpus Christi); "
            "a S&P pode não observar algum desses feriados."
            f"\nFonte da regra: {PMI_URL}"
        )
    else:
        description = (
            f"{label}, referência {reference}. "
            f"Horário oficial convertido de UTC para Brasília ({local.strftime('%H:%M')})."
            f"\nFonte: {PMI_URL}"
        )
    return event(
        uid_value=uid("pmi", key),
        tag="PMI",
        summary=f"{label} – {reference}",
        description=description,
        start=local,
        end=local + timedelta(minutes=30),
        url=PMI_URL,
    )


def pmi_rule_events(today: date, official: list[dict]) -> list[dict]:
    covered = {item["uid"] for item in official}
    events: list[dict] = []
    start_year, start_month = _month_add(today.year, today.month, -1)
    for offset in range(16):
        year, month = _month_add(start_year, start_month, offset)
        ref_year, ref_month = previous_month(date(year, month, 1))
        for index, kind, label in (
            (1, "industrial", "PMI Industrial Brasil"),
            (3, "servicos", "PMI de Serviços Brasil"),
        ):
            key = f"{kind}-{ref_year}-{ref_month:02d}"
            release_uid = uid("pmi", key)
            if release_uid in covered:
                continue
            release = nth_business_day(year, month, index)
            if not in_window(release, today):
                continue
            local = datetime(release.year, release.month, release.day, 10, 0, tzinfo=SAO_PAULO)
            events.append(
                _pmi_event(
                    key=key,
                    label=label,
                    ref_year=ref_year,
                    ref_month=ref_month,
                    local=local,
                    estimated=True,
                )
            )
    return events


def fetch_pmi(today: date | None = None) -> list[dict]:
    today = today or datetime.now(SAO_PAULO).date()
    official: list[dict] = []
    try:
        html = http_get(PMI_URL, timeout=40, attempts=2, browser=True).decode("utf-8", "replace")
        official = parse_pmi_html(html, today)
        if not official:
            log_source("PMI: a página oficial abriu, mas não trouxe PMI Brasil. Os meses seguem a regra.")
    except (FetchError, UnicodeError, ValueError) as exc:
        official = []
        log_source(f"PMI: calendário oficial indisponível ({exc}). Os meses seguem a regra.")
    events = official + pmi_rule_events(today, official)
    if not events:
        raise FetchError("não foi possível montar o calendário de PMI")
    return events


def log_source(message: str) -> None:
    print(message, file=sys.stderr)
