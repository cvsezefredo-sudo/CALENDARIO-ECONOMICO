#!/usr/bin/env python3
"""Monta o calendário econômico único: BCB, IBGE, PMI, Fed e BLS.

O Banco Central continua vindo dos feeds ICS oficiais. As outras fontes
entram pelo módulo fontes_extras. Uma fonte que falha não apaga as demais:
o script reaproveita o cache da última coleta boa. O arquivo publicado só
é trocado quando o resultado é um VCALENDAR válido e não vazio.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from zoneinfo import ZoneInfo

import fontes_extras

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "calendario-bcb.ics"
INDEX_URL = (
    "https://www.bcb.gov.br/api/paginasite/sitebcb/acessoinformacao/calendariobc_ics"
)
EVENTOS_URL = "https://www.bcb.gov.br/api/exportarics/sitebcb/eventosics"
AGENDA_URL = "https://www.bcb.gov.br/api/exportarics/sitebcb/agendaics"
CALNAME = "Calendário Econômico"
CALDESC = (
    "Agenda econômica centralizada: Banco Central, IBGE, PMI Brasil, "
    "FOMC e divulgações do BLS, no horário de Brasília"
)
TZID = "America/Sao_Paulo"
SAO_PAULO = ZoneInfo(TZID)
UTC = ZoneInfo("UTC")

FALLBACK_NAMES = (
    "Reuniões do Copom",
    "Atas e Comunicados do Copom",
    "Focus",
    "Estatísticas macroeconômicas",
    "Estatísticas fiscais",
    "Estatísticas monetárias e de crédito",
    "Estatísticas do setor externo",
    "Estatísticas do mercado aberto",
    "Estatísticas do Valores a Receber",
    "Índice de atividade econômica (IBC-Br)",
    "Índice de Atividade Econômica Regional (IBCR)",
    "Índice de Commodities \u2013 Brasil (IC-Br)",
    "Relatório de Política Monetária",
    "Relatório de Estabilidade Financeira",
    "Relatório de Investimento Direto",
    "Boletim Regional",
    "Capitais Internacionais",
    "Reuniões do CMN e COMOC",
    "Reuniões do Comef",
    "Atas e Comunicados do Comef",
    "Reuniões do Coremec",
    "Reuniões do GRC",
    "Indeco",
    "Ranking de Reclamações",
    "Sondagens do BC",
    "Sondagens - PTC PEF",
)

# Etiquetas curtas, no estilo [Copom], [Focus], [Fiscais].
TAG_BY_NAME = {
    "eventos": "Eventos",
    "reuniões do copom": "Copom",
    "atas e comunicados do copom": "Copom",
    "focus": "Focus",
    "estatísticas macroeconômicas": "Macro",
    "estatísticas fiscais": "Fiscais",
    "estatísticas monetárias e de crédito": "Monetárias",
    "estatísticas do setor externo": "Externo",
    "estatísticas do mercado aberto": "Aberto",
    "estatísticas do valores a receber": "Receber",
    "índice de atividade econômica (ibc-br)": "IBC-Br",
    "índice de atividade econômica regional (ibcr)": "IBCR",
    "índice de commodities - brasil (ic-br)": "IC-Br",
    "relatório de política monetária": "Pol. Monetária",
    "relatório de estabilidade financeira": "Estabilidade",
    "relatório de investimento direto": "Investimento",
    "boletim regional": "Regional",
    "capitais internacionais": "Capitais",
    "reuniões do cmn e comoc": "CMN",
    "reuniões do comef": "Comef",
    "atas e comunicados do comef": "Comef",
    "reuniões do coremec": "Coremec",
    "reuniões do grc": "GRC",
    "indeco": "Indeco",
    "ranking de reclamações": "Ranking",
    "sondagens do bc": "Sondagens",
    "sondagens - ptc pef": "PTC",
}

TEXT_PROPS = {
    "SUMMARY",
    "DESCRIPTION",
    "LOCATION",
    "COMMENT",
    "UID",
    "CONTACT",
    "RELATED-TO",
    "RESOURCES",
    "CATEGORIES",
}

USER_AGENT = (
    "calendario-economico-bcb/1.0 "
    "(+https://github.com/cvsezefredo-sudo/CALENDARIO-ECONOMICO)"
)
FEED_RE = re.compile(
    r"https://www\.bcb\.gov\.br/api/exportarics/sitebcb/"
    r"(?:eventosics|agendaics\?lista=[^'\"\\\s<>]+)"
)
TAG_RE = re.compile(r"<\s*/?\s*[a-zA-Z][^>]*>")
URL_RE = re.compile(r"https?://[^\s<>\"']+")
DT_RE = re.compile(r"^\d{8}T\d{6}Z?$")
DATE_RE = re.compile(r"^\d{8}$")

VTIMEZONE = "\r\n".join(
    [
        "BEGIN:VTIMEZONE",
        f"TZID:{TZID}",
        f"X-LIC-LOCATION:{TZID}",
        "BEGIN:STANDARD",
        "DTSTART:19900101T000000",
        "TZNAME:BRT",
        "TZOFFSETFROM:-0300",
        "TZOFFSETTO:-0300",
        "END:STANDARD",
        "END:VTIMEZONE",
    ]
)


@dataclass
class Prop:
    name: str
    params: str
    value: str


@dataclass
class Event:
    props: list[Prop]
    tag: str
    uid: str
    sort_key: tuple[str, str, str]
    dropped_dtend: bool = False


@dataclass
class FeedResult:
    url: str
    name: str
    tag: str
    events: list[Event] = field(default_factory=list)
    error: str = ""


class _HTMLText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.links: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in {"script", "style"}:
            self._skip += 1
            return
        if tag == "br":
            self.parts.append("\n")
        elif tag in {"p", "div", "tr", "h1", "h2", "h3", "h4", "ul", "ol", "li", "table"}:
            self.parts.append("\n")
        if tag == "li":
            self.parts.append("• ")
        if tag == "a":
            for key, val in attrs:
                if key.lower() == "href" and val:
                    self.links.append(html.unescape(val.strip()))

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"script", "style"} and self._skip:
            self._skip -= 1
            return
        if tag in {"p", "div", "li", "tr", "h1", "h2", "h3", "h4"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        self.parts.append(data)


def log(message: str) -> None:
    print(message, file=sys.stderr)


def norm_name(name: str) -> str:
    name = name.replace("\u2013", "-").replace("\u2014", "-").replace("\u2212", "-")
    return " ".join(name.casefold().split())


def tag_for(name: str) -> str:
    key = norm_name(name)
    if key in TAG_BY_NAME:
        return TAG_BY_NAME[key]
    match = re.search(r"\(([^)]{2,20})\)\s*$", name)
    if match:
        return sanitize_tag(match.group(1))
    cleaned = re.sub(
        r"^(reuniões do|atas e comunicados do|relatório de|estatísticas do|estatísticas)\s+",
        "",
        name.strip(),
        count=1,
        flags=re.IGNORECASE,
    )
    return sanitize_tag(cleaned or name or "BCB")


def sanitize_tag(tag: str) -> str:
    tag = re.sub(r"\s+", " ", tag).strip(" -–—")
    tag = tag.replace("[", "").replace("]", "")
    return tag[:32] or "BCB"


def agenda_url(name: str) -> str:
    query = urllib.parse.urlencode(
        [("lista", name)], quote_via=urllib.parse.quote
    )
    return f"{AGENDA_URL}?{query}"


def fallback_urls() -> list[str]:
    return [EVENTOS_URL, *[agenda_url(name) for name in FALLBACK_NAMES]]


def normalize_feed_url(url: str) -> str:
    url = html.unescape(url.strip())
    parts = urllib.parse.urlsplit(url)
    pairs = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    query = urllib.parse.urlencode(pairs, quote_via=urllib.parse.quote)
    return urllib.parse.urlunsplit(
        (parts.scheme, parts.netloc, parts.path, query, "")
    )


def calendar_name(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    if parts.path.rstrip("/").endswith("eventosics"):
        return "Eventos"
    lista = ""
    for key, value in urllib.parse.parse_qsl(parts.query):
        if key == "lista":
            lista = value.strip()
    return lista or "Calendário BCB"


def extract_feed_urls(payload: str) -> list[str]:
    text = payload
    stripped = payload.lstrip()
    if stripped.startswith("{"):
        try:
            body = json.loads(payload)
        except json.JSONDecodeError:
            body = None
        if isinstance(body, dict) and isinstance(body.get("conteudo"), str):
            text = body["conteudo"]
    found: list[str] = []
    seen: set[str] = set()
    for match in FEED_RE.findall(text):
        url = normalize_feed_url(match)
        if url not in seen:
            seen.add(url)
            found.append(url)
    return found


def http_get(url: str, timeout: int = 40, attempts: int = 3) -> bytes:
    last: Exception | None = None
    for attempt in range(attempts):
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/calendar, application/json, text/plain;q=0.9, */*;q=0.8",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                status = getattr(response, "status", 200)
                data = response.read()
                if status != 200:
                    raise RuntimeError(f"HTTP {status}")
                return data
        except (urllib.error.URLError, TimeoutError, RuntimeError) as exc:
            last = exc
            if attempt + 1 < attempts:
                time.sleep(1.5 * (attempt + 1))
    assert last is not None
    raise last


def load_feed_urls() -> list[str]:
    try:
        payload = http_get(INDEX_URL, timeout=40).decode("utf-8", "replace")
        urls = extract_feed_urls(payload)
    except (urllib.error.URLError, TimeoutError, RuntimeError, UnicodeError) as exc:
        log(f"Lista oficial indisponível ({exc}). Usando a lista fixa.")
        return fallback_urls()
    if len(urls) < 10:
        log(
            f"Lista oficial trouxe {len(urls)} links. Usando a lista fixa."
        )
        return fallback_urls()
    log(f"Lista oficial: {len(urls)} calendários.")
    return urls


def unfold(text: str) -> list[str]:
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


def split_content_line(line: str) -> tuple[str, str, str]:
    quoted = False
    for index, char in enumerate(line):
        if char == '"':
            quoted = not quoted
        elif char == ":" and not quoted:
            left, value = line[:index], line[index + 1 :]
            break
    else:
        raise ValueError("linha sem dois-pontos")
    if ";" in left:
        name, params = left.split(";", 1)
        params = ";" + params
    else:
        name, params = left, ""
    return name.upper(), params, value


def unescape_text(value: str) -> str:
    output: list[str] = []
    index = 0
    while index < len(value):
        if value[index] == "\\" and index + 1 < len(value):
            nxt = value[index + 1]
            mapping = {"n": "\n", "N": "\n", "\\": "\\", ";": ";", ",": ","}
            output.append(mapping.get(nxt, nxt))
            index += 2
            continue
        output.append(value[index])
        index += 1
    return "".join(output)


def escape_text(value: str) -> str:
    value = value.replace("\\", "\\\\")
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = value.replace("\n", "\\n")
    value = value.replace(";", "\\;")
    value = value.replace(",", "\\,")
    return value


def fold(line: str) -> str:
    data = line.encode("utf-8")
    if len(data) <= 75:
        return line
    parts: list[str] = []
    limit = 75
    index = 0
    while index < len(data):
        end = min(index + limit, len(data))
        if end < len(data):
            while end > index and (data[end] & 0xC0) == 0x80:
                end -= 1
            if end == index:
                end = min(index + limit, len(data))
        parts.append(data[index:end].decode("utf-8"))
        index = end
        limit = 74
    return "\r\n ".join(parts)


def tidy(text: str) -> str:
    text = text.replace("\u00a0", " ").replace("\u200b", "").replace("\ufeff", "")
    text = text.replace("\u200c", "").replace("\u200d", "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    lines = [line.strip() for line in text.split("\n")]
    collapsed: list[str] = []
    blank = False
    for line in lines:
        if not line:
            if collapsed and not blank:
                collapsed.append("")
            blank = True
            continue
        collapsed.append(line)
        blank = False
    return "\n".join(collapsed).strip()


def html_to_text(value: str) -> tuple[str, list[str]]:
    parser = _HTMLText()
    parser.feed(value)
    parser.close()
    return tidy(html.unescape("".join(parser.parts))), parser.links


def plain_text(value: str) -> tuple[str, list[str]]:
    links = URL_RE.findall(value)
    if TAG_RE.search(value):
        text, html_links = html_to_text(value)
        return text, html_links + links
    if "&" in value:
        value = html.unescape(html.unescape(value))
        links = URL_RE.findall(value) or links
    return tidy(value), links


def clean_links(links: list[str]) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for link in links:
        link = html.unescape(link).strip().rstrip(".,;)")
        if not link.lower().startswith(("http://", "https://")):
            continue
        if any(char.isspace() for char in link):
            continue
        if link in seen:
            continue
        seen.add(link)
        cleaned.append(link)
    return cleaned


def prefix_summary(summary: str, tag: str) -> str:
    summary = re.sub(r"\s+", " ", summary).strip()
    prefix = f"[{tag}]"
    if summary == prefix or summary.startswith(prefix + " "):
        return summary
    if not summary:
        return prefix
    return f"{prefix} {summary}"


def normalize_dtstamp(value: str) -> str:
    value = value.strip()
    if re.fullmatch(r"\d{8}T\d{6}Z", value):
        return value
    if re.fullmatch(r"\d{8}T\d{6}", value):
        local = datetime.strptime(value, "%Y%m%dT%H%M%S").replace(tzinfo=SAO_PAULO)
        return local.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    return value


def dt_parts(params: str, value: str) -> tuple[str, str, str] | None:
    value = value.strip()
    upper = params.upper()
    if "VALUE=DATE" in upper or DATE_RE.fullmatch(value):
        if DATE_RE.fullmatch(value):
            return ("d", value, "")
        return None
    match = DT_RE.fullmatch(value)
    if not match:
        return None
    return ("t", value[:8], value[9:15])


def end_before_start(
    start: tuple[str, str, str] | None, end: tuple[str, str, str] | None
) -> bool:
    if start is None or end is None:
        return False
    if (end[1], end[2]) < (start[1], start[2]):
        return True
    return False


MIN_YEAR = 1990
MAX_YEAR = 2100
CANON_DT_RE = re.compile(r"^\d{8}T\d{6}(?:Z|[+-]\d{4})?$")
SLASH_DT_RE = re.compile(
    r"^(\d{1,2})/(\d{1,2})/(\d{4})(?:[ T](\d{2}):(\d{2})(?::(\d{2}))?)?$"
)
ISO_DT_RE = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2}))?)?(Z)?$"
)


def _real_date(year: int, month: int, day: int) -> bool:
    try:
        date(year, month, day)
    except ValueError:
        return False
    return MIN_YEAR <= year <= MAX_YEAR


def _write_when(prop: Prop, year: int, month: int, day: int, clock: tuple[int, int, int] | None) -> None:
    if clock is None:
        prop.params = ";VALUE=DATE"
        prop.value = f"{year:04d}{month:02d}{day:02d}"
        return
    hour, minute, second = clock
    prop.params = f";TZID={TZID}"
    prop.value = f"{year:04d}{month:02d}{day:02d}T{hour:02d}{minute:02d}{second:02d}"


def canonicalize_when(prop: Prop) -> bool:
    """Garante um DTSTART/DTEND que o calendário consegue ler.

    O feed do BCB às vezes traz data nula como 19700101 (epoch do Unix) ou
    deixa a data em texto dd/mm/aaaa. O app mostra isso como 1º de janeiro
    de 1970. Datas anteriores a 1990 são recusadas. Texto com data válida é
    reescrito no formato iCal. Valor que já está canônico e é uma data real
    permanece byte a byte.
    """
    value = prop.value.strip()
    if CANON_DT_RE.fullmatch(value):
        return _real_date(int(value[0:4]), int(value[4:6]), int(value[6:8]))
    if DATE_RE.fullmatch(value):
        if not _real_date(int(value[0:4]), int(value[4:6]), int(value[6:8])):
            return False
        if "VALUE=DATE" not in prop.params.upper():
            prop.params = ";VALUE=DATE"
        return True
    slash = SLASH_DT_RE.fullmatch(value)
    if slash:
        day, month, year = int(slash.group(1)), int(slash.group(2)), int(slash.group(3))
        if not _real_date(year, month, day):
            return False
        clock = None
        if slash.group(4) is not None:
            clock = (int(slash.group(4)), int(slash.group(5)), int(slash.group(6) or 0))
        _write_when(prop, year, month, day, clock)
        return True
    iso = ISO_DT_RE.fullmatch(value)
    if iso:
        year, month, day = int(iso.group(1)), int(iso.group(2)), int(iso.group(3))
        if not _real_date(year, month, day):
            return False
        if iso.group(4) is None:
            _write_when(prop, year, month, day, None)
            return True
        hour, minute, second = int(iso.group(4)), int(iso.group(5)), int(iso.group(6) or 0)
        if iso.group(7):
            local = datetime(year, month, day, hour, minute, second, tzinfo=UTC).astimezone(SAO_PAULO)
            _write_when(prop, local.year, local.month, local.day, (local.hour, local.minute, local.second))
        else:
            _write_when(prop, year, month, day, (hour, minute, second))
        return True
    return False


def prop_map(props: list[Prop]) -> dict[str, Prop]:
    mapped: dict[str, Prop] = {}
    for prop in props:
        mapped.setdefault(prop.name, prop)
    return mapped


def parse_events(text: str, tag: str) -> list[Event]:
    events: list[Event] = []
    current: list[Prop] | None = None
    for line in unfold(text):
        upper = line.upper()
        if upper == "BEGIN:VEVENT":
            current = []
            continue
        if upper == "END:VEVENT":
            if current:
                event = build_event(current, tag)
                if event is not None:
                    events.append(event)
            current = None
            continue
        if current is None or upper.startswith("BEGIN:") or upper.startswith("END:"):
            continue
        try:
            name, params, raw_value = split_content_line(line)
        except ValueError:
            continue
        value = unescape_text(raw_value) if name in TEXT_PROPS else raw_value
        current.append(Prop(name, params, value))
    return events


def build_event(props: list[Prop], tag: str) -> Event | None:
    by_name = prop_map(props)
    uid_prop = by_name.get("UID")
    start_prop = by_name.get("DTSTART")
    if uid_prop is None or start_prop is None:
        return None
    uid = re.sub(r"\s+", "", uid_prop.value).strip()
    if not uid or not start_prop.value.strip():
        return None
    uid_prop.value = uid

    summary_prop = by_name.get("SUMMARY")
    summary = summary_prop.value if summary_prop else ""
    summary = prefix_summary(plain_text(summary)[0].replace("\n", " "), tag)
    if summary_prop is None:
        props.append(Prop("SUMMARY", "", summary))
    else:
        summary_prop.value = summary

    links: list[str] = []
    description_prop = by_name.get("DESCRIPTION")
    if description_prop is not None:
        text, found = plain_text(description_prop.value)
        description_prop.value = text
        links.extend(found)
    location_prop = by_name.get("LOCATION")
    if location_prop is not None:
        text, found = plain_text(location_prop.value)
        location_prop.value = re.sub(r"\s+", " ", text).strip()
        links.extend(found)
        if not location_prop.value:
            props = [prop for prop in props if prop.name != "LOCATION"]

    url_prop = by_name.get("URL")
    links = clean_links(links)
    if url_prop is not None:
        url_prop.value = url_prop.value.strip()
        if not url_prop.value.lower().startswith(("http://", "https://")):
            props = [prop for prop in props if prop is not url_prop]
            url_prop = None
    if url_prop is None and links:
        props.append(Prop("URL", "", links[0]))
    if description_prop is not None and links:
        extra = [link for link in links if link not in description_prop.value]
        if extra:
            description_prop.value = (
                description_prop.value.rstrip() + "\n" + "\n".join(extra)
            ).strip()

    stamp = by_name.get("DTSTAMP")
    if stamp is not None:
        stamp.value = normalize_dtstamp(stamp.value)
        stamp.params = ""

    if not canonicalize_when(start_prop):
        log(f"Evento descartado, data inválida ({start_prop.value!r}): {uid}")
        return None
    end_prop = by_name.get("DTEND")
    if end_prop is not None and not canonicalize_when(end_prop):
        props = [prop for prop in props if prop.name != "DTEND"]
        end_prop = None
    start = dt_parts(start_prop.params, start_prop.value)
    dropped = False
    if end_prop is not None and end_before_start(
        start, dt_parts(end_prop.params, end_prop.value)
    ):
        props = [prop for prop in props if prop.name != "DTEND"]
        dropped = True

    sort = start if start is not None else ("z", "99999999", "999999")
    return Event(props, tag, uid, (sort[0], sort[1], sort[2] + uid), dropped)


def render_prop(prop: Prop) -> str:
    value = escape_text(prop.value) if prop.name in TEXT_PROPS else prop.value
    value = value.replace("\r\n", "").replace("\r", "").replace("\n", "\\n")
    return fold(f"{prop.name}{prop.params}:{value}")


def ordered_props(props: list[Prop]) -> list[Prop]:
    preferred = [
        "UID",
        "DTSTAMP",
        "DTSTART",
        "DTEND",
        "DURATION",
        "SUMMARY",
        "LOCATION",
        "DESCRIPTION",
        "URL",
    ]
    rank = {name: index for index, name in enumerate(preferred)}
    indexed = list(enumerate(props))
    indexed.sort(key=lambda item: (rank.get(item[1].name, len(preferred)), item[0]))
    seen_single = set()
    ordered: list[Prop] = []
    single = {"UID", "DTSTAMP", "DTSTART", "DTEND", "SUMMARY", "LOCATION", "DESCRIPTION", "URL"}
    for _, prop in indexed:
        if prop.name in single:
            if prop.name in seen_single:
                continue
            seen_single.add(prop.name)
        if prop.name in {"DESCRIPTION", "LOCATION", "URL"} and not prop.value.strip():
            continue
        ordered.append(prop)
    return ordered


def render_event(event: Event) -> str:
    lines = ["BEGIN:VEVENT"]
    for prop in ordered_props(event.props):
        lines.append(render_prop(prop))
    lines.append("END:VEVENT")
    return "\r\n".join(lines)


def render_calendar(events: list[Event]) -> bytes:
    header = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//cvsezefredo//Calendario Economico//PT",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        fold("X-WR-CALNAME:" + escape_text(CALNAME)),
        fold("X-WR-CALDESC:" + escape_text(CALDESC)),
        f"X-WR-TIMEZONE:{TZID}",
        "X-PUBLISHED-TTL:PT1H",
        "REFRESH-INTERVAL;VALUE=DURATION:PT1H",
        VTIMEZONE,
    ]
    body = [render_event(event) for event in events]
    text = "\r\n".join(header + body + ["END:VCALENDAR"]) + "\r\n"
    return text.encode("utf-8")


def fetch_feed(index: int, url: str) -> tuple[int, FeedResult]:
    name = calendar_name(url)
    tag = tag_for(name)
    result = FeedResult(url=url, name=name, tag=tag)
    try:
        data = http_get(url)
    except (urllib.error.URLError, TimeoutError, RuntimeError) as exc:
        result.error = str(exc)
        return index, result
    text = data.decode("utf-8", "replace")
    if "BEGIN:VCALENDAR" not in text.upper() or "BEGIN:VEVENT" not in text.upper():
        result.error = "resposta sem VCALENDAR"
        return index, result
    try:
        result.events = parse_events(text, tag)
    except (ValueError, UnicodeError) as exc:
        result.error = str(exc)
        return index, result
    if not result.events:
        result.error = "nenhum evento"
    return index, result


CACHE_DIR = ROOT / "data" / "cache"
DECISION_NOTE = (
    "Decisão de juros do Copom. O calendário do BCB marca este dia à meia-noite, "
    "sem horário. A decisão costuma ser divulgada às 18:30 (horário de Brasília)."
)
MEETING_NOTE = (
    "Dia de reunião do Copom, sem a divulgação da decisão. "
    "A decisão sai no último dia da reunião, em geral às 18:30 (horário de Brasília)."
)
EXTRA_MINIMUM = {"ibge": 20, "fed": 4, "bls": 4, "pmi": 4}


def _prop(event: Event, name: str) -> Prop | None:
    for prop in event.props:
        if prop.name == name:
            return prop
    return None


def _set_only(event: Event, name: str, params: str, value: str) -> None:
    event.props = [prop for prop in event.props if prop.name != name]
    event.props.append(Prop(name, params, value))


def _append_note(event: Event, note: str) -> None:
    prop = _prop(event, "DESCRIPTION")
    if prop is None:
        event.props.append(Prop("DESCRIPTION", "", note))
        return
    if note in prop.value:
        return
    prop.value = (prop.value.rstrip() + "\n" + note).strip()


def _summary_body(summary: str) -> str:
    return re.sub(r"^\[[^\]]+\]\s*", "", summary).strip()


def refresh_sort(event: Event) -> None:
    start = _prop(event, "DTSTART")
    if start is None:
        return
    parts = dt_parts(start.params, start.value)
    if parts is None:
        return
    event.sort_key = (parts[0], parts[1], parts[2] + event.uid)


def fix_copom_decisions(events: list[Event]) -> int:
    """Coloca 18:30 no segundo dia da reunião quando o BCB manda meia-noite.

    O primeiro dia, também à meia-noite, vira compromisso de dia inteiro para
    não disparar alarme à 00:00. Reuniões que já trazem horário ficam como estão.
    """
    meetings: list[tuple[date, Event]] = []
    for event in events:
        if event.tag != "Copom":
            continue
        summary = _prop(event, "SUMMARY")
        start = _prop(event, "DTSTART")
        if summary is None or start is None:
            continue
        if _summary_body(summary.value).casefold() != "reunião do copom":
            continue
        value = start.value.strip()
        if "VALUE=DATE" in start.params.upper() or not re.fullmatch(r"\d{8}T\d{6}", value):
            continue
        if not value.endswith("T000000"):
            continue
        try:
            day = datetime.strptime(value[:8], "%Y%m%d").date()
        except ValueError:
            continue
        meetings.append((day, event))
    if not meetings:
        return 0
    meetings.sort(key=lambda item: (item[0], item[1].uid))
    clusters: list[list[tuple[date, Event]]] = []
    for item in meetings:
        if not clusters or (item[0] - clusters[-1][-1][0]).days > 1:
            clusters.append([item])
        else:
            clusters[-1].append(item)
    fixed = 0
    for cluster in clusters:
        decision_day = max(day for day, _event in cluster)
        for day, event in cluster:
            if day == decision_day:
                stamp = day.strftime("%Y%m%d")
                _set_only(event, "DTSTART", f";TZID={TZID}", stamp + "T183000")
                _set_only(event, "DTEND", f";TZID={TZID}", stamp + "T190000")
                summary = _prop(event, "SUMMARY")
                if summary is not None:
                    summary.value = "[Copom] Decisão de juros (Copom)"
                _append_note(event, DECISION_NOTE)
            else:
                _set_only(event, "DTSTART", ";VALUE=DATE", day.strftime("%Y%m%d"))
                _set_only(
                    event,
                    "DTEND",
                    ";VALUE=DATE",
                    (day + timedelta(days=1)).strftime("%Y%m%d"),
                )
                _append_note(event, MEETING_NOTE)
            refresh_sort(event)
            fixed += 1
    return fixed


def save_cache(name: str, payload: list) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / f"{name}.json"
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def load_cache(name: str) -> list:
    path = CACHE_DIR / f"{name}.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log(f"Cache {name} ilegível ({exc}).")
        return []
    return data if isinstance(data, list) else []


def event_to_cache(event: Event) -> dict:
    return {
        "tag": event.tag,
        "props": [
            {"name": prop.name, "params": prop.params, "value": prop.value}
            for prop in event.props
        ],
    }


def event_from_cache(payload: dict) -> Event | None:
    props = [
        Prop(item["name"], item.get("params", ""), item.get("value", ""))
        for item in payload.get("props") or []
    ]
    return build_event(props, str(payload.get("tag") or "BCB"))


def events_from_items(items: list[dict]) -> list[Event]:
    built: list[Event] = []
    for item in items:
        start = datetime.fromisoformat(item["start"])
        if start.tzinfo is None:
            start = start.replace(tzinfo=SAO_PAULO)
        local = start.astimezone(SAO_PAULO)
        props = [
            Prop("UID", "", item["uid"]),
            Prop("DTSTAMP", "", local.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")),
        ]
        if item.get("all_day"):
            props.append(Prop("DTSTART", ";VALUE=DATE", local.strftime("%Y%m%d")))
        else:
            props.append(Prop("DTSTART", f";TZID={TZID}", local.strftime("%Y%m%dT%H%M%S")))
            if item.get("end"):
                end = datetime.fromisoformat(item["end"])
                if end.tzinfo is None:
                    end = end.replace(tzinfo=SAO_PAULO)
                end = end.astimezone(SAO_PAULO)
                props.append(Prop("DTEND", f";TZID={TZID}", end.strftime("%Y%m%dT%H%M%S")))
        props.append(Prop("SUMMARY", "", item["summary"]))
        if item.get("description"):
            props.append(Prop("DESCRIPTION", "", item["description"]))
        if item.get("url"):
            props.append(Prop("URL", "", item["url"]))
        event = build_event(props, item["tag"])
        if event is not None:
            built.append(event)
    return built


def load_named_source(name: str, title: str, tag: str, fetcher) -> FeedResult:
    result = FeedResult(url=name, name=title, tag=tag)
    try:
        items = fetcher()
    except (fontes_extras.FetchError, TimeoutError, ValueError, OSError, json.JSONDecodeError) as exc:
        items = None
        failure: Exception = exc
    else:
        failure = None
    minimum = EXTRA_MINIMUM.get(name, 1)
    if items is not None and len(items) >= minimum:
        save_cache(name, items)
        result.events = events_from_items(items)
        return result
    reason = str(failure) if failure is not None else f"só {0 if items is None else len(items)} eventos"
    cached = load_cache(name)
    if cached:
        result.events = events_from_items(cached)
        log(
            f"AVISO [{tag}] {title}: {reason}. "
            f"Mantidos {len(result.events)} eventos da coleta anterior."
        )
        return result
    result.error = reason
    return result


def merge(feeds: list[FeedResult]) -> tuple[list[Event], int]:
    merged: list[Event] = []
    seen: set[str] = set()
    duplicates = 0
    for feed in feeds:
        for event in feed.events:
            if event.uid in seen:
                duplicates += 1
                continue
            seen.add(event.uid)
            merged.append(event)
    merged.sort(key=lambda event: event.sort_key)
    return merged, duplicates


def physical_lines(data: bytes) -> list[str]:
    if data.startswith(b"\xef\xbb\xbf"):
        raise ValueError("o arquivo contém BOM")
    if b"\r\n" not in data:
        raise ValueError("o arquivo não usa CRLF")
    stripped = data.replace(b"\r\n", b"")
    if b"\n" in stripped or b"\r" in stripped:
        raise ValueError("há quebra de linha fora do par CRLF")
    if not data.endswith(b"\r\n"):
        raise ValueError("o arquivo não termina com CRLF")
    text = data.decode("utf-8")
    lines = text.split("\r\n")
    lines.pop()
    return lines


def validate(data: bytes) -> int:
    lines = physical_lines(data)
    if not lines or lines[0] != "BEGIN:VCALENDAR":
        raise ValueError("o arquivo não começa com BEGIN:VCALENDAR")
    if lines[-1] != "END:VCALENDAR":
        raise ValueError("o arquivo não termina com END:VCALENDAR")
    for number, line in enumerate(lines, 1):
        if len(line.encode("utf-8")) > 75:
            raise ValueError(f"linha {number} passa de 75 octetos")
    logical = unfold(data.decode("utf-8"))
    required = {
        "VERSION:2.0",
        "X-WR-CALNAME:" + escape_text(CALNAME),
        f"X-WR-TIMEZONE:{TZID}",
        "X-PUBLISHED-TTL:PT1H",
        "REFRESH-INTERVAL;VALUE=DURATION:PT1H",
        f"TZID:{TZID}",
    }
    present = set(logical)
    missing = sorted(item for item in required if item not in present)
    if missing:
        raise ValueError("faltam propriedades: " + ", ".join(missing))
    if logical.count("BEGIN:VTIMEZONE") != 1 or logical.count("END:VTIMEZONE") != 1:
        raise ValueError("o calendário precisa de um único VTIMEZONE")
    if logical.count("BEGIN:VCALENDAR") != logical.count("END:VCALENDAR"):
        raise ValueError("VCALENDAR desbalanceado")
    event_count = 0
    in_event = False
    has_uid = has_start = has_summary = False
    for line in logical:
        if line == "BEGIN:VEVENT":
            if in_event:
                raise ValueError("VEVENT aninhado")
            in_event = True
            has_uid = has_start = has_summary = False
            event_count += 1
            continue
        if line == "END:VEVENT":
            if not in_event or not (has_uid and has_start and has_summary):
                raise ValueError("VEVENT sem UID, DTSTART ou SUMMARY")
            in_event = False
            continue
        if not in_event:
            continue
        name = line.split(":", 1)[0].split(";", 1)[0].upper()
        has_uid = has_uid or name == "UID"
        has_start = has_start or name == "DTSTART"
        if name == "SUMMARY":
            value = unescape_text(line.split(":", 1)[1])
            if not value.startswith("["):
                raise ValueError("SUMMARY sem etiqueta de categoria")
            has_summary = True
    if in_event:
        raise ValueError("VEVENT sem END")
    if event_count < 1:
        raise ValueError("calendário sem eventos")
    return event_count


def _write_atomic(path: Path, data: bytes) -> None:
    temporary = path.with_name(path.name + ".tmp")
    try:
        temporary.write_bytes(data)
        if temporary.read_bytes() != data:
            raise SystemExit("Falha ao gravar o arquivo temporário.")
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def publish(path: Path, data: bytes) -> None:
    count = validate(data)
    if path.exists():
        previous = path.read_bytes()
        if previous == data:
            log(f"Arquivo inalterado ({count} eventos).")
            _publish_alias(path, data)
            return
        previous_count = previous.count(b"BEGIN:VEVENT")
        if previous_count >= 50 and count < max(1, previous_count // 4):
            raise SystemExit(
                "Recusando substituir o calendário bom "
                f"({previous_count} eventos) por um resultado pequeno ({count})."
            )
    _write_atomic(path, data)
    log(f"Arquivo gravado: {path} ({count} eventos).")
    _publish_alias(path, data)


def _publish_alias(path: Path, data: bytes) -> None:
    if path.name != "calendario-bcb.ics":
        return
    alias = path.with_name("calendario-economico.ics")
    if alias.exists() and alias.read_bytes() == data:
        log(f"Cópia inalterada: {alias}")
        return
    _write_atomic(alias, data)
    log(f"Cópia gravada: {alias}")


def build(urls: list[str]) -> tuple[list[Event], list[FeedResult], int]:
    results: list[FeedResult | None] = [None] * len(urls)
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(fetch_feed, index, url) for index, url in enumerate(urls)]
        for future in futures:
            index, result = future.result()
            results[index] = result
    feeds = [result for result in results if result is not None]
    events, duplicates = merge(feeds)
    return events, feeds, duplicates


def report(feeds: list[FeedResult], duplicates: int, repaired: int, copom_fixed: int) -> None:
    ok = [feed for feed in feeds if not feed.error]
    failed = [feed for feed in feeds if feed.error]
    total = sum(len(feed.events) for feed in feeds)
    for feed in ok:
        log(f"OK  [{feed.tag}] {feed.name}: {len(feed.events)} eventos")
    for feed in failed:
        log(f"FALHA [{feed.tag}] {feed.name}: {feed.error}")
    print(
        f"Resumo: {len(ok)} calendários, {len(failed)} falhas, "
        f"{total - duplicates} eventos, {duplicates} UIDs repetidos, "
        f"{repaired} términos inválidos removidos, "
        f"{copom_fixed} dias de Copom com horário ajustado."
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="caminho do arquivo .ics (padrão: calendario-bcb.ics na raiz)",
    )
    return parser.parse_args()


def remember_bcb(feeds: list[FeedResult]) -> list[FeedResult]:
    current = [event for feed in feeds if not feed.error for event in feed.events]
    previous = []
    for payload in load_cache("bcb"):
        if isinstance(payload, dict):
            event = event_from_cache(payload)
            if event is not None:
                previous.append(event)
    if current and (not previous or len(current) >= max(1, len(previous) // 2)):
        save_cache("bcb", [event_to_cache(event) for event in current])
        return feeds
    if not current and previous:
        log(f"BCB indisponível. Mantidos {len(previous)} eventos da coleta anterior.")
        return [
            FeedResult(url="cache:bcb", name="Banco Central (cache)", tag="BCB", events=previous)
        ]
    return feeds


def main() -> int:
    args = parse_args()
    urls = load_feed_urls()
    _bcb_events, feeds, _duplicates = build(urls)
    feeds = remember_bcb(feeds)
    extras = [
        load_named_source("ibge", "Calendário de divulgações do IBGE", "IBGE", fontes_extras.fetch_ibge),
        load_named_source("pmi", "PMI Brasil (S&P Global)", "PMI", fontes_extras.fetch_pmi),
        load_named_source("fed", "Calendário do Federal Reserve", "Fed", fontes_extras.fetch_fed),
        load_named_source("bls", "Cronograma do BLS", "BLS", fontes_extras.fetch_bls),
    ]
    feeds = feeds + extras
    events, duplicates = merge(feeds)
    copom_fixed = fix_copom_decisions(events)
    events.sort(key=lambda event: event.sort_key)
    repaired = sum(1 for event in events if event.dropped_dtend)
    report(feeds, duplicates, repaired, copom_fixed)
    if not events:
        log("Nenhum evento disponível. O arquivo anterior foi mantido.")
        return 1
    data = render_calendar(events)
    try:
        publish(args.output, data)
    except ValueError as exc:
        log(f"Resultado inválido ({exc}). O arquivo anterior foi mantido.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
