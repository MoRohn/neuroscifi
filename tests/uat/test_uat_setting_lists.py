"""Wave 2 lane X: the next setting cannot be forgotten by the regression checks. The list of "every Wave 1/2 setting"
is written out in several places (this folder's equivalence and rollback tests, scripts/bench_wave1.py WAVE1_OFF_*,
the integrated benchmark's BASE_*, the learning-cycles harness's WAVE1_OFF_WEB). This test fails when a setting of
the config classes (InstanceConfig, WebConfig, the voice settings) that Wave 0 did not have is missing from the lists
the Wave 0 equivalence test switches off and the rollback test rolls back, unless it is named below as a bound (a
number, not a switch); and when another copy of the list gives a setting a different "off" value."""
import dataclasses
import importlib.util
import inspect
from pathlib import Path

from neuroscifi.instance.store import InstanceConfig
from neuroscifi.voice import config as vconfig
from neuroscifi.webcortex.config import WebConfig
import uat.test_uat_rollback as rollback
import uat.test_uat_wave0_equivalence as equivalence

# The settings Wave 0 shipped (its own commit 15b0f03): read there from the same classes
WAVE0_INSTANCE = {"evidence_ttl_seconds", "max_evidence_rows", "min_free_bytes", "ask_budget_per_epoch",
                  "ask_budget_window_seconds", "consolidation", "max_consolidated_rows", "max_thermal_celsius",
                  "graph_rule_migration", "max_imagined_rows", "imagined_lifetime_seconds", "quiet"}
WAVE0_WEB = {"enabled", "autonomous", "pocket_research", "browser_rendering", "news_rendering", "memory_check",
             "daily_renders", "search_browser_user_agent", "openwebsearch_url", "searxng_url", "searxng_engines",
             "daily_provider_queries", "engines", "providers", "budget_ceiling", "daily_autonomous",
             "min_domain_interval", "read_ahead", "max_document_bytes", "cache_max_bytes", "cache_max_age_days",
             "deny_domains", "private_terms", "auto_provisional_min_sources", "auto_provisional_min_confidence",
             "freshness_ttl", "max_open_web_questions", "max_web_questions_per_task", "web_question_stale_days",
             "mcp_research_per_hour", "daily_mcp", "calibrated_forecasts", "tick_admission"}
WAVE0_VOICE = {"mode", "voice", "speed", "retain_audio", "audio_ttl_hours", "transcript_visible", "asr_url",
               "asr_model", "tts_url", "key_file", "personaplex", "personaplex_url", "personaplex_voice", "consent"}
# Numbers Wave 1 added: bounds of a switch, not switches (test_uat_rollback.py's docstring says so)
BOUNDS = {"imagine_max_seconds", "imagine_max_bytes", "broadcast_threshold", "recheck_per_tick", "recheck_daily",
          "reflection_interval_days", "supersession_min_sources"}


def _added():
    instance = {f.name for f in dataclasses.fields(InstanceConfig)} - WAVE0_INSTANCE - BOUNDS
    web = {f.name for f in dataclasses.fields(WebConfig)} - WAVE0_WEB - BOUNDS
    voice = set(vconfig.DEFAULTS) - WAVE0_VOICE - BOUNDS
    return instance, web, voice


def test_every_added_setting_is_switched_off_by_the_wave0_equivalence_test():
    instance, web, _ = _added()
    assert instance - set(equivalence.OFF_INSTANCE) == set(), "add it to OFF_INSTANCE"
    assert web - set(equivalence.OFF_WEB) == set(), "add it to OFF_WEB"


def test_every_added_switch_is_rolled_back_by_a_test_that_names_it():
    instance, web, voice = _added()
    assert (instance | web | voice) - set(rollback.SWITCHES) == set(), "add a rollback test and a SWITCHES entry"
    for name, test in rollback.SWITCHES.items():
        assert getattr(rollback, test.__name__) is test
        assert f'"{name}"' in inspect.getsource(test) or f'"{name}"' in inspect.getsource(rollback), name


def test_the_wave0_settings_are_the_ones_wave0_had():
    """The frozen Wave 0 lists above are not allowed to grow: a setting added later must be classified."""
    assert WAVE0_INSTANCE <= {f.name for f in dataclasses.fields(InstanceConfig)}
    assert WAVE0_WEB <= {f.name for f in dataclasses.fields(WebConfig)}
    assert WAVE0_VOICE <= set(vconfig.DEFAULTS)
    assert len(WAVE0_INSTANCE) == 12 and len(WAVE0_WEB) == 33 and len(WAVE0_VOICE) == 14


def _script(name):
    path = Path(__file__).resolve().parents[2] / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"{name}_lists", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_other_copies_of_the_list_agree_on_what_off_means():
    """Each other copy may hold fewer settings (each names its own scope: Wave 1's, a benchmark's baseline), but a
    setting it holds is switched off to the same value as here."""
    from neuroscifi.acceptance.cognition import cycles, integrated
    bench = _script("bench_wave1")
    off = equivalence.OFF_INSTANCE | equivalence.OFF_WEB
    copies = {"bench_wave1.WAVE1_OFF_INSTANCE": bench.WAVE1_OFF_INSTANCE, "bench_wave1.WAVE1_OFF_WEB":
              bench.WAVE1_OFF_WEB, "cycles.WAVE1_OFF_WEB": cycles.WAVE1_OFF_WEB, "integrated.BASE_WEB":
              integrated.BASE_WEB, "integrated.BASE_INSTANCE": integrated.BASE_INSTANCE}
    for where, values in copies.items():
        differ = {k: (v, off[k]) for k, v in values.items() if k in off and v != off[k]}
        assert differ == {}, (where, differ)
