"""Marian planner adapter, alternative engines and the runtime status output.

A fake ``marian`` package stands in for Marian (same plan API and choice
rule), and a fake engine executable stands in for a patched llama-server.
No GPU, model load or profile run happens here.
"""

from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import sys

import pytest

from backend_fakes import FakeStrata, install_fake_marian, profile_rows, write_fake_engine
from themis_shield.inference import NotEnoughMemory, TargetUnavailable
from themis_shield.inference.llamacpp import LlamaCppTarget, engine_extra_args
from themis_shield.inference.marian import MarianPlanner, MarianTarget, PlanDecision
from themis_shield.runtime.calibration import model_signature
from themis_shield.runtime.catalogue import BUILTINS, Catalogue, ModelEntry
from themis_shield.runtime.planning import LaunchPlanner, merge_engine_args
from themis_shield.runtime.resources import ResourceSnapshot
from themis_shield.runtime.scheduler import Scheduler
from themis_shield.runtime.settings import RuntimeSettings

GEMMA_26B = BUILTINS[1]   # MoE (UD-IQ3_XXS), ctx 16384, -ncmoe 18, est 7.1 GiB VRAM


@pytest.fixture
def profiles(tmp_path):
    directory = tmp_path / "profiles"
    directory.mkdir()
    (directory / "abc123-def456-llama.cpp.json").write_text("{}", encoding="utf-8")
    return directory


@pytest.fixture
def model(tmp_path):
    models = tmp_path / "models"
    models.mkdir()
    path = models / GEMMA_26B.file
    path.write_bytes(b"SYNTHETIC-GGUF-STUB")
    return path


def planned_entry(**changes) -> ModelEntry:
    values = {"id": "gemma-planned", "roles": ("answer",), "planner": "marian", **changes}
    return replace(GEMMA_26B, **values)


def no_marian(name):
    raise ImportError(name)


# -- planner -------------------------------------------------------------------

def test_not_installed_falls_back(model):
    planner = MarianPlanner(importer=no_marian)
    assert planner.available() == (False, "marian-not-installed")
    decision = planner.plan(model, 16384)
    assert (decision.source, decision.reason) == ("catalogue", "marian-not-installed")
    assert planner.status()["installed"] is False


def test_unrelated_marian_package_is_not_trusted(model, monkeypatch):
    import types

    unrelated = types.ModuleType("marian.plan")   # e.g. a different PyPI package
    monkeypatch.setitem(sys.modules, "marian", types.ModuleType("marian"))
    monkeypatch.setitem(sys.modules, "marian.plan", unrelated)
    decision = MarianPlanner().plan(model, 16384)
    assert decision.reason == "marian-incompatible"


def test_plan_translates_to_validated_llama_args(model, profiles, monkeypatch):
    calls = install_fake_marian(monkeypatch, profile_rows())
    decision = MarianPlanner(profiles).plan(model, 16384)
    assert decision.planned and decision.reason == "planned"
    # smallest measured context that fits (16384), then fastest generation (q8_0, -ncmoe 14)
    assert decision.ctx == 16384
    assert decision.args == (
        "-ngl", "999", "-c", "16384", "-ctk", "q8_0", "-ctv", "q8_0", "-ncmoe", "14", "-t", "8",
    )
    assert decision.vram_peak_gb == pytest.approx(7300 / 1024, abs=0.001)
    assert decision.tg_tps == 31.0
    assert decision.profile == "abc123-def456-llama.cpp.json"
    assert calls.last_need == 16384 and calls.last_objective == "tg"
    assert calls.last_dir == profiles
    record = decision.record()
    assert record["args_source"] == "marian"
    json.dumps(record)


def test_prompt_speed_objective(model, profiles, monkeypatch):
    install_fake_marian(monkeypatch, profile_rows())
    rows = profile_rows()
    rows[1]["pp_tps"] = 900.0
    install_fake_marian(monkeypatch, rows)
    decision = MarianPlanner(profiles, objective="pp").plan(model, 10000)
    assert decision.args[decision.args.index("-ncmoe") + 1] == "22"
    with pytest.raises(ValueError):
        MarianPlanner(objective="fastest")


@pytest.mark.parametrize("rows,need,reason", [
    ([], 4096, "no-profile"),
    (profile_rows(), 65536, "no-fit"),                                   # never extrapolates
    ([dict(profile_rows()[3])], 16384, "no-fit"),                        # overflow rows never qualify
])
def test_fallback_reasons(model, profiles, monkeypatch, rows, need, reason):
    install_fake_marian(monkeypatch, rows)
    decision = MarianPlanner(profiles).plan(model, need)
    assert (decision.source, decision.reason) == ("catalogue", reason)
    assert decision.args == ()


def test_missing_profiles_directory_and_model(tmp_path, model, monkeypatch):
    install_fake_marian(monkeypatch, profile_rows(), profiles_dir=None)
    assert MarianPlanner().plan(model, 4096).reason == "no-profiles-dir"
    assert MarianPlanner(tmp_path / "absent").plan(model, 4096).reason == "no-profiles-dir"
    assert MarianPlanner(tmp_path).plan(tmp_path / "absent.gguf", 4096).reason == "model-missing"


def test_default_profiles_directory_comes_from_marian(profiles, model, monkeypatch):
    calls = install_fake_marian(monkeypatch, profile_rows(), profiles_dir=profiles)
    assert MarianPlanner().plan(model, 8192).planned
    assert calls.last_dir == profiles


@pytest.mark.parametrize("overrides", [
    {"cache_type": "q3_bogus"},
    {"n_cpu_moe": 5000},
    {"n_cpu_moe": True},
    {"extra": ["--host", "0.0.0.0"]},
    {"ctx": 1024},              # below what was asked for
])
def test_invalid_plans_are_refused(model, profiles, monkeypatch, overrides):
    install_fake_marian(monkeypatch, profile_rows(), config_overrides=overrides)
    decision = MarianPlanner(profiles).plan(model, 8192)
    assert (decision.source, decision.reason) == ("catalogue", "plan-invalid")


def test_expert_cache_plans_need_a_patched_engine(model, profiles, monkeypatch):
    install_fake_marian(monkeypatch, profile_rows(), config_overrides={"moe_cache_mib": 4000})
    planner = MarianPlanner(profiles)
    assert planner.plan(model, 8192).reason == "plan-needs-patched-engine"
    decision = planner.plan(model, 8192, allow_extra_args=True)
    assert decision.planned and decision.extra_args == ("--moe-cache-mib", "4000")


def test_planner_errors_do_not_leak(model, profiles, monkeypatch):
    install_fake_marian(monkeypatch, profile_rows(), raise_on_load=True)
    decision = MarianPlanner(profiles).plan(model, 8192)
    assert (decision.source, decision.reason) == ("catalogue", "planner-error")
    assert str(model) not in json.dumps(decision.record())


def test_decisions_are_cached_until_profiles_change(model, profiles, monkeypatch):
    calls = install_fake_marian(monkeypatch, profile_rows())
    planner = MarianPlanner(profiles)
    planner.plan(model, 8192)
    planner.plan(model, 8192)
    assert calls.load == 1
    (profiles / "abc123-def456-llama.cpp-2.json").write_text("{}", encoding="utf-8")
    planner.plan(model, 8192)
    assert calls.load == 2


def test_plan_decision_codes_are_closed():
    with pytest.raises(ValueError):
        PlanDecision("marian", "because I said so")
    with pytest.raises(ValueError):
        PlanDecision("elsewhere", "planned")


def test_marian_is_not_a_runtime():
    target = MarianTarget()
    assert not target.health()
    with pytest.raises(TargetUnavailable, match="planner, not a runtime"):
        target.start()


# -- launch planning ---------------------------------------------------------------

def test_launch_planner_static_and_planned(model, profiles, monkeypatch):
    install_fake_marian(monkeypatch, profile_rows())
    launch = LaunchPlanner(MarianPlanner(profiles), model.parent)
    entry, decision = launch(GEMMA_26B)
    assert entry is GEMMA_26B and decision.reason == "static"
    planned, decision = launch(planned_entry())
    assert decision.planned
    assert planned.args == decision.args and planned.ctx == 16384
    assert planned.est_vram_gb == pytest.approx(7.129, abs=0.001)    # raised to the measured peak
    assert planned.variants == ()
    assert planned.id == "gemma-planned"


def test_launch_planner_never_lowers_memory_estimates(model, profiles, monkeypatch):
    install_fake_marian(monkeypatch, profile_rows())
    launch = LaunchPlanner(MarianPlanner(profiles), model.parent)
    planned, decision = launch(planned_entry(est_vram_gb=7.8, min_vram_gb=7.1))
    assert decision.planned and planned.est_vram_gb == 7.8


def test_launch_planner_model_missing_and_no_planner(tmp_path, monkeypatch):
    install_fake_marian(monkeypatch, profile_rows())
    entry = planned_entry()
    _, decision = LaunchPlanner(MarianPlanner(tmp_path), tmp_path)(entry)
    assert decision.reason == "model-missing"
    _, decision = LaunchPlanner(None, tmp_path)(entry)
    assert decision.reason == "marian-not-installed"


def test_merge_engine_args_plan_wins():
    assert merge_engine_args(
        ("--moe-miss-source", "tier", "--moe-cache-mib", "2000"), ("--moe-cache-mib", "4000"),
    ) == ("--moe-miss-source", "tier", "--moe-cache-mib", "4000")


class RecordingTarget:
    name = "laptop"
    pid = None
    load_ms = 2.0

    def __init__(self, entry, launched):
        self.entry, self.launched, self.resident, self.last_timings = entry, launched, False, {}
        self.plan = None

    def set_launch_plan(self, source, reason):
        self.plan = (source, reason)

    def start(self):
        self.launched.append((self.entry.id, self.entry.args, self.plan))
        self.resident = True

    def stop(self):
        self.resident = False

    def health(self):
        return self.resident


def planned_scheduler(tmp_path, model, launch, free=7.8):
    catalogue = Catalogue([*BUILTINS, planned_entry()])
    launched = []
    scheduler = Scheduler(
        catalogue, lambda entry: RecordingTarget(entry, launched),
        lambda: ResourceSnapshot(8.0, free, 30, 32, 2, 10, 64),
        lease_path=tmp_path / "gpu.lock", memory_settle_seconds=0.2,
        launch_planner=launch,
    )
    return scheduler, launched


def test_scheduler_uses_the_plan_and_records_it(tmp_path, model, profiles, monkeypatch):
    install_fake_marian(monkeypatch, profile_rows())
    launch = LaunchPlanner(MarianPlanner(profiles), model.parent)
    scheduler, launched = planned_scheduler(tmp_path, model, launch)
    try:
        with scheduler.acquire("gemma-planned"):
            pass
        entry_id, args, plan = launched[-1]
        assert plan == ("marian", "planned")
        assert args[args.index("-ncmoe") + 1] == "14"
        status = scheduler.status()
        assert status["launch_plan"]["args_source"] == "marian"
        assert status["timings"][-1]["args_source"] == "marian"
        assert status["timings"][-1]["plan_reason"] == "planned"
        with scheduler.acquire("gemma4-e4b"):
            pass
        assert launched[-1][2] == ("catalogue", "static")
    finally:
        scheduler.close()


def test_plan_that_does_not_fit_now_falls_back(tmp_path, model, profiles, monkeypatch):
    rows = profile_rows()
    rows[2]["vram_peak_mib"] = 7900          # the fastest plan needs 7.7 GiB + margin
    install_fake_marian(monkeypatch, rows)
    launch = LaunchPlanner(MarianPlanner(profiles), model.parent)
    scheduler, launched = planned_scheduler(tmp_path, model, launch, free=7.7)
    try:
        with scheduler.acquire("gemma-planned"):
            pass
        entry_id, args, plan = launched[-1]
        assert plan == ("catalogue", "plan-does-not-fit-now")
        assert args == GEMMA_26B.args
        assert scheduler.status()["launch_plan"]["reason"] == "plan-does-not-fit-now"
    finally:
        scheduler.close()


def test_without_marian_the_catalogue_args_are_used(tmp_path, model):
    launch = LaunchPlanner(MarianPlanner(importer=no_marian), model.parent)
    scheduler, launched = planned_scheduler(tmp_path, model, launch)
    try:
        with scheduler.acquire("gemma-planned"):
            pass
        assert launched[-1][1:] == (GEMMA_26B.args, ("catalogue", "marian-not-installed"))
    finally:
        scheduler.close()


def test_neither_plan_nor_catalogue_fits(tmp_path, model, profiles, monkeypatch):
    install_fake_marian(monkeypatch, profile_rows())
    launch = LaunchPlanner(MarianPlanner(profiles), model.parent)
    scheduler, launched = planned_scheduler(tmp_path, model, launch, free=3.0)
    try:
        with pytest.raises(NotEnoughMemory):
            with scheduler.acquire("gemma-planned"):
                pass
        assert launched == []
    finally:
        scheduler.close()


def test_runtime_log_records_who_chose_the_args(tmp_path, model):
    entry = planned_entry()
    target = LlamaCppTarget(
        replace(entry, args=("-ngl", "999", "-c", "16384", "-ctk", "q8_0", "-ctv", "q8_0", "-ncmoe", "14")),
        models_dir=model.parent, logs_dir=tmp_path / "logs",
        startup_timeout=10, retry_pause=0.01, stop_timeout=2, poll_interval=0.02,
        command_prefix=(sys.executable, str(Path(__file__).with_name("fake_llama_server.py"))),
        timeout_seconds=2,
    )
    target.set_launch_plan("marian", "planned")
    try:
        target.start()
        log = target.log_path.read_text(encoding="utf-8")
    finally:
        target.stop()
    assert "ARGS_SOURCE marian planned" in log
    assert "ARGS -t 8 -c 16384 -ngl 999 -ctk q8_0 -ctv q8_0 -ncmoe 14" in log
    assert str(model.parent) not in log and "gguf" not in log.lower()
    with pytest.raises(ValueError):
        target.set_launch_plan("marian", "free text")


# -- alternative engines ------------------------------------------------------------

def test_engine_extra_args_allow_list():
    assert engine_extra_args([
        "--moe-miss-source", "ring", "--moe-ring-mib", "64", "--moe-cache-mib", "4000",
        "--moe-tier-mib", "6144",
    ])
    for bad in (
        ["--host", "0.0.0.0"], ["--port", "80"], ["--api-key-file", "x"], ["-m", "x.gguf"],
        ["--log-file", "x"], ["--moe-cache-mib", "-1"], ["--moe-cache-mib", "1e3"],
        ["--moe-ring-mib", "0"], ["--moe-miss-source", "network"], ["--moe-cache-mib"],
        ["--moe-cache-mib", "1", "--moe-cache-mib", "2"], ["-ngl", "99"],
    ):
        with pytest.raises(ValueError):
            engine_extra_args(bad)


def test_catalogue_engine_and_planner_rules():
    base = BUILTINS[0]
    ok = replace(base, engine_path="patched/llama-server", engine_args=("--moe-cache-mib", "100"))
    assert ok.engine_args == ("--moe-cache-mib", "100")
    for changes in (
        {"engine_args": ("--moe-cache-mib", "100")},              # needs engine_path
        {"engine_path": "/abs/llama-server"},
        {"engine_path": "../escape/llama-server"},
        {"engine_path": "dir\\\\llama-server"},
        {"engine_path": "C:llama-server"},
        {"engine_path": "x", "engine_args": ("--host", "0.0.0.0")},
        {"planner": "oracle"},
    ):
        with pytest.raises(ValueError):
            replace(base, **changes)
    with pytest.raises(ValueError):
        replace(BUILTINS[1], runtime="ollama", planner="marian")


def test_catalogue_comments_are_ignored(tmp_path):
    path = tmp_path / "catalogue.json"
    path.write_text(json.dumps({
        "version": 1, "comment": "top-level note",
        "models": [{"id": "gemma4-e4b", "comment": "entry note", "display_name": "Renamed"}],
    }), encoding="utf-8")
    assert Catalogue.load(path).get("gemma4-e4b").display_name == "Renamed"
    path.write_text(json.dumps({"version": 1, "comment": 7, "models": []}), encoding="utf-8")
    with pytest.raises(ValueError):
        Catalogue.load(path)
    path.write_text(json.dumps({"version": 1, "models": [], "notes": "x"}), encoding="utf-8")
    with pytest.raises(ValueError):
        Catalogue.load(path)


def test_shipped_example_catalogue_back_end_entries():
    from themis_shield.profiles import profile_path

    catalogue = Catalogue.load(profile_path("catalogue", "example"))
    planned = catalogue.get("gemma4-26b-a4b-planned")
    assert planned.planner == "marian" and planned.roles == ("answer",)
    patched = catalogue.get("gemma4-26b-a4b-patched")
    assert patched.engine_path and patched.engine_args


def test_engine_for_resolves_inside_engines_dir(tmp_path):
    settings = RuntimeSettings(engine_path=tmp_path / "engine" / "llama-server", engines_dir=tmp_path / "engines")
    assert settings.engine_for(BUILTINS[0]) == tmp_path / "engine" / "llama-server"
    entry = replace(BUILTINS[0], engine_path="patched/llama-server")
    assert settings.engine_for(entry) == (tmp_path / "engines" / "patched" / "llama-server").resolve()
    default_root = RuntimeSettings(engine_path=tmp_path / "engine" / "llama-server")
    assert default_root.engine_for(entry) == (tmp_path / "engine" / "patched" / "llama-server").resolve()


def test_signature_changes_only_with_back_end_fields():
    base = BUILTINS[0]
    assert model_signature(base) == model_signature(replace(base, display_name="x"))
    assert model_signature(base) != model_signature(replace(base, engine_path="patched/llama-server"))
    assert model_signature(base) != model_signature(replace(base, planner="marian"))


def test_fake_engine_binary_receives_validated_extra_args(tmp_path, model, monkeypatch):
    engines = tmp_path / "engines"
    engine = write_fake_engine(engines / "patched")
    entry = replace(
        GEMMA_26B, engine_path=f"patched/{engine.name}",
        engine_args=("--moe-miss-source", "tier", "--moe-tier-mib", "6144"),
    )
    settings = RuntimeSettings(engines_dir=engines, models_dir=model.parent)
    argv_file = tmp_path / "argv.json"
    monkeypatch.setenv("THEMIS_FAKE_ENGINE_ARGV", str(argv_file))
    target = LlamaCppTarget(
        entry, engine_path=settings.engine_for(entry), models_dir=model.parent,
        logs_dir=tmp_path / "logs", startup_timeout=20, retry_pause=0.01,
        stop_timeout=5, poll_interval=0.05, timeout_seconds=5,
    )
    try:
        target.start()
        assert target.health()
        log = target.log_path.read_text(encoding="utf-8")
    finally:
        target.stop()
    argv = json.loads(argv_file.read_text(encoding="utf-8"))
    assert argv[argv.index("--moe-miss-source") + 1] == "tier"
    assert argv[argv.index("--moe-tier-mib") + 1] == "6144"
    assert argv[argv.index("--host") + 1] == "127.0.0.1"
    assert "ENGINE_ALTERNATIVE" in log and str(engines) not in log


# -- runtime settings, config and status ---------------------------------------------

def test_settings_file_and_toml_keys(tmp_path):
    from themis_shield.integration import runtime_settings

    settings_file = tmp_path / "runtime.json"
    settings_file.write_text(json.dumps({
        "engines_dir": "~/engines", "marian_profiles_dir": str(tmp_path / "p"),
        "marian_objective": "pp", "strata_api_key_env": "MY_STRATA_KEY",
    }), encoding="utf-8")
    loaded = RuntimeSettings.load(settings_file)
    assert loaded.engines_dir == Path("~/engines").expanduser()
    assert loaded.marian_objective == "pp"
    config = {"runtime": {
        "settings_file": str(settings_file), "marian_objective": "tg",
        "engines_dir": "", "strata_api_key_env": "OTHER_KEY",
    }}
    merged = runtime_settings(config)
    assert merged.marian_objective == "tg"                    # toml overrides the file
    assert merged.engines_dir == Path("~/engines").expanduser()  # empty keeps the file's value
    assert merged.strata_api_key_env == "OTHER_KEY"
    with pytest.raises(ValueError):
        runtime_settings({"runtime": {"marian_objective": "fastest"}})
    with pytest.raises(ValueError):
        runtime_settings({"runtime": {"strata_api_key_env": "has spaces"}})


def test_example_config_parses_with_back_end_keys():
    import tomllib
    from themis_shield.integration import runtime_settings

    path = Path(__file__).resolve().parents[1] / "config" / "themis.example.toml"
    config = tomllib.loads(path.read_text(encoding="utf-8"))
    settings = runtime_settings(config)
    assert settings.marian_objective == "tg"
    assert settings.strata_api_key_env == "STRATA_API_KEY"


def test_runtime_status_reports_both_back_ends(tmp_path, model, profiles, monkeypatch, capsys):
    from themis_shield.runtime import __main__ as cli
    from themis_shield.runtime import settings as settings_module

    install_fake_marian(monkeypatch, profile_rows())
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    monkeypatch.setattr(
        settings_module, "ResourceReader",
        lambda gpu: (lambda: ResourceSnapshot(8.0, 7.5, 20, 32, 12, 10, 64)),
    )
    with FakeStrata(model="qwen3.8-flash-next-gyro-s") as strata:
        catalogue_file = tmp_path / "catalogue.json"
        catalogue_file.write_text(json.dumps({"version": 1, "models": [
            {"id": "strata-qwen3.8-flash-next", "endpoint": strata.url,
             "served_model": "qwen3.8-flash-next-gyro-s"},
            {"id": "gemma-planned", "display_name": "planned", "runtime": "llamacpp",
             "planner": "marian", "file": GEMMA_26B.file, "args": list(GEMMA_26B.args),
             "ctx": 16384, "roles": ["answer"], "est_vram_gb": 7.1, "est_ram_gb": 16.0,
             "min_vram_gb": 7.1, "expected_tok_s": 23.6},
            {"id": "gemma-patched", "display_name": "patched", "runtime": "llamacpp",
             "engine_path": "patched/llama-server", "engine_args": ["--moe-cache-mib", "100"],
             "file": GEMMA_26B.file, "args": list(GEMMA_26B.args), "ctx": 16384,
             "roles": ["answer"], "est_vram_gb": 7.1, "est_ram_gb": 16.0,
             "min_vram_gb": 7.1, "expected_tok_s": 23.6},
        ]}), encoding="utf-8")
        settings_file = tmp_path / "runtime.json"
        settings_file.write_text(json.dumps({
            "models_dir": str(model.parent), "marian_profiles_dir": str(profiles),
            "engines_dir": str(tmp_path / "engines"),
        }), encoding="utf-8")
        code = cli.main(["--settings", str(settings_file), "--catalogue", str(catalogue_file), "status"])
    out = capsys.readouterr().out
    assert code == 0, out
    status = json.loads(out)
    assert status["status"] == "not running"
    assert status["resources"]["external_holders"] == ["strata-qwen3.8-flash-next"]
    assert status["resources"]["vram_free_gb"] == pytest.approx(0.1)
    backends = status["backends"]
    strata_status = backends["strata"][0]
    assert strata_status["attached"] is True and strata_status["model_ok"] is True
    assert strata_status["endpoint"].startswith("http://127.0.0.1:")
    assert strata_status["vram_counted_gb"] == 7.9
    assert strata_status["metrics"]["state"] == "idle"
    assert backends["marian"]["installed"] is True
    assert backends["marian"]["entries"][0]["id"] == "gemma-planned"
    assert backends["marian"]["entries"][0]["args_source"] == "marian"
    assert backends["alternative_engines"] == [
        {"id": "gemma-patched", "engine_found": False, "engine_args": ["--moe-cache-mib", "100"]},
    ]
    assert str(model.parent) not in out and str(tmp_path / "engines") not in out


def test_plan_prefers_the_models_own_vram_share(model, profiles, monkeypatch):
    rows = profile_rows()
    rows[2]["vram_baseline_mib"] = 1100       # desktop held 1.1 GiB while this row was profiled
    rows[2]["vram_model_mib"] = 6200
    install_fake_marian(monkeypatch, rows)
    decision = MarianPlanner(profiles).plan(model, 16384)
    assert decision.planned
    assert decision.vram_peak_gb == pytest.approx(6200 / 1024, abs=0.001)


def test_plan_without_a_model_share_keeps_the_whole_peak(model, profiles, monkeypatch):
    rows = profile_rows()
    rows[2]["vram_model_mib"] = 0             # missing or unusable: fall back to the peak
    install_fake_marian(monkeypatch, rows)
    decision = MarianPlanner(profiles).plan(model, 16384)
    assert decision.vram_peak_gb == pytest.approx(7300 / 1024, abs=0.001)
