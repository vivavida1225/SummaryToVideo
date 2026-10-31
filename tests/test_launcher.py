import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows PowerShell launcher')
@pytest.mark.parametrize('case', ['wrong_version', 'missing_version', 'same_version', 'wrong_home', 'broken', 'missing'])
def test_launcher_recreates_incompatible_venv_and_reuses_compatible_one(tmp_path, case):
    script = Path(__file__).with_name('launcher_python_environment.ps1')
    result = subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass',
                             '-File', str(script), '-FixtureRoot', str(tmp_path), '-Case', case],
                            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
