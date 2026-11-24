"""Three modules resolved the home folder at import and raised where the account has none (a unit with a bare uid;
open-items round, lane D)."""
from pathlib import Path

import pytest

from neuroscifi import paths
from neuroscifi.resources import idle

ROOT = Path(__file__).resolve().parents[1]


# ---- no home folder ------------------------------------------------------------------------------------------------

def test_modules_import_and_read_their_defaults_without_a_home_folder(monkeypatch):
    """Path.home() raised at import where HOME is unset and the uid has no account (a unit with a bare uid)."""
    import importlib
    import pwd
    monkeypatch.delenv("HOME", raising=False)
    monkeypatch.delenv("NSF_INFERENCE_DIR", raising=False)
    monkeypatch.delenv("NSF_INFERENCE_KEY_FILE", raising=False)
    monkeypatch.setattr(pwd, "getpwuid", lambda uid: (_ for _ in ()).throw(KeyError(uid)))
    assert paths.home() == paths.NOWHERE
    with pytest.raises(RuntimeError):
        Path.home()                                # what the modules called at import
    from neuroscifi.inference import config as iconfig
    from neuroscifi.pocket import tls
    from neuroscifi.voice import config as vconfig
    try:
        for module in (iconfig, vconfig, tls):
            importlib.reload(module)
        assert iconfig.DEFAULT_KEY_FILE == paths.NOWHERE / ".config" / "neuroscifi" / "inference" / "api_key"
        assert iconfig.key_path(iconfig.InferenceConfig()) is None            # no such file: no key, no error
        assert str(vconfig.DEFAULT_KEY_FILE).startswith("/nonexistent/")
        assert str(tls.DEFAULT_CA_DIR).startswith("/nonexistent/")
        assert idle.memory_settings()["embed_port"] == 18441
    finally:
        monkeypatch.undo()
        for module in (iconfig, vconfig, tls):
            importlib.reload(module)


def test_home_is_the_environments_then_the_accounts(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert paths.home() == tmp_path
    assert paths.home({"HOME": "/somewhere"}) == Path("/somewhere")
    monkeypatch.delenv("HOME")
    assert paths.home() == Path(__import__("pwd").getpwuid(__import__("os").getuid()).pw_dir)


def test_the_inference_key_default_is_read_when_asked_not_at_import(tmp_path, monkeypatch):
    from neuroscifi.inference import config as iconfig
    monkeypatch.delenv("NSF_INFERENCE_KEY_FILE", raising=False)
    monkeypatch.setenv("NSF_INFERENCE_DIR", str(tmp_path))
    assert iconfig.DEFAULT_DIR == tmp_path and iconfig.key_path(iconfig.InferenceConfig()) is None
    (tmp_path / "api_key").write_text("k\n")
    assert iconfig.key_path(iconfig.InferenceConfig()) == tmp_path / "api_key"
    assert iconfig.key_path(iconfig.InferenceConfig(), "embed") is None       # a memory model never falls back
    monkeypatch.setattr(iconfig, "DEFAULT_KEY_FILE", tmp_path / "other", raising=False)   # an override still wins
    assert iconfig.key_path(iconfig.InferenceConfig()) is None
