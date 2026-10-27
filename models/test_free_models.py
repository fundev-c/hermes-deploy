"""Offline tests for the config rewrite in free_models.py (no network).

    ~/.hermes/hermes-agent/venv/bin/python -m pytest -q models/test_free_models.py
"""
import yaml

from free_models import plan_profile

FREE = {"pricing": {"prompt": "0", "completion": "0"}, "context_length": 262144}
PAID = {"pricing": {"prompt": "0.000001", "completion": "0.000002"}, "context_length": 1000000}
CATALOG = {"a:free": FREE, "b:free": FREE, "c:free": FREE, "d:free": FREE, "paid/x": PAID}

TEAM = """display:
  streaming: false
fallback_providers:
- model: gone:free
  provider: nous
- model: b:free
  provider: nous
group_sessions_per_user: true
model:
  base_url: https://inference-api.nousresearch.com/v1
  context_length: 262144
  default: gone-primary
  provider: nous
onboarding:
  seen: {}
"""
DEFAULT_STYLE = TEAM.replace("- model: gone:free\n  provider: nous\n- model: b:free\n  provider: nous\n",
                             "  - model: gone:free\n    provider: nous\n  - model: b:free\n    provider: nous\n")


def run(text, chain):
    return plan_profile(text, yaml.safe_load(text), chain, CATALOG)


def test_dead_primary_takes_shared_ranking_and_keeps_formatting():
    new, changes = run(TEAM, ["a:free", "b:free", "c:free"])
    cfg = yaml.safe_load(new)
    assert cfg["model"]["default"] == "a:free"
    assert [f["model"] for f in cfg["fallback_providers"]] == ["b:free", "c:free"]
    assert new.startswith("display:\n  streaming: false\n") and "onboarding:\n  seen: {}\n" in new
    assert changes[0] == "primary gone-primary -> a:free"


def test_indented_list_style_is_preserved():
    new, _ = run(DEFAULT_STYLE, ["a:free", "b:free"])
    assert "fallback_providers:\n  - model: b:free\n    provider: nous\n" in new
    assert yaml.safe_load(new)["model"]["default"] == "a:free"


def test_working_primary_is_sticky_and_new_models_append():
    text = TEAM.replace("default: gone-primary", "default: c:free")
    new, _ = run(text, ["a:free", "b:free", "c:free", "d:free"])
    cfg = yaml.safe_load(new)
    assert cfg["model"]["default"] == "c:free"
    assert [f["model"] for f in cfg["fallback_providers"]] == ["b:free", "a:free", "d:free"]


def test_paid_primary_and_paid_fallback_are_left_alone():
    text = TEAM.replace("default: gone-primary", "default: paid/x").replace(
        "- model: gone:free\n", "- model: paid/x\n")
    new, changes = run(text, ["a:free", "b:free"])
    cfg = yaml.safe_load(new)
    assert cfg["model"]["default"] == "paid/x"
    assert [f["model"] for f in cfg["fallback_providers"]] == ["b:free", "a:free", "paid/x"]
    assert not changes[0].startswith("primary")


def test_nothing_to_do_returns_none():
    text = TEAM.replace("default: gone-primary", "default: a:free").replace("- model: gone:free\n  provider: nous\n", "")
    assert run(text, ["a:free", "b:free"]) is None
