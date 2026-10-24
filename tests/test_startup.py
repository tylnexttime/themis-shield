"""`themis serve` startup refusals: reason codes, value-free messages, non-zero exit."""

from __future__ import annotations

import socket
from pathlib import Path
from types import SimpleNamespace

import pytest

from themis_shield import startup
from themis_shield.dpapi import ProtectionError
from themis_shield.protectors import PassphraseMismatch, PassphraseProtector
from themis_shield.startup import REASONS, StartupRefused
from themis_shield.vault import Vault
from themis_shield.web.__main__ import main as serve
from themis_shield.web.app import read_gate_token

SECRET_LOOKING = "SYNTHETIC-VALUE-THAT-MUST-NOT-BE-PRINTED-0042"
GATE = (
    '[gate]\nsynthetic_only = {synthetic}\nrequester_identity = "{requester}"\n'
    'operator_identity = "{operator}"\n'
)


def _config(tmp_path, *, synthetic="true", requester="synthetic-requester-01",
            operator="synthetic-operator-01", extra=""):
    path = tmp_path / "gate.toml"
    path.write_text(
        GATE.format(synthetic=synthetic, requester=requester, operator=operator) + extra,
        encoding="utf-8",
    )
    return path


def _code(call):
    with pytest.raises(StartupRefused) as caught:
        call()
    return caught.value


def test_every_reason_has_a_one_line_value_free_explanation():
    for code, text in REASONS.items():
        assert code.isupper() and "\n" not in text and text.endswith(".")
        assert StartupRefused(code).message.startswith(f"Themis startup refused [{code}]: ")
    with pytest.raises(ValueError):
        StartupRefused("NOT_A_REASON")


def test_missing_config(tmp_path):
    assert _code(lambda: startup.check_config(tmp_path / "absent.toml")).code == "CONFIG_MISSING"


def test_unreadable_config(tmp_path):
    path = tmp_path / "bad.toml"
    path.write_text(f'[gate\nsynthetic_only = "{SECRET_LOOKING}"', encoding="utf-8")
    refusal = _code(lambda: startup.check_config(path))
    assert refusal.code == "CONFIG_UNREADABLE" and SECRET_LOOKING not in refusal.message


def test_incomplete_config(tmp_path):
    path = tmp_path / "gate.toml"
    path.write_text("[cloud]\nmodel = \"m\"\n", encoding="utf-8")
    assert _code(lambda: startup.check_config(path)).code == "CONFIG_INCOMPLETE"


@pytest.mark.parametrize("value", ["false", '"true"', "1"])
def test_synthetic_only_must_be_true(tmp_path, value):
    path = _config(tmp_path, synthetic=value, requester=SECRET_LOOKING)
    refusal = _code(lambda: startup.check_config(path))
    assert refusal.code == "SYNTHETIC_ONLY_REQUIRED" and SECRET_LOOKING not in refusal.message


def test_operator_must_differ_from_requester(tmp_path):
    path = _config(tmp_path, requester=SECRET_LOOKING, operator=SECRET_LOOKING)
    refusal = _code(lambda: startup.check_config(path))
    assert refusal.code == "OPERATOR_IS_REQUESTER" and SECRET_LOOKING not in refusal.message


def test_gate_token_missing_names_the_fix(tmp_path):
    refusal = _code(lambda: startup.check_token(tmp_path / "gate-token", read_gate_token))
    assert refusal.code == "GATE_TOKEN_MISSING"
    assert "themis serve --provision-token" in refusal.message and "--open" in refusal.message


def test_port_in_use():
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    try:
        port = listener.getsockname()[1]
        assert _code(lambda: startup.check_port(port)).code == "PORT_IN_USE"
    finally:
        listener.close()


def test_free_port_passes():
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    startup.check_port(port)


@pytest.mark.parametrize("error, code", [
    (ProtectionError("no vault protector configured for this platform"), "VAULT_PROTECTOR_UNAVAILABLE"),
    (PassphraseMismatch("vault passphrase does not match"), "VAULT_KEY_REJECTED"),
    (FileNotFoundError("catalogue profile not found: x"), "CONFIG_REFERENCE_MISSING"),
    (KeyError("requester_identity"), "CONFIG_INCOMPLETE"),
    (ValueError("unknown [hooks] settings"), "CONFIG_INVALID"),
])
def test_build_failures_map_to_reason_codes(error, code):
    assert startup.classify(error).code == code


def test_unexpected_failure_reports_only_the_exception_class():
    refusal = startup.classify(RuntimeError(SECRET_LOOKING))
    assert refusal.code == "STARTUP_FAILED" and refusal.detail == "RuntimeError"
    assert SECRET_LOOKING not in refusal.message


def test_wrong_vault_passphrase_is_refused_at_startup(tmp_path):
    path = tmp_path / "vault.db"
    Vault(path, protector=PassphraseProtector("synthetic passphrase one")).close()
    vault = Vault(path, protector=PassphraseProtector("synthetic passphrase two"))
    try:
        refusal = _code(lambda: startup.check_vault(SimpleNamespace(vault=vault)))
        assert refusal.code == "VAULT_KEY_REJECTED"
    finally:
        vault.close()
    vault = Vault(path, protector=PassphraseProtector("synthetic passphrase one"))
    try:
        startup.check_vault(SimpleNamespace(vault=vault))
    finally:
        vault.close()


def _router(tmp_path, *, tracer=True, engine=True, model=True, runtime="llamacpp"):
    tracer_dir = tmp_path / "pii-tracer"
    tracer_dir.mkdir(exist_ok=True)
    if tracer:
        (tracer_dir / "config.json").write_text("{}", encoding="utf-8")
    engine_path = tmp_path / "engine" / "llama-server"
    models = tmp_path / "models"
    models.mkdir(exist_ok=True)
    if engine:
        engine_path.parent.mkdir(exist_ok=True)
        engine_path.write_bytes(b"")
    if model:
        (models / "finder.gguf").write_bytes(b"")
    entry = SimpleNamespace(runtime=runtime, file="finder.gguf")
    return SimpleNamespace(
        targets=SimpleNamespace(tracer_model_directory=str(tracer_dir)),
        runtime_settings=SimpleNamespace(engine_for=lambda _: engine_path, models_dir=models),
        runtime=SimpleNamespace(
            role_models={"finder-fast": "m", "finder-thorough": "m", "answer": "m"},
            catalogue=SimpleNamespace(get=lambda _: entry),
        ),
    )


def test_model_checks(tmp_path):
    present = lambda name: True  # noqa: E731
    startup.check_models(_router(tmp_path), module_present=present)
    assert _code(lambda: startup.check_models(
        _router(tmp_path), module_present=lambda name: name != "transformers",
    )).code == "TRACER_EXTRA_MISSING"
    (tmp_path / "a").mkdir()
    assert _code(lambda: startup.check_models(
        _router(tmp_path / "a", tracer=False), module_present=present,
    )).code == "TRACER_WEIGHTS_MISSING"
    (tmp_path / "b").mkdir()
    assert _code(lambda: startup.check_models(
        _router(tmp_path / "b", engine=False), module_present=present,
    )).code == "ENGINE_MISSING"
    (tmp_path / "c").mkdir()
    refusal = _code(lambda: startup.check_models(_router(tmp_path / "c", model=False), module_present=present))
    assert refusal.code == "MODEL_MISSING" and refusal.detail == "finder.gguf"
    # Attached back ends (no llama.cpp process) are not checked for an engine or a file.
    (tmp_path / "d").mkdir()
    startup.check_models(
        _router(tmp_path / "d", engine=False, model=False, runtime="strata"), module_present=present,
    )


def test_serve_prints_reason_and_exits_non_zero(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    with pytest.raises(SystemExit) as caught:
        serve(["--config", str(tmp_path / "absent.toml")])
    assert caught.value.code == 2
    assert "[CONFIG_MISSING]" in capsys.readouterr().err
    path = _config(tmp_path, requester=SECRET_LOOKING)
    with pytest.raises(SystemExit) as caught:
        serve(["--config", str(path)])
    assert caught.value.code == 2
    captured = capsys.readouterr()
    assert "[GATE_TOKEN_MISSING]" in captured.err
    assert SECRET_LOOKING not in captured.err + captured.out
