"""Real layout falsifiers at both reference viewports.

Requires Playwright and an installed Chromium browser. Missing browser tooling
is reported as a skip, not a layout pass.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from themis_shield.web.reasons import catalogue

# Existing marking cases use the au-pspf example profile.
pytestmark = pytest.mark.usefixtures("au_pspf_markings")


@pytest.mark.parametrize("width,height", [(1280, 800), (1440, 1000)])
def test_findings_single_scroll_column_and_polish(width, height):
    playwright = pytest.importorskip("playwright.sync_api")
    root = Path(__file__).parents[1] / "server" / "static"
    local = {
        "id": "gemma4-e4b", "display_name": "Synthetic E4B", "runtime": "llamacpp",
        "roles": ["answer"], "available": True, "tokens_per_hour_estimate": 7_200_000,
        "variants": [{
            "mode": "full", "fits_now": True, "expected_prompt_tok_s": 2000,
            "expected_gen_tok_s": 50, "measured": True,
            "measured_label": "measured on this device 22/10/2026",
        }],
    }
    preview = {
        "preview_id": "pre-synthetic", "marking": {
            "class": "OFFICIAL", "reason_code": "ROUTE_CLOUD_PII", "banner": "",
        },
        "route_options": ["local", "cloud"],
        "route_reasons": {"local": "ROUTE_CLOUD_PII", "cloud": "ROUTE_CLOUD_PII"},
        "findings": {"counts_by_label": {"PERSON": 20}, "replacements": [
            {"category": "P", "label": "PERSON", "length": 12} for _ in range(20)
        ]},
        "tiers": {str(tier): {"ms": 100, "ran": True} for tier in (1, 2, 3)},
        "decision": "RELEASE", "reasons": [],
        "size": {"documents": 1, "words": 20, "routine": True},
    }
    local_job = {
        "job_id": "JOB-2026-0001", "route": "local", "marking_class": "PROTECTED",
        "status": "Completed", "stage1": None, "package": None, "stage2": None,
        "due": None, "expiry": None, "counts": {}, "timings": {},
    }
    queue_job = {
        **local_job, "job_id": "JOB-2026-0002", "route": "cloud-tokenised",
        "summary": "Explain a synthetic request for ⟦P:abcdef⟧.",
        "stage1": "requested", "package": "ready", "stage2": "pending",
        "size": {"documents": 2, "words": 123}, "major_flags": ["VOLUME", "NEW_SOURCE"],
        "due": "2026-10-22T00:00:00Z",
    }
    with playwright.sync_playwright() as manager:
        try:
            browser = manager.chromium.launch()
        except playwright.Error:
            pytest.skip("Playwright Chromium is not installed")
        try:
            page = browser.new_page(viewport={"width": width, "height": height}, timezone_id="Europe/Lisbon")

            def serve(route):
                path = route.request.url.split("8742", 1)[-1].split("?", 1)[0]
                responses = {
                    "/api/reasons": {"reasons": catalogue()},
                    "/api/settings": {
                        "local_target": "laptop", "default_local_model": "gemma4-e4b",
                        "default_cloud_model": "themis/cloud:example-cloud-model",
                        "operator_console_enabled": True,
                    },
                    "/api/models": {"local": [local], "cloud": [{
                        "id": "themis/cloud:example-cloud-model", "display_name": "Synthetic cloud",
                    }]},
                    "/api/runtime": {"loaded_models": [], "queue": 0, "resources": {}},
                    "/api/capacity": {
                        "tiers": [], "end_to_end_input_tokens_per_hour": 40272,
                        "measured_utc": "2026-10-22T00:00:00Z",
                        "reference_job": {"documents": 20, "measured": True},
                    },
                    "/api/preview": preview,
                    "/api/jobs": {"jobs": [local_job]},
                    "/api/operator/queue": {"jobs": [queue_job]},
                }
                if path in responses:
                    route.fulfill(content_type="application/json", body=json.dumps(responses[path]))
                else:
                    file = root / ("index.html" if path == "/" else path.lstrip("/"))
                    kind = "text/html" if file.suffix == ".html" else "text/css" if file.suffix == ".css" else "text/javascript"
                    route.fulfill(content_type=kind, body=file.read_text(encoding="utf-8"))

            page.route("http://127.0.0.1:8742/**", serve)
            page.goto("http://127.0.0.1:8742/")
            page.locator("#prompt").fill("Synthetic question.")
            page.locator("#check").click()
            page.locator("#findings").wait_for(state="visible")
            assert page.evaluate("""
                () => {
                    const panel = document.getElementById("findings");
                    const composer = document.getElementById("composer");
                    const messages = document.getElementById("messages");
                    const style = getComputedStyle(panel);
                    const box = composer.getBoundingClientRect();
                    return messages.contains(panel) && !composer.contains(panel)
                      && style.overflowY === "visible" && style.maxHeight === "none"
                      && panel.scrollHeight <= panel.clientHeight + 2
                      && box.bottom <= innerHeight && box.top >= 0;
                }
            """)
            page.locator('[data-screen="models"]').click()
            assert "Reading rate (prefill), tokens/hour" in page.locator("#local-models").inner_text()
            assert "40,300" in page.locator("#capacity").inner_text()
            assert "22/10/2026" in page.locator("#capacity").inner_text()
            page.locator('[data-screen="jobs"]').click()
            assert "Local only: no cloud approval stages" in page.locator("#jobs-table").inner_text()
            assert "Stage 1: unknown" not in page.locator("#jobs-table").inner_text()
            page.locator('[data-screen="operator"]').click()
            text = page.locator("#operator-queue").inner_text()
            assert "2 documents · 123 words" in text
            assert "new data source" in text.lower()
            assert "22/10/2026, 11:00 local time" in text
            assert '"documents"' not in text and '["VOLUME"' not in text
        finally:
            browser.close()
