"""Voice panel source links go through safeUrl (http/https only), run under node without a browser (L7)."""
from pathlib import Path
import json
import shutil
import subprocess

import pytest

UI = Path(__file__).resolve().parents[2] / "src" / "neuroscifi" / "voice" / "web" / "voice-ui.mjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_voice_ui_source_links_are_safe_urls():
    script = (f"import {{ safeUrl }} from {json.dumps(UI.as_uri())};\n"
              "const cases = ['javascript:alert(1)', 'data:text/html,x', 'vbscript:x', ' javascript:x', 'not a url',"
              " 'https://example.org/a?b=1', 'http://example.com/'];\n"
              "console.log(JSON.stringify(cases.map((u) => safeUrl(u))));")
    out = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == [None, None, None, None, None, "https://example.org/a?b=1", "http://example.com/"]
    text = UI.read_text()
    assert "href: s.url" not in text and "href: safeUrl(s.url)" in text
