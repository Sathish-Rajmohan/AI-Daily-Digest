"""
Runs the real AMP validator on every version of the AMP email. Gmail drops an
AMP version that breaks any rule without an error, so this is the check that
matters most when build_amp() changes.

It's skipped unless asked for, because the validator downloads its rules from
cdn.ampproject.org and needs Node. To run it:

    VALIDATE_AMP=1 python -m pytest tests/test_amp_validator.py

On PowerShell: $env:VALIDATE_AMP = "1"
"""

import os
import shutil
import subprocess

import pytest

import digest
from test_amp import DATE, FACT, mixed_results

NPX = shutil.which("npx") or shutil.which("npx.cmd")

pytestmark = pytest.mark.skipif(
    not (os.environ.get("VALIDATE_AMP") and NPX),
    reason="set VALIDATE_AMP=1 (needs Node and a network) to run the AMP validator",
)


@pytest.mark.parametrize("variant", digest.AMP_VARIANTS)
def test_the_amp4email_validator_passes_every_version(tmp_path, variant):
    page = tmp_path / f"{variant}.html"
    page.write_text(digest.build_amp(mixed_results(), DATE, FACT, variant), encoding="utf-8")
    result = subprocess.run(
        [NPX, "--yes", "amphtml-validator", "--html_format", "AMP4EMAIL", "--format", "text", str(page)],
        capture_output=True, text=True, timeout=180,
    )
    assert result.returncode == 0 and "PASS" in result.stdout, result.stdout + result.stderr
