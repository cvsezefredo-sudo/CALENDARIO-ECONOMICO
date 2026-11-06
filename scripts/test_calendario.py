"""Testes sem rede do calendário econômico."""

from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))

import fontes_extras
import merge_bcb_ics as cal

SAO_PAULO = ZoneInfo("America/Sao_Paulo")
TODAY = date(2026, 11, 6)


def test_ibge_ipca_hours() -> None:
    items = [
        {
            "id": 4321,
            "titulo": "Índice Nacional de Preços ao Consumidor Amplo",
            "data_divulgacao": "12/11/2026 12:00:00",
            "tipo": "Divulgação de Indicadores",
            "produto_id": 9256,
            "alias_produto": "indice-nacional-de-precos-ao-consumidor-amplo",
            "ano_referencia_inicio": 2026,
            "mes_referencia_inicio": 10,
            "ano_referencia_fim": 0,
            "mes_referencia_fim": 0,
            "link": "",
        },
        {
            "id": 4338,
            "titulo": "Índice Nacional de Preços ao Consumidor Amplo",
            "data_divulgacao": "11/12/2026 12:00:00",
            "tipo": "Divulgação de Indicadores",
            "produto_id": 9256,
            "alias_produto": "indice-nacional-de-precos-ao-consumidor-amplo",
            "ano_referencia_inicio": 2026,
            "mes_referencia_inicio": 11,
            "ano_referencia_fim": 0,
            "mes_referencia_fim": 0,
            "link": "",
        },
        {
            "id": 4332,
            "titulo": "Sistema de Contas Nacionais Trimestrais",
            "data_divulgacao": "02/12/2026 12:00:00",
            "tipo": "Divulgação de Indicadores",
            "produto_id": 9300,
            "alias_produto": "contas-nacionais-trimestrais",
            "ano_referencia_inicio": 2026,
            "mes_referencia_inicio": 7,
            "ano_referencia_fim": 2026,
            "mes_referencia_fim": 9,
            "link": "",
        },
    ]
    events = {item["uid"]: item for item in fontes_extras.ibge_items_to_events(items, TODAY)}
    october = events[fontes_extras.uid("ibge", "4321")]
    november = events[fontes_extras.uid("ibge", "4338")]
    gdp = events[fontes_extras.uid("ibge", "4332")]
    assert october["summary"] == "IPCA – outubro/2026"
    assert october["start"].startswith("2026-11-12T09:00:00")
    assert november["summary"] == "IPCA – novembro/2026"
    assert november["start"].startswith("2026-12-11T09:00:00")
    assert gdp["summary"] == "PIB trimestral – 3º trimestre/2026"
    assert gdp["start"].startswith("2026-12-02T09:00:00")


def test_fomc_december_converts_eastern_time() -> None:
    payload = {
        "events": [
            {
                "title": "FOMC Meeting",
                "time": "2:00 p.m.",
                "month": "2026-12",
                "days": "9",
                "description": "&lt;p&gt;Two-day meeting, December 8 - 9&lt;/p&gt;&#10;&#10;&lt;p&gt;Press Conference&lt;/p&gt;",
            },
            {
                "title": "FOMC Minutes",
                "time": "2:00 p.m.",
                "month": "2026-12",
                "days": "30",
                "description": "<p>Meeting of December 8-9</p>",
            },
        ]
    }
    events = fontes_extras.fed_payload_to_events(payload, TODAY)
    decision = next(item for item in events if "Decisão" in item["summary"])
    minutes = next(item for item in events if item["summary"] == "Ata do FOMC")
    assert decision["start"].startswith("2026-12-09T16:00:00")
    assert minutes["start"].startswith("2026-12-30T16:00:00")
    assert "14:00" in decision["description"]
    assert "<" not in decision["description"]
    assert "coletiva" in decision["description"]
    assert "Meeting of December 8-9" in minutes["description"]


def test_bls_cpi_and_payroll() -> None:
    html = """
    <table class="release-list">
      <tr><th>Reference Month</th><th>Release Date</th><th>Release Time</th></tr>
      <tr><td>October 2026</td><td>Nov. 10, 2026</td><td>08:30 AM</td></tr>
      <tr><td>October 2026</td><td>Nov. 06, 2026</td><td>08:30 AM</td></tr>
    </table>
    """
    cpi = fontes_extras.parse_bls_schedule_html(html, "cpi", TODAY)[0]
    payroll = fontes_extras.parse_bls_schedule_html(html, "empsit", TODAY)[1]
    assert cpi["summary"] == "CPI dos EUA – outubro/2026"
    assert cpi["start"].startswith("2026-11-10T10:30:00")
    assert payroll["summary"] == "Payroll dos EUA – outubro/2026"
    assert payroll["start"].startswith("2026-11-06T10:30:00")


def test_pmi_official_and_rule() -> None:
    html = """
    <div class="listHeading"><span>2026</span></div>
    <div class="listSubHeading"><span>December 01</span></div>
    <div class="listItem">
      <span class="releaseDate calendarDate">13:00&nbsp;UTC</span>
      <span class="releaseTitle">S&amp;P Global Brazil Manufacturing PMI</span>
    </div>
    <div class="listSubHeading"><span>December 03</span></div>
    <div class="listItem">
      <span class="releaseDate calendarDate">13:00&nbsp;UTC</span>
      <span class="releaseTitle">S&amp;P Global Brazil Services PMI</span>
    </div>
    """
    official = fontes_extras.parse_pmi_html(html, TODAY)
    by_summary = {item["summary"]: item for item in official}
    industry = by_summary["PMI Industrial Brasil – novembro/2026"]
    services = by_summary["PMI de Serviços Brasil – novembro/2026"]
    assert industry["start"].startswith("2026-12-01T10:00:00")
    assert services["start"].startswith("2026-12-03T10:00:00")
    assert "estimada" not in industry["description"]
    filled = fontes_extras.pmi_rule_events(TODAY, official)
    assert all(item["uid"] != industry["uid"] for item in filled)
    november = next(item for item in filled if item["start"].startswith("2026-11-03"))
    assert "estimada" in november["description"].lower() or "estimada" in november["description"]
    assert fontes_extras.nth_business_day(2026, 11, 1) == date(2026, 11, 3)
    assert fontes_extras.nth_business_day(2026, 11, 3) == date(2026, 11, 5)
    assert fontes_extras.nth_business_day(2026, 12, 1) == date(2026, 12, 1)
    assert fontes_extras.nth_business_day(2026, 5, 1) == date(2026, 5, 4)
    assert fontes_extras.easter(2026) == date(2026, 4, 5)
    assert fontes_extras.easter(2025) == date(2025, 4, 20)


def test_copom_decision_time_and_epoch_dropped() -> None:
    raw = """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:copom-dia-1
DTSTAMP:20260624T120000Z
DTSTART;TZID=America/Sao_Paulo:20261208T000000
DTEND;TZID=America/Sao_Paulo:20261208T000000
SUMMARY:Reunião do Copom
DESCRIPTION:Reunião do Copom
END:VEVENT
BEGIN:VEVENT
UID:copom-dia-2
DTSTAMP:20260624T120000Z
DTSTART;TZID=America/Sao_Paulo:20261209T000000
DTEND;TZID=America/Sao_Paulo:20261209T000000
SUMMARY:Reunião do Copom
DESCRIPTION:Reunião do Copom
END:VEVENT
BEGIN:VEVENT
UID:copom-epoch
DTSTAMP:20260624T120000Z
DTSTART;TZID=America/Sao_Paulo:19700101T000000
SUMMARY:Reunião do Copom
DESCRIPTION:data nula
END:VEVENT
BEGIN:VEVENT
UID:copom-texto
DTSTAMP:20260624T120000Z
DTSTART:09/12/2026 18:30:00
SUMMARY:Outro evento
END:VEVENT
END:VCALENDAR
"""
    events = cal.parse_events(raw, "Copom")
    uids = {event.uid for event in events}
    assert "copom-epoch" not in uids
    text = next(event for event in events if event.uid == "copom-texto")
    start = next(prop for prop in text.props if prop.name == "DTSTART")
    assert start.value == "20261209T183000"
    assert "America/Sao_Paulo" in start.params
    fixed = cal.fix_copom_decisions(events)
    assert fixed == 2
    first = next(event for event in events if event.uid == "copom-dia-1")
    second = next(event for event in events if event.uid == "copom-dia-2")
    first_start = next(prop for prop in first.props if prop.name == "DTSTART")
    second_start = next(prop for prop in second.props if prop.name == "DTSTART")
    second_summary = next(prop for prop in second.props if prop.name == "SUMMARY")
    assert first_start.params == ";VALUE=DATE"
    assert first_start.value == "20261208"
    assert second_start.value == "20261209T183000"
    assert second_summary.value == "[Copom] Decisão de juros (Copom)"
    assert cal.fix_copom_decisions(events) == 0


def test_calendar_has_no_epoch_event() -> None:
    raw = """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:ok
DTSTAMP:20261106T120000Z
DTSTART;TZID=America/Sao_Paulo:20261211T090000
SUMMARY:IPCA
END:VEVENT
END:VCALENDAR
"""
    events = cal.parse_events(raw, "IBGE")
    data = cal.render_calendar(events)
    text = data.decode("utf-8")
    assert "19700101" not in text
    assert "DTSTART:19900101T000000" in text
    assert cal.validate(data) == 1
    vevents = text.split("BEGIN:VEVENT")[1:]
    assert all("19900101" not in part for part in vevents)


def test_known_clock_conversions() -> None:
    fomc = datetime(2026, 12, 9, 14, 0, tzinfo=ZoneInfo("America/New_York"))
    assert fomc.astimezone(SAO_PAULO).hour == 16
    cpi = datetime(2026, 11, 10, 8, 30, tzinfo=ZoneInfo("America/New_York"))
    assert cpi.astimezone(SAO_PAULO).strftime("%H:%M") == "10:30"


if __name__ == "__main__":
    test_ibge_ipca_hours()
    test_fomc_december_converts_eastern_time()
    test_bls_cpi_and_payroll()
    test_pmi_official_and_rule()
    test_copom_decision_time_and_epoch_dropped()
    test_calendar_has_no_epoch_event()
    test_known_clock_conversions()
    print("ok")
