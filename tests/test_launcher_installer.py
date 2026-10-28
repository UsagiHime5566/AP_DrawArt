"""環境安裝入口：驗證 Windows 腳本與模擬安裝流程，不下載或安裝套件。"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = PROJECT_ROOT / "launcher" / "install-environment.ps1"
POWERSHELL = shutil.which("powershell.exe")
pytestmark = pytest.mark.skipif(sys.platform != "win32" or not POWERSHELL, reason="需要 Windows PowerShell")


def ps_literal(value: Path | str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def run_ps(tmp_path: Path, body: str) -> subprocess.CompletedProcess[str]:
    script = tmp_path / "installer-check.ps1"
    script.write_text(
        "[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)\n"
        "$ErrorActionPreference = 'Stop'\n"
        f". {ps_literal(SCRIPT)} -DefinitionsOnly\n"
        + body,
        encoding="utf-8-sig",
    )
    env = dict(os.environ)
    env.pop("PSModulePath", None)
    return subprocess.run(
        [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
        env=env,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=40,
        check=False,
    )


def assert_success(result: subprocess.CompletedProcess[str]) -> None:
    assert result.returncode == 0, result.stdout + result.stderr


def test_installer_powershell51_syntax(tmp_path: Path) -> None:
    result = run_ps(
        tmp_path,
        f"""
$tokens = $null
$parseErrors = $null
[Management.Automation.Language.Parser]::ParseFile({ps_literal(SCRIPT)}, [ref]$tokens, [ref]$parseErrors) | Out-Null
if ($parseErrors.Count -gt 0) {{ throw ($parseErrors | Out-String) }}
""",
    )
    assert_success(result)


@pytest.mark.parametrize("relative", ["Scripts", "."])
def test_existing_python_runtime_is_reused(tmp_path: Path, relative: str) -> None:
    root = tmp_path / "展覽 runtime with spaces"
    executable = root / "runtime" / "python" / relative / "python.exe"
    executable.parent.mkdir(parents=True)
    executable.touch()
    executable.with_name("pythonw.exe").touch()
    result = run_ps(
        tmp_path,
        f"""
function Test-Python314 {{ param([string]$Executable) return (Test-Path -LiteralPath $Executable -PathType Leaf) }}
function Find-PythonBase {{ throw 'Existing runtime must not be replaced' }}
$selected = Ensure-PythonRuntime -Root {ps_literal(root)}
if ([IO.Path]::GetFullPath($selected) -ne [IO.Path]::GetFullPath({ps_literal(executable)})) {{ throw $selected }}
""",
    )
    assert_success(result)


def test_checksum_mismatch_stops_installation(tmp_path: Path) -> None:
    archive = tmp_path / "archive.zip"
    archive.write_bytes(b"corrupt download")
    result = run_ps(
        tmp_path,
        f"""
$rejected = $false
try {{ Assert-FileHash -Path {ps_literal(archive)} -Expected ('0' * 64) }}
catch {{
    $rejected = $_.Exception.Message -like '*SHA256 mismatch*'
    if (-not $rejected) {{ throw ('Unexpected verification error: ' + $_.Exception.Message) }}
}}
if (-not $rejected) {{ throw 'Corrupt download was accepted' }}
""",
    )
    assert_success(result)


def test_native_nonzero_exit_is_failure(tmp_path: Path) -> None:
    result = run_ps(
        tmp_path,
        f"""
$rejected = $false
try {{ Invoke-CheckedCommand -Executable {ps_literal(sys.executable)} -Arguments @('-I', '-c', 'import sys; sys.exit(7)') }}
catch {{ $rejected = $_.Exception.Message -like '*exit code 7*' }}
if (-not $rejected) {{ throw 'Native command failure was ignored' }}
""",
    )
    assert_success(result)


def test_node_archive_is_extracted_to_project_runtime(tmp_path: Path) -> None:
    root = tmp_path / "展覽 node with spaces"
    (root / "runtime" / "downloads").mkdir(parents=True)
    archive = tmp_path / "node-fixture.zip"
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("node-v24.22.1-win-x64/node.exe", "node-fixture")
        package.writestr("node-v24.22.1-win-x64/node_modules/npm/bin/npm-cli.js", "npm-fixture")
    result = run_ps(
        tmp_path,
        f"""
function Get-Download {{
    param([string]$Uri, [string]$Destination, [string]$Sha256)
    if ($Uri.EndsWith('/SHASUMS256.txt')) {{
        $checksum = Get-Sha256 -Path {ps_literal(archive)}
        Set-Content -LiteralPath $Destination -Value ($checksum + '  node-v24.22.1-win-x64.zip')
    }}
    else {{
        Copy-Item -LiteralPath {ps_literal(archive)} -Destination $Destination
        Assert-FileHash -Path $Destination -Expected $Sha256
    }}
}}
function Invoke-CheckedCommand {{ param([string]$Executable, [string[]]$Arguments) }}
$node = Ensure-NodeRuntime -Root {ps_literal(root)}
if (-not (Test-Path -LiteralPath $node -PathType Leaf)) {{ throw 'node.exe missing' }}
""",
    )
    assert_success(result)
    assert (root / "runtime/node/node.exe").read_text() == "node-fixture"
    assert (root / "runtime/node/node_modules/npm/bin/npm-cli.js").read_text() == "npm-fixture"


@pytest.mark.parametrize("fail_pip", [False, True])
def test_backend_install_checks_failures_and_preserves_data(tmp_path: Path, fail_pip: bool) -> None:
    root = tmp_path / "展覽 root with spaces"
    root.mkdir()
    (root / "requirements.txt").write_text("# fixture\n", encoding="utf-8")
    for folder in ("data", "logs", "config"):
        (root / folder).mkdir()
        (root / folder / "keep.txt").write_text("keep", encoding="utf-8")
    fail_value = "$true" if fail_pip else "$false"
    result = run_ps(
        tmp_path,
        f"""
$script:Commands = [Collections.Generic.List[object]]::new()
$script:FrontendCalled = $false
function Ensure-PythonRuntime {{ param([string]$Root) return 'mock-python.exe' }}
function Invoke-CheckedCommand {{
    param([string]$Executable, [string[]]$Arguments)
    $script:Commands.Add(@($Arguments))
    if ({fail_value} -and ($Arguments -contains 'install')) {{ throw 'Simulated pip failure' }}
}}
function Install-Frontend {{ param([string]$Root, [string]$CodeRoot) $script:FrontendCalled = $true }}
$failed = $false
try {{ Install-Environment -Root {ps_literal(root)} }}
catch {{ $failed = $_.Exception.Message -eq 'Simulated pip failure' }}
if ($failed -ne {fail_value}) {{ throw 'Unexpected install result' }}
if ($script:FrontendCalled -eq {fail_value}) {{ throw 'Frontend stage ran at the wrong time' }}
if (-not {fail_value} -and $script:Commands.Count -ne 4) {{ throw 'Missing backend validation' }}
""",
    )
    assert_success(result)
    for folder in ("data", "logs", "config"):
        assert (root / folder / "keep.txt").read_text(encoding="utf-8") == "keep"


@pytest.mark.parametrize("fail_build", [False, True])
def test_frontend_build_uses_lockfile_and_restores_environment(tmp_path: Path, fail_build: bool) -> None:
    root = tmp_path / "展覽 frontend with spaces"
    frontend = root / "frontend"
    frontend.mkdir(parents=True)
    (frontend / "package.json").write_text("{}", encoding="utf-8")
    (frontend / "package-lock.json").write_text("{}", encoding="utf-8")
    fail_value = "$true" if fail_build else "$false"
    result = run_ps(
        tmp_path,
        f"""
$script:Commands = [Collections.Generic.List[object]]::new()
$env:VITE_PAGES = 'original-pages'
$env:VITE_OUT_DIR = 'original-output'
$env:NODE_ENV = 'production'
$originalPath = $env:PATH
$originalLocation = (Get-Location).Path
function Ensure-NodeRuntime {{ param([string]$Root) return (Join-Path $Root 'runtime\\node\\node.exe') }}
function Invoke-CheckedCommand {{
    param([string]$Executable, [string[]]$Arguments)
    $script:Commands.Add(@($Arguments))
    if ($Arguments -contains 'build') {{
        if ({fail_value}) {{ throw 'Simulated build failure' }}
        # 依安裝腳本指定的 VITE_PAGES 產生頁面，確認它要求的是 v2.0 的展覽與後臺頁
        foreach ($page in ($env:VITE_PAGES -split ',')) {{
            $pageDir = Join-Path $env:VITE_OUT_DIR $page
            New-Item -ItemType Directory -Path $pageDir -Force | Out-Null
            Set-Content -LiteralPath (Join-Path $pageDir 'index.html') -Value '<html></html>'
        }}
    }}
}}
$failed = $false
try {{ Install-Frontend -Root {ps_literal(root)} -CodeRoot {ps_literal(root)} }}
catch {{ $failed = $_.Exception.Message -eq 'Simulated build failure' }}
if ($failed -ne {fail_value}) {{ throw 'Unexpected build result' }}
if ($script:Commands.Count -ne 2 -or -not ($script:Commands[0] -contains 'ci')) {{ throw 'Expected npm ci before build' }}
if ($env:VITE_PAGES -ne 'original-pages' -or $env:VITE_OUT_DIR -ne 'original-output') {{ throw 'Vite environment leaked' }}
if ($env:NODE_ENV -ne 'production' -or $env:PATH -ne $originalPath) {{ throw 'Process environment leaked' }}
if ((Get-Location).Path -ne $originalLocation) {{ throw 'Working directory leaked' }}
""",
    )
    assert_success(result)


def test_installer_bat_accepts_unicode_and_spaces(tmp_path: Path) -> None:
    root = tmp_path / "展覽 with spaces"
    (root / "launcher").mkdir(parents=True)
    shutil.copyfile(PROJECT_ROOT / "install-environment.bat", root / "install-environment.bat")
    shutil.copyfile(SCRIPT, root / "launcher" / SCRIPT.name)
    env = dict(os.environ, AP_DRAWART_INSTALL_NO_PAUSE="1")
    result = subprocess.run(
        f'cmd.exe /d /s /c ""{root / "install-environment.bat"}" -DefinitionsOnly"',
        env=env,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (root / "runtime").exists()


@pytest.mark.parametrize("venv", [False, True])
def test_start_bat_selects_installed_and_legacy_runtimes(tmp_path: Path, venv: bool) -> None:
    root = tmp_path / "展覽 start with spaces"
    runtime = root / "runtime" / "python"
    runtime.mkdir(parents=True)
    (runtime / "pythonw.exe").touch()
    if venv:
        (runtime / "Scripts").mkdir()
        (runtime / "Scripts" / "pythonw.exe").touch()

    # 在找到執行檔後輸出選擇結果，不啟動實際背景程序或視窗。
    source = (PROJECT_ROOT / "start-exhibition.bat").read_text(encoding="utf-8")
    prefix = source.split("\n:found_python\n", 1)[0]
    bat = root / "start-exhibition.bat"
    bat.write_text(prefix + "\n:found_python\necho SELECTED=%PYW%\nexit /b 0\n", encoding="utf-8")
    result = subprocess.run(
        f'cmd.exe /d /s /c ""{bat}""',
        capture_output=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    suffix = b"\\Scripts\\pythonw.exe" if venv else b"\\runtime\\python\\pythonw.exe"
    assert suffix in result.stdout


@pytest.mark.parametrize("venv", [False, True])
def test_stop_bat_selects_console_python(tmp_path: Path, venv: bool) -> None:
    root = tmp_path / "展覽 stop with spaces"
    runtime = root / "runtime" / "python"
    runtime.mkdir(parents=True)
    (runtime / "python.exe").touch()
    if venv:
        (runtime / "Scripts").mkdir()
        (runtime / "Scripts" / "python.exe").touch()

    # 結束 BAT 用有主控台的 python.exe 顯示結果；只檢查選擇結果，不實際結束任何程序。
    source = (PROJECT_ROOT / "stop-exhibition.bat").read_text(encoding="utf-8")
    prefix = source.replace("\r\n", "\n").split("\n:found_python\n", 1)[0]
    bat = root / "stop-exhibition.bat"
    bat.write_text(prefix + "\n:found_python\necho SELECTED=%PY%\nexit /b 0\n", encoding="utf-8")
    result = subprocess.run(
        f'cmd.exe /d /s /c ""{bat}""',
        capture_output=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    suffix = b"\\Scripts\\python.exe" if venv else b"\\runtime\\python\\python.exe"
    assert suffix in result.stdout
