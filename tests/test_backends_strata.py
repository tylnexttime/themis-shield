"""Strata attach adapter, occupancy accounting and scheduler integration.

Everything runs against FakeStrata (a loopback HTTP fake). Nothing starts,
installs or contacts a real Strata, and nothing touches the GPU.
"""

from __future__ import annotations

from dataclasses import replace
import json
import math
import threading
import time

import pytest

from backend_fakes import CANARY, FakeStrata
from themis_shield.inference import InferenceRequest, NotEnoughMemory, TargetUnavailable
from themis_shield.inference.strata import (
    DEFAULT_MIN_MAX_TOKENS, STRATA_SAMPLING, StrataConfig, StrataTarget, loopback_origin,
)
from themis_shield.runtime.catalogue import BUILTINS, Catalogue, ModelEntry
from themis_shield.runtime.occupancy import OccupancyAwareReader, StrataHolder, strata_holders
from themis_shield.runtime.resources import ResourceSnapshot
from themis_shield.runtime.scheduler import Scheduler

MESSAGES = [{"role": "user", "content": "Synthetic question about a public timetable."}]
STRATA_ID = "strata-qwen3.8-flash-next"


@pytest.fixture
def strata():
    with FakeStrata() as fake:
        yield fake


def strata_entry(url: str | None, model: str = "fake-moe-model", **changes) -> ModelEntry:
    base = next(entry for entry in BUILTINS if entry.id == STRATA_ID)
    return replace(base, endpoint=url, served_model=model if url else None, **changes)


def attached(fake: FakeStrata, **config) -> StrataTarget:
    target = StrataTarget(StrataConfig(endpoint=fake.url, model_id=fake.model, **config))
    target.start()
    return target


# -- endpoint and configuration ------------------------------------------

@pytest.mark.parametrize("url", [
    "http://127.0.0.1:8095/v1", "http://127.0.0.1:8095", "http://127.0.0.1:8095/",
    "http://localhost:8095/v1",
])
def test_loopback_endpoints_accepted_and_normalised(url):
    assert loopback_origin(url) == "http://127.0.0.1:8095"


@pytest.mark.parametrize("url", [
    "http://10.0.0.5:8095/v1", "http://0.0.0.0:8095/v1", "http://[::1]:8095/v1",
    "https://127.0.0.1:8095/v1", "http://user:pw@127.0.0.1:8095/v1",
    "http://127.0.0.1:8095/v1?x=1", "http://127.0.0.1:8095/api", "http://127.0.0.1/v1",
    "http://strata.example:8095/v1", "ftp://127.0.0.1:8095", "",
])
def test_non_loopback_endpoints_refused(url):
    with pytest.raises(ValueError):
        loopback_origin(url)
    with pytest.raises(ValueError):
        StrataConfig(endpoint=url, model_id="m")


def test_unconfigured_target_never_claims_available():
    target = StrataTarget()
    assert not target.health()
    assert target.probe()["configured"] is False
    with pytest.raises(TargetUnavailable, match="not configured"):
        target.start()
    with pytest.raises(TargetUnavailable):
        target.chat(MESSAGES)


def test_config_from_catalogue_entry_uses_entry_speeds():
    entry = strata_entry("http://127.0.0.1:8095/v1", min_max_tokens=4096)
    config = StrataConfig.from_entry(entry, api_key_env="MY_STRATA_KEY")
    assert config.endpoint == "http://127.0.0.1:8095/v1"
    assert config.model_id == "fake-moe-model"
    assert config.min_max_tokens == 4096
    assert config.expected_prompt_tok_s == entry.expected_prompt_tok_s == 100.0
    assert config.expected_tok_s == entry.expected_tok_s
    assert config.ctx == 65536
    assert config.api_key_env == "MY_STRATA_KEY"


def test_bad_key_variable_name_refused():
    with pytest.raises(ValueError):
        StrataConfig(api_key_env="lower-case;rm")


# -- attach ------------------------------------------------------------------

def test_attach_checks_health_and_model_list(strata):
    target = attached(strata)
    assert target.attached and target.health()
    assert target.attach_info == {"max_context": 65536}
    paths = [item["path"] for item in strata.requests]
    assert "/health" in paths and "/v1/models" in paths
    assert not any(item["method"] == "POST" for item in strata.requests)


def test_attach_refused_when_unreachable():
    with FakeStrata() as fake:
        url, model = fake.url, fake.model
    target = StrataTarget(StrataConfig(endpoint=url, model_id=model))
    with pytest.raises(TargetUnavailable, match="not reachable"):
        target.start()
    assert not target.attached


@pytest.mark.parametrize("change,message", [
    ({"service": "llama.cpp"}, "not a Strata server"),
    ({"loaded": False}, "not loaded"),
    ({"health_model": "another-model"}, "different model"),
    ({"listed": ["another-model"]}, "different model"),
])
def test_attach_refusals(strata, change, message):
    for key, value in change.items():
        setattr(strata, key, value)
    target = StrataTarget(StrataConfig(endpoint=strata.url, model_id=strata.model))
    with pytest.raises(TargetUnavailable, match=message):
        target.start()
    assert not target.attached
    assert not any(item["method"] == "POST" for item in strata.requests)


def test_stop_detaches_without_contacting_strata(strata):
    target = attached(strata)
    before = len(strata.requests)
    target.stop()
    assert not target.attached
    assert len(strata.requests) == before
    with pytest.raises(TargetUnavailable, match="not attached"):
        target.chat(MESSAGES)


# -- requests ----------------------------------------------------------------

def test_chat_applies_strata_defaults_and_drops_reasoning(strata):
    target = attached(strata)
    output = target.chat(MESSAGES, max_tokens=1024)
    body = strata.chat_bodies[-1]
    assert body["model"] == strata.model
    assert body["max_tokens"] == DEFAULT_MIN_MAX_TOKENS          # floor for a thinking model
    assert body["stream"] is False
    assert "cache_prompt" not in body
    for key, value in STRATA_SAMPLING.items():
        assert body[key] == value
    message = output["choices"][0]["message"]
    assert message == {"role": "assistant", "content": "Synthetic local answer."}
    assert "SYNTHETIC-REASONING" not in json.dumps(output)
    assert output["usage"] == {"prompt_tokens": 12, "completion_tokens": 30, "total_tokens": 42}


def test_large_max_tokens_kept_and_capped_by_context(strata):
    target = attached(strata, ctx=16384, min_max_tokens=2048)
    target.chat(MESSAGES, max_tokens=4000)
    assert strata.chat_bodies[-1]["max_tokens"] == 4000
    target.chat(MESSAGES, max_tokens=50_000)
    assert strata.chat_bodies[-1]["max_tokens"] == 16384


def test_truncated_answer_is_reported_not_hidden(strata):
    strata.finish, strata.content = "length", ""
    target = attached(strata)
    output = target.chat(MESSAGES)
    assert output["choices"][0]["finish_reason"] == "length"


def test_streaming_is_a_buffered_burst(strata):
    target = attached(strata)
    chunks = list(target.chat(MESSAGES, stream=True))
    assert chunks[1]["choices"][0]["delta"]["content"] == "Synthetic local answer."
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop"


def test_legacy_request_interface(strata):
    target = attached(strata)
    response = target.chat(InferenceRequest({"messages": MESSAGES, "max_tokens": 100}))
    assert response.text == "Synthetic local answer."
    assert strata.chat_bodies[-1]["max_tokens"] == DEFAULT_MIN_MAX_TOKENS


def test_not_a_pii_finder(strata):
    target = attached(strata)
    assert target.capabilities.structured_output is False
    with pytest.raises(TargetUnavailable, match="not evaluated"):
        target.chat_json("finder-fast", MESSAGES, {"name": "x", "schema": {"type": "object"}})
    with pytest.raises(TargetUnavailable, match="structured output"):
        target.chat(MESSAGES, response_format={"type": "json_schema", "json_schema": {}})
    with pytest.raises(TargetUnavailable, match="structured output"):
        target.chat(InferenceRequest({"messages": MESSAGES, "response_format": {"type": "json_schema"}}))
    assert strata.chat_bodies == []


def test_one_request_at_a_time(strata):
    strata.delay = 0.6
    target = attached(strata)
    errors: list[Exception] = []
    first = threading.Thread(target=lambda: target.chat(MESSAGES))
    first.start()
    time.sleep(0.2)
    try:
        target.chat(MESSAGES)
    except TargetUnavailable as exc:
        errors.append(exc)
    first.join()
    assert len(errors) == 1 and "one request at a time" in str(errors[0])
    assert len(strata.chat_bodies) == 1
    target.chat(MESSAGES)  # free again afterwards


def test_timeouts_follow_measured_speeds():
    target = StrataTarget(StrataConfig(
        endpoint="http://127.0.0.1:8095/v1", model_id="m",
        expected_prompt_tok_s=100.0, expected_tok_s=10.0, ctx=65536,
    ))
    short = target.request_timeout(200, 8192)
    assert short == pytest.approx(60 + 1.5 * (100 / 100 + 8192 / 10))
    long_prompt = target.request_timeout(64_000, 8192)       # ~32K tokens at 100 tok/s
    assert long_prompt > short + 400
    ceiling = 60 + 1.5 * (65536 / 100 + 8192 / 10)
    assert target.request_timeout(10_000_000, 8192) == pytest.approx(ceiling)
    assert target.request_timeout(0, 1) == 120.0              # floor


def test_per_request_timeout_is_set_and_restored(strata):
    target = attached(strata)
    seen = []
    original = StrataTarget._exchange

    def spy(self, method, path, body=None):
        if method == "POST":
            seen.append(self.timeout_seconds)
        return original(self, method, path, body)

    StrataTarget._exchange = spy
    try:
        target.chat(MESSAGES)
    finally:
        StrataTarget._exchange = original
    assert seen and seen[0] == target.request_timeout(
        len(MESSAGES[0]["content"]), DEFAULT_MIN_MAX_TOKENS,
    )
    assert target.timeout_seconds == target.config.probe_timeout_seconds


def test_api_key_read_from_named_environment_variable(monkeypatch):
    with FakeStrata(api_key="synthetic-strata-key-0001") as fake:
        monkeypatch.delenv("THEMIS_TEST_STRATA_KEY", raising=False)
        target = StrataTarget(StrataConfig(
            endpoint=fake.url, model_id=fake.model, api_key_env="THEMIS_TEST_STRATA_KEY",
        ))
        with pytest.raises(TargetUnavailable, match="model list"):
            target.start()                     # /v1/models needs the key
        monkeypatch.setenv("THEMIS_TEST_STRATA_KEY", "synthetic-strata-key-0001")
        target.start()
        target.chat(MESSAGES)
        assert fake.chat_headers[-1]["Authorization"] == "Bearer synthetic-strata-key-0001"
        assert "synthetic-strata-key-0001" not in json.dumps(target.attach_info)


def test_metrics_are_content_free(strata):
    target = attached(strata)
    metrics = target.metrics()
    assert metrics["vram_free_mib"] == 427
    assert metrics["expert_cache_mib"] == 1127
    assert metrics["state"] == "idle" and metrics["queued"] == 0
    assert CANARY not in json.dumps(metrics)


# -- catalogue -----------------------------------------------------------------

def test_catalogue_strata_entry_rules():
    url = "http://127.0.0.1:8095/v1"
    assert strata_entry(url).external
    assert strata_entry(None).endpoint is None              # unconfigured is allowed
    assert strata_entry(url, file="").file == ""           # no GGUF needed
    with pytest.raises(ValueError, match="answer role"):
        strata_entry(url, roles=("answer", "finder-fast"))
    with pytest.raises(ValueError, match="loopback"):
        strata_entry("http://192.0.2.10:8095/v1")
    with pytest.raises(ValueError, match="both"):
        replace(strata_entry(None), endpoint=url)
    with pytest.raises(ValueError, match="no llama.cpp"):
        strata_entry(url, args=("-ngl", "99"))
    with pytest.raises(ValueError, match="no llama.cpp"):
        strata_entry(url, planner="marian")
    with pytest.raises(ValueError, match="max_tokens"):
        strata_entry(url, min_max_tokens=10**7)
    llama = BUILTINS[0]
    with pytest.raises(ValueError, match="attached runtimes"):
        replace(llama, endpoint=url, served_model="m")


def test_shipped_example_catalogue_configures_strata():
    from themis_shield.profiles import profile_path

    catalogue = Catalogue.load(profile_path("catalogue", "example"))
    entry = catalogue.get(STRATA_ID)
    assert entry.endpoint == "http://127.0.0.1:8095/v1"
    assert entry.served_model == "qwen3.8-flash-next-gyro-s"
    assert entry.roles == ("answer",)


def test_strata_cannot_be_mapped_to_a_finder_role(tmp_path, strata):
    catalogue = Catalogue([*BUILTINS[:3], strata_entry(strata.url)])
    with pytest.raises(ValueError, match="role mapping"):
        Scheduler(
            catalogue, lambda entry: None, lambda: ResourceSnapshot(8, 8, 30, 32, 2, 10, 64),
            lease_path=tmp_path / "gpu.lock",
            role_models={"finder-fast": STRATA_ID, "answer": STRATA_ID},
        )


# -- occupancy ------------------------------------------------------------------

def base_reader(free: float = 7.5, total: float = 8.0):
    return lambda: ResourceSnapshot(total, free, 20, 32, 12, 10, 64)


def test_attached_strata_counts_as_occupied(strata):
    holders = strata_holders([strata_entry(strata.url)])
    assert len(holders) == 1 and holders[0].reserve_gb == 7.9
    snapshot = OccupancyAwareReader(base_reader(free=6.4), holders)()  # WDDM trimmed an idle Strata
    assert snapshot.vram_free_gb == pytest.approx(0.1)
    assert snapshot.external_vram_gb == 7.9
    assert snapshot.external_holders == (STRATA_ID,)
    record = snapshot.record()
    assert record["external_holders"] == [STRATA_ID]
    json.dumps(record)


def test_unloaded_or_absent_strata_is_not_counted(strata):
    strata.loaded = False
    holder = strata_holders([strata_entry(strata.url)])[0]
    snapshot = OccupancyAwareReader(base_reader(), [holder])()
    assert snapshot.vram_free_gb == 7.5 and snapshot.external_holders == ()
    with FakeStrata() as gone:
        url = gone.url
    snapshot = OccupancyAwareReader(base_reader(), strata_holders([strata_entry(url)]))()
    assert snapshot.vram_free_gb == 7.5


def test_unknown_reservation_takes_the_whole_card(strata):
    entry = strata_entry(strata.url, est_vram_gb=None, min_vram_gb=None)
    holder = strata_holders([entry])[0]
    assert holder.occupied_vram_gb() == math.inf
    snapshot = OccupancyAwareReader(base_reader(), [holder])()
    assert snapshot.vram_free_gb == 0.0 and snapshot.external_vram_gb == 8.0


def test_shared_endpoint_counted_once_and_unconfigured_ignored(strata):
    one = strata_entry(strata.url)
    two = replace(one, id="strata-second", est_vram_gb=7.0, min_vram_gb=6.8)
    holders = strata_holders([one, two, strata_entry(None), BUILTINS[0]])
    assert len(holders) == 1
    assert holders[0].reserve_gb == 7.9
    assert holders[0].label == f"{STRATA_ID}+strata-second"


def test_probe_results_are_cached(strata):
    now = [100.0]
    target = StrataTarget(StrataConfig(endpoint=strata.url, model_id=strata.model))
    holder = StrataHolder("s", target, 7.0, cache_seconds=2.0, clock=lambda: now[0])
    holder.occupied_vram_gb()
    holder.occupied_vram_gb()
    assert sum(1 for item in strata.requests if item["path"] == "/health") == 1
    now[0] += 3
    holder.occupied_vram_gb()
    assert sum(1 for item in strata.requests if item["path"] == "/health") == 2


# -- scheduler -------------------------------------------------------------------

class OwnedFake:
    """A llama.cpp stand-in owned by the scheduler."""

    name = "laptop"
    pid = None
    load_ms = 1.0

    def __init__(self, entry, events):
        self.entry, self.events, self.resident, self.last_timings = entry, events, False, {}

    def start(self):
        self.events.append(("start", self.entry.id))
        self.resident = True

    def stop(self):
        self.events.append(("stop", self.entry.id))
        self.resident = False

    def health(self):
        return self.resident

    def chat(self, *args, **kwargs):
        return {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}


def make_scheduler(tmp_path, fake_url, holders_from=None, free=7.5):
    entry = strata_entry(fake_url)
    catalogue = Catalogue([*BUILTINS[:3], entry])
    events = []

    def factory(item):
        if item.runtime == "strata":
            return StrataTarget(StrataConfig.from_entry(item))
        return OwnedFake(item, events)

    reader = OccupancyAwareReader(base_reader(free=free), strata_holders(holders_from or [entry]))
    scheduler = Scheduler(
        catalogue, factory, reader, lease_path=tmp_path / "gpu.lock",
        memory_settle_seconds=0.2,
    )
    return scheduler, events


def test_scheduler_attaches_strata_without_admitting_its_memory(tmp_path, strata):
    scheduler, events = make_scheduler(tmp_path, strata.url)
    try:
        with scheduler.acquire(STRATA_ID) as target:
            assert isinstance(target, StrataTarget) and target.attached
            output = target.chat(MESSAGES)
            assert output["choices"][0]["message"]["content"] == "Synthetic local answer."
        status = scheduler.status()
        assert status["loaded_models"] == [STRATA_ID]
        assert status["resources"]["external_holders"] == [STRATA_ID]
        assert status["timings"][-1]["outcome"] == "complete"
    finally:
        scheduler.close()
    assert events == []  # nothing owned was started


def test_llama_server_not_started_beside_attached_strata(tmp_path, strata):
    scheduler, events = make_scheduler(tmp_path, strata.url)
    try:
        with scheduler.acquire(STRATA_ID):
            pass
        with pytest.raises(NotEnoughMemory, match="attached runtime holds GPU memory"):
            with scheduler.acquire("gemma4-e4b"):
                pass
        assert events == []
        assert not any(item["method"] == "POST" and "stop" in item["path"] for item in strata.requests)
        strata.loaded = False   # the user stopped Strata's model; it no longer holds memory
        time.sleep(2.1)         # probe cache expiry
        with scheduler.acquire("gemma4-e4b"):
            pass
        assert events == [("start", "gemma4-e4b")]
    finally:
        scheduler.close()


def test_scheduler_refuses_unattached_strata(tmp_path):
    with FakeStrata() as gone:
        url = gone.url
    scheduler, _ = make_scheduler(tmp_path, url)
    try:
        with pytest.raises(TargetUnavailable, match="not reachable"):
            with scheduler.acquire(STRATA_ID):
                pass
        assert scheduler.status()["loaded_models"] == []
    finally:
        scheduler.close()


def test_build_runtime_wires_strata_and_occupancy(tmp_path, monkeypatch, strata):
    from themis_shield.runtime import settings as settings_module
    from themis_shield.runtime.settings import RuntimeSettings, build_runtime

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    monkeypatch.setattr(settings_module, "ResourceReader", lambda gpu: base_reader())
    catalogue = Catalogue([*BUILTINS[:3], strata_entry(strata.url)])
    scheduler = build_runtime(
        RuntimeSettings(data_dir=tmp_path / "data", strata_api_key_env="THEMIS_UNSET_KEY_VAR"),
        catalogue,
    )
    try:
        assert isinstance(scheduler.resource_reader, OccupancyAwareReader)
        assert scheduler.launch_planner is None
        with scheduler.acquire(STRATA_ID) as target:
            assert target.config.api_key_env == "THEMIS_UNSET_KEY_VAR"
            assert target.chat(MESSAGES)["choices"][0]["finish_reason"] == "stop"
    finally:
        scheduler.close()
