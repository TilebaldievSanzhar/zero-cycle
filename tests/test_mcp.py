"""MCP-сервер: инструменты сессии на засеянном пакете, протокол через клиент в памяти и запуск по stdio."""

import json
import sys
from datetime import timedelta

import anyio
import pytest

from zerocycle.bench.corpus import generate
from zerocycle.mcp.server import build
from zerocycle.mcp.tools import Session


@pytest.fixture(scope="module")
def seeded(tmp_path_factory):
    """Засеянный пакет: формат листа (F08), повтор GUID (F07), заменённая редакция СП (D07)."""
    root = tmp_path_factory.mktemp("mcp")
    seed_dir = generate(5, root, codes=["F08", "F07", "D07"])
    truth = json.loads((seed_dir / "ground_truth.json").read_text(encoding="utf-8"))
    return seed_dir, {d["id"]: d for d in truth["defects"]}


@pytest.fixture(scope="module")
def session(seeded):
    seed_dir, _ = seeded
    s = Session(root=seed_dir / "package")
    run_id = s.start(".", "ru")
    assert s.wait(run_id, timeout=120)
    yield s
    s.close()


def test_summary_counts_and_violations(session, seeded):
    _, defects = seeded
    summary = session.summary()
    assert summary["state"] == "done" and summary["run_id"] == "r1"
    assert summary["counts"]["violated"] >= 3 and summary["withheld_by_gate"] == 0
    rules = {v["rule"] for v in summary["violated"]}
    assert {"spds.sheets.format", "spds.pde.guid", "ru.norms.registry"} <= rules
    f08 = next(v for v in summary["violated"] if v["rule"] == "spds.sheets.format")
    assert f08["document"] == defects["F08"]["file"] and f08["page"] == defects["F08"]["page"]
    assert set(f08) >= {"category", "document", "page", "description", "basis"}  # схема ответа рук бенчмарка


def test_list_explain_and_render(session):
    page = session.list_findings(status="violated", category="нормы и ссылки на них")
    assert page["total"] >= 1 and all(f["status"] == "violated" for f in page["findings"])
    fid = page["findings"][0]["id"]
    card = session.explain_finding(fid)
    assert card["rule_card"]["id"] == "ru.norms.registry" and card["rule_card"]["expert_wording"]
    assert card["basis_documents"][0]["document"] == "Реестр «Стройкомплекс.РФ»"
    assert card["anchors"][0]["quote"]
    png, meta = session.render_fragment(fid)
    assert png.startswith(b"\x89PNG") and meta["bbox"] is not None


def test_norm_status_both_jurisdictions():
    s = Session()
    ru = s.norm_status("СП 54.13330.2016 и СП 256.1325800.2016")
    assert [r["status"] for r in ru["results"]] == ["outdated_edition", "current"]
    assert ru["results"][0]["current"]["code"] == "СП 54.13330.2022" and not ru["exhaustive"]
    kz = s.norm_status("СН РК 1.02-03-2011*", "kz")
    assert kz["results"][0]["status"] == "replaced" and kz["results"][0]["basis"].startswith("АГСК-1")
    with pytest.raises(ValueError):
        s.norm_status("без обозначений")


def test_root_limits_paths(session, tmp_path):
    with pytest.raises(ValueError, match="вне разрешённой папки"):
        session.start(str(tmp_path), "ru")
    with pytest.raises(ValueError, match="проверок ещё не было"):
        Session().list_findings()


def test_protocol_tools_over_memory_transport(seeded):
    from mcp.shared.memory import create_connected_server_and_client_session

    seed_dir, _ = seeded
    session = Session(root=seed_dir / "package")

    async def main():
        async with create_connected_server_and_client_session(
            build(session), read_timeout_seconds=timedelta(seconds=120)
        ) as client:
            tools = {t.name for t in (await client.list_tools()).tools}
            assert tools == {
                "check_package",
                "run_status",
                "list_findings",
                "explain_finding",
                "render_fragment",
                "list_rules",
                "norm_status",
            }
            res = await client.call_tool("check_package", {"path": ".", "wait_seconds": 120})
            summary = res.structuredContent
            assert not res.isError and summary["state"] == "done"
            violated = summary["violated"][0]["id"]
            img = await client.call_tool("render_fragment", {"finding_id": violated})
            assert img.content[0].type == "image" and img.content[0].mimeType == "image/png"
            bad = await client.call_tool("explain_finding", {"finding_id": "F999"})
            assert bad.isError and "нет находки" in bad.content[0].text

    try:
        anyio.run(main)
    finally:
        session.close()


def test_stdio_server_starts_and_answers():
    """Как запускает клиент: `python -m zerocycle mcp` по stdio."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(command=sys.executable, args=["-m", "zerocycle", "mcp"])

    async def main():
        with anyio.fail_after(60):
            async with stdio_client(params) as (read, write), ClientSession(read, write) as client:
                init = await client.initialize()
                assert init.serverInfo.name == "zerocycle" and "Нулевой цикл" in (init.instructions or "")
                res = await client.call_tool("list_rules", {"jurisdiction": "kz"})
                ids = {r["id"] for r in res.structuredContent["rules"]}
                assert {"kz.completeness.ird", "kz.norms.agsk1", "spds.sheets.count"} <= ids

    anyio.run(main)
