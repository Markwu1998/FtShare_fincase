"""Exercise PowerShell failure propagation without any provider calls or tasks."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


@unittest.skipUnless(shutil.which("powershell"), "Windows PowerShell is required")
class UpdateScriptTest(unittest.TestCase):
    def run_scenario(self, scenario):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scripts = root / "scripts"
            scripts.mkdir()
            source = Path(__file__).resolve().parents[1] / "scripts/update-sources.ps1"
            shutil.copyfile(source, scripts / source.name)
            (scripts / "scenario.json").write_text(json.dumps(scenario), encoding="utf-8")
            (scripts / "python-backend.ps1").write_text('''param([string]$Mode, [string[]]$Arguments)
$scenario = Get-Content "$PSScriptRoot/scenario.json" -Raw | ConvertFrom-Json
$source = if ($Mode -eq 'history') { $Arguments[1] } else { 'crowding' }
Add-Content "$PSScriptRoot/calls.txt" $source
exit $scenario.$source
''', encoding="utf-8")
            result = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                                     str(scripts / source.name)], capture_output=True, timeout=30)
            calls = (scripts / "calls.txt").read_text().splitlines()
            return result.returncode, calls

    def test_required_source_failure_survives_optional_success_and_crowding_runs(self):
        code, calls = self.run_scenario({"rqdata": 23, "ddb": 0, "crowding": 0})
        self.assertEqual(code, 1)
        self.assertEqual(calls, ["rqdata", "ddb", "crowding"])

    def test_optional_source_failure_is_a_degradation(self):
        code, _ = self.run_scenario({"rqdata": 0, "ddb": 23, "crowding": 0})
        self.assertEqual(code, 0)

    def test_crowding_failure_is_reported(self):
        code, _ = self.run_scenario({"rqdata": 0, "ddb": 0, "crowding": 23})
        self.assertEqual(code, 1)
