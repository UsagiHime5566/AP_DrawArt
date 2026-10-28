[CmdletBinding()]
param(
    [string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot),
    [switch]$SkipFrontend,
    [switch]$DefinitionsOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# 固定使用與專案 Python 3.14 相容的版本；雜湊取自 Python 官方發布頁。
$script:PythonVersion = '3.14.7'
$script:PythonSha256 = '9d9eb2709ef81bf5cd30db3c2096bdbc4ea10087c22e62f27d356b36f6ae9649'
$script:NodeVersion = '24.22.1'

function Invoke-CheckedCommand {
    param([string]$Executable, [string[]]$Arguments)

    if (-not (Test-Path -LiteralPath $Executable -PathType Leaf)) {
        throw "Executable not found: $Executable"
    }
    # 原生程式可能使用 stderr 輸出進度；退出碼才是成功與否的依據。
    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        & $Executable @Arguments 2>&1 | ForEach-Object { Write-Host "$_" }
        $commandExitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousPreference
    }
    if ($commandExitCode -ne 0) {
        throw "Command failed with exit code ${commandExitCode}: $Executable $($Arguments -join ' ')"
    }
}

function Test-Python314 {
    param([string]$Executable)

    if (-not (Test-Path -LiteralPath $Executable -PathType Leaf)) {
        return $false
    }
    try {
        & $Executable -I -c "import struct, sys; sys.exit(0 if sys.version_info[:2] == (3, 14) and struct.calcsize('P') == 8 else 1)" *> $null
        return ($LASTEXITCODE -eq 0)
    }
    catch {
        return $false
    }
}

function Get-Sha256 {
    param([string]$Path)

    # 使用 .NET，避免依賴不同 PowerShell 安裝的模組自動載入狀態。
    $stream = [IO.File]::OpenRead($Path)
    $algorithm = [Security.Cryptography.SHA256]::Create()
    try {
        return [BitConverter]::ToString($algorithm.ComputeHash($stream)).Replace('-', '').ToLowerInvariant()
    }
    finally {
        $algorithm.Dispose()
        $stream.Dispose()
    }
}

function Assert-FileHash {
    param([string]$Path, [string]$Expected)

    $actual = Get-Sha256 -Path $Path
    if ($actual -ine $Expected) {
        throw "SHA256 mismatch: $Path. Re-run the installer to download it again."
    }
}

function Get-Download {
    param([string]$Uri, [string]$Destination, [string]$Sha256)

    if ((Test-Path -LiteralPath $Destination -PathType Leaf) -and $Sha256) {
        if ((Get-Sha256 -Path $Destination) -ieq $Sha256) {
            return
        }
    }
    Write-Host "Downloading $Uri"
    $partial = "$Destination.partial"
    Invoke-WebRequest -Uri $Uri -OutFile $partial -UseBasicParsing -TimeoutSec 300
    if ($Sha256) {
        Assert-FileHash -Path $partial -Expected $Sha256
    }
    Move-Item -LiteralPath $partial -Destination $Destination -Force
}

function Find-PythonBase {
    param([string]$Root)

    $candidates = @(
        (Join-Path $Root 'runtime\base-python\python.exe'),
        (Join-Path $env:USERPROFILE 'miniconda3\envs\agent-use-latest\python.exe')
    )
    if ($env:AP_DRAWART_PYTHONW) {
        $candidates = @((Join-Path (Split-Path -Parent $env:AP_DRAWART_PYTHONW) 'python.exe')) + $candidates
    }
    foreach ($registryPath in @(
        'HKCU:\Software\Python\PythonCore\3.14\InstallPath',
        'HKLM:\Software\Python\PythonCore\3.14\InstallPath'
    )) {
        if (Test-Path -LiteralPath $registryPath) {
            $key = Get-Item -LiteralPath $registryPath
            $directory = [string]$key.GetValue('')
            if ($directory) {
                $candidates += Join-Path $directory 'python.exe'
            }
        }
    }
    foreach ($candidate in $candidates) {
        if (Test-Python314 -Executable $candidate) {
            return $candidate
        }
    }
    return $null
}

function Install-PythonBase {
    param([string]$Root)

    $cache = Join-Path $Root 'runtime\downloads'
    $target = Join-Path $Root 'runtime\base-python'
    $fileName = "python-$script:PythonVersion-amd64.exe"
    $installer = Join-Path $cache $fileName
    Get-Download -Uri "https://www.python.org/ftp/python/$script:PythonVersion/$fileName" -Destination $installer -Sha256 $script:PythonSha256
    Write-Host "Installing Python $script:PythonVersion..."
    $arguments = @(
        '/quiet', 'InstallAllUsers=0', "TargetDir=`"$target`"",
        'Include_pip=1', 'Include_launcher=0', 'InstallLauncherAllUsers=0',
        'Include_test=0', 'Include_doc=0', 'Include_tcltk=0',
        'PrependPath=0', 'AppendPath=0', 'Shortcuts=0', 'AssociateFiles=0'
    )
    $process = Start-Process -FilePath $installer -ArgumentList $arguments -Wait -PassThru -WindowStyle Hidden
    if ($process.ExitCode -notin @(0, 3010)) {
        throw "Python installation failed with exit code $($process.ExitCode)."
    }
    $python = Join-Path $target 'python.exe'
    if (-not (Test-Python314 -Executable $python)) {
        throw "Python 3.14 x64 was not found after installation: $python"
    }
    return $python
}

function Ensure-PythonRuntime {
    param([string]$Root)

    $directory = Join-Path $Root 'runtime\python'
    foreach ($relative in @('Scripts\python.exe', 'python.exe')) {
        $python = Join-Path $directory $relative
        $pythonw = Join-Path (Split-Path -Parent $python) 'pythonw.exe'
        if ((Test-Python314 -Executable $python) -and (Test-Path -LiteralPath $pythonw -PathType Leaf)) {
            return $python
        }
    }
    $base = Find-PythonBase -Root $Root
    if (-not $base) {
        $base = Install-PythonBase -Root $Root
    }
    Write-Host 'Creating the project Python environment...'
    Invoke-CheckedCommand -Executable $base -Arguments @('-I', '-m', 'venv', '--copies', $directory)
    $python = Join-Path $directory 'Scripts\python.exe'
    $pythonw = Join-Path $directory 'Scripts\pythonw.exe'
    if (-not ((Test-Python314 -Executable $python) -and (Test-Path -LiteralPath $pythonw -PathType Leaf))) {
        throw "Project Python environment is incomplete: $directory"
    }
    return $python
}

function Ensure-NodeRuntime {
    param([string]$Root)

    $target = Join-Path $Root 'runtime\node'
    $node = Join-Path $target 'node.exe'
    $npm = Join-Path $target 'node_modules\npm\bin\npm-cli.js'
    if ((Test-Path -LiteralPath $node -PathType Leaf) -and (Test-Path -LiteralPath $npm -PathType Leaf)) {
        try {
            $version = & $node --version
            if ($LASTEXITCODE -eq 0 -and "$version" -match '^v24\.') {
                & $node $npm --version *> $null
                if ($LASTEXITCODE -eq 0) {
                    return $node
                }
            }
        }
        catch {
            Write-Host 'Repairing the incomplete Node.js runtime...'
        }
    }

    $cache = Join-Path $Root 'runtime\downloads'
    $folderName = "node-v$script:NodeVersion-win-x64"
    $fileName = "$folderName.zip"
    $baseUri = "https://nodejs.org/dist/v$script:NodeVersion"
    $checksums = Join-Path $cache "node-v$script:NodeVersion-SHASUMS256.txt"
    Get-Download -Uri "$baseUri/SHASUMS256.txt" -Destination $checksums
    $pattern = '^([a-fA-F0-9]{64})\s+\*?' + [regex]::Escape($fileName) + '$'
    $expected = $null
    foreach ($line in Get-Content -LiteralPath $checksums) {
        if ($line -match $pattern) {
            $expected = $Matches[1]
            break
        }
    }
    if (-not $expected) {
        throw "Node.js checksum not found for $fileName"
    }
    $archive = Join-Path $cache $fileName
    Get-Download -Uri "$baseUri/$fileName" -Destination $archive -Sha256 $expected
    $expanded = Join-Path $cache ('expanded-' + [guid]::NewGuid().ToString('N'))
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [IO.Compression.ZipFile]::ExtractToDirectory($archive, $expanded)
    New-Item -ItemType Directory -Path $target -Force | Out-Null
    Get-ChildItem -LiteralPath (Join-Path $expanded $folderName) |
        Copy-Item -Destination $target -Recurse -Force
    Invoke-CheckedCommand -Executable $node -Arguments @('--version')
    return $node
}

function Install-Frontend {
    param([string]$Root, [string]$CodeRoot)

    $frontend = Join-Path $CodeRoot 'frontend'
    $package = Join-Path $frontend 'package.json'
    if (-not (Test-Path -LiteralPath $package -PathType Leaf)) {
        # 已備妥靜態檔案的交付包不必再次安裝前端工具。
        foreach ($page in @('exhibition', 'admin')) {
            if (-not (Test-Path -LiteralPath (Join-Path $CodeRoot "static\$page\index.html") -PathType Leaf)) {
                throw "Neither frontend source nor built frontend is available: $CodeRoot"
            }
        }
        Write-Host 'Using the prebuilt frontend in app/static.'
        return
    }
    if (-not (Test-Path -LiteralPath (Join-Path $frontend 'package-lock.json') -PathType Leaf)) {
        throw 'frontend/package-lock.json is missing. Restore it from the repository.'
    }
    $node = Ensure-NodeRuntime -Root $Root
    $npm = Join-Path (Split-Path -Parent $node) 'node_modules\npm\bin\npm-cli.js'
    $saved = @{}
    foreach ($name in @('PATH', 'NODE_ENV', 'VITE_PAGES', 'VITE_OUT_DIR')) {
        $saved[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
    }
    Push-Location -LiteralPath $frontend
    try {
        $env:PATH = (Split-Path -Parent $node) + [IO.Path]::PathSeparator + $env:PATH
        $env:NODE_ENV = 'development'
        $env:VITE_PAGES = 'exhibition,admin'
        $env:VITE_OUT_DIR = Join-Path $frontend 'dist'
        Invoke-CheckedCommand -Executable $node -Arguments @($npm, 'ci', '--include=dev', '--no-audit', '--no-fund')
        Invoke-CheckedCommand -Executable $node -Arguments @($npm, 'run', 'build')
        foreach ($page in @('exhibition', 'admin')) {
            if (-not (Test-Path -LiteralPath (Join-Path $frontend "dist\$page\index.html") -PathType Leaf)) {
                throw "Frontend build did not produce $page/index.html"
            }
        }
    }
    finally {
        Pop-Location
        foreach ($name in $saved.Keys) {
            [Environment]::SetEnvironmentVariable($name, $saved[$name], 'Process')
        }
    }
}

function Install-Environment {
    param([string]$Root, [switch]$WithoutFrontend)

    if (-not [Environment]::Is64BitOperatingSystem) {
        throw 'This installer requires 64-bit Windows.'
    }
    $Root = (Resolve-Path -LiteralPath $Root).Path
    $codeRoot = $Root
    if (Test-Path -LiteralPath (Join-Path $Root 'app\launcher\__init__.py') -PathType Leaf) {
        $codeRoot = Join-Path $Root 'app'
    }
    $requirements = Join-Path $Root 'requirements.txt'
    if (-not (Test-Path -LiteralPath $requirements -PathType Leaf)) {
        $requirements = Join-Path $codeRoot 'requirements.txt'
    }
    if (-not (Test-Path -LiteralPath $requirements -PathType Leaf)) {
        throw 'requirements.txt is missing. Keep it with the project or app directory.'
    }
    New-Item -ItemType Directory -Path (Join-Path $Root 'runtime\downloads') -Force | Out-Null

    Write-Host '[1/4] Preparing Python 3.14 x64...'
    $python = Ensure-PythonRuntime -Root $Root
    Write-Host "[2/4] Installing backend dependencies from $requirements"
    Invoke-CheckedCommand -Executable $python -Arguments @('-I', '-m', 'ensurepip', '--upgrade')
    Invoke-CheckedCommand -Executable $python -Arguments @('-I', '-m', 'pip', 'install', '--disable-pip-version-check', '-r', $requirements)
    Invoke-CheckedCommand -Executable $python -Arguments @('-I', '-m', 'pip', 'check')
    Write-Host '[3/4] Verifying backend imports...'
    Invoke-CheckedCommand -Executable $python -Arguments @('-I', '-c', 'import fastapi, uvicorn, multipart, PIL, openai, psutil, httpx')
    if (-not $WithoutFrontend) {
        Write-Host '[4/4] Installing frontend dependencies and building local pages...'
        Install-Frontend -Root $Root -CodeRoot $codeRoot
    }
    else {
        Write-Host '[4/4] Frontend skipped by request.'
    }
    Write-Host "Installation complete. Python: $python"
}

if ($DefinitionsOnly) {
    return
}

$transcriptStarted = $false
$installLock = $null
$result = 1
try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $ProgressPreference = 'SilentlyContinue'
    $ProjectRoot = (Resolve-Path -LiteralPath $ProjectRoot).Path
    $runtime = Join-Path $ProjectRoot 'runtime'
    New-Item -ItemType Directory -Path $runtime -Force | Out-Null
    $installLock = [IO.File]::Open(
        (Join-Path $runtime 'install.lock'),
        [IO.FileMode]::OpenOrCreate,
        [IO.FileAccess]::ReadWrite,
        [IO.FileShare]::None
    )
    Start-Transcript -Path (Join-Path $runtime 'install-environment.txt') -Append | Out-Null
    $transcriptStarted = $true
    Install-Environment -Root $ProjectRoot -WithoutFrontend:$SkipFrontend
    $result = 0
}
catch {
    Write-Host "[ERROR] $($_.Exception.Message)" -ForegroundColor Red
    Write-Host 'Close the exhibition before retrying. No artwork, settings, or runtime logs are removed.'
}
finally {
    if ($transcriptStarted) {
        Stop-Transcript | Out-Null
    }
    if ($null -ne $installLock) {
        $installLock.Dispose()
    }
}
exit $result

