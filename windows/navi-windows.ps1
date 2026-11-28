param(
    [ValidateSet("start", "stop", "status", "setup")]
    [string]$Operation = "start"
)

$ErrorActionPreference = "Stop"
[Console]::InputEncoding = New-Object System.Text.UTF8Encoding $false
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$env:PYTHONUTF8 = "1"
$Root = Split-Path -Parent $PSScriptRoot
$Backend = Join-Path $Root "backend"
$Venv = Join-Path $Root ".venv-win"
$Python = Join-Path $Venv "Scripts\python.exe"
$RuntimeStamp = Join-Path $Venv ".navi-uv-lock"
$LockFile = Join-Path $Root "uv.lock"
$Frontend = Join-Path $Root "frontend"
$FrontendIndex = Join-Path $Frontend "dist\index.html"
$NodeRoot = Join-Path $Root ".tools"
$NodeVersion = "22.12.0"

function Find-Uv {
    $command = Get-Command uv.exe -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }
    $userUv = Join-Path $env:USERPROFILE ".local\bin\uv.exe"
    if (Test-Path -LiteralPath $userUv) {
        return $userUv
    }
    return $null
}

function Ensure-Uv {
    $uv = Find-Uv
    if ($uv) {
        return $uv
    }
    Write-Host "Installing uv..."
    Invoke-RestMethod -Uri "https://astral.sh/uv/install.ps1" | Invoke-Expression
    $uv = Find-Uv
    if (-not $uv) {
        throw "uv installation did not produce uv.exe."
    }
    return $uv
}

function Ensure-Python {
    $lockHash = if (Test-Path -LiteralPath $LockFile) { (Get-FileHash -LiteralPath $LockFile -Algorithm SHA256).Hash } else { "" }
    if (Test-Path -LiteralPath $Python) {
        $savedHash = if (Test-Path -LiteralPath $RuntimeStamp) { [IO.File]::ReadAllText($RuntimeStamp).Trim() } else { "" }
        if ($savedHash -eq $lockHash) {
            return
        }
        if (-not (Find-Uv)) {
            return
        }
    }
    $uv = Find-Uv
    if (-not $uv) {
        $uv = Ensure-Uv
    }
    Write-Host "Preparing the Python environment..."
    $previousEnvironment = $env:UV_PROJECT_ENVIRONMENT
    $env:UV_PROJECT_ENVIRONMENT = $Venv
    Push-Location $Root
    try {
        & $uv sync --locked --python 3.12
        if ($LASTEXITCODE -ne 0) {
            throw "Python dependency setup failed."
        }
    }
    finally {
        Pop-Location
        $env:UV_PROJECT_ENVIRONMENT = $previousEnvironment
    }
    if (-not (Test-Path -LiteralPath $Python)) {
        throw "Python environment was not created at $Venv."
    }
    [IO.File]::WriteAllText($RuntimeStamp, $lockHash, (New-Object Text.UTF8Encoding $false))
}

function Node-Version-Is-Supported([string]$NodeDirectory) {
    $node = Join-Path $NodeDirectory "node.exe"
    if (-not (Test-Path -LiteralPath $node)) {
        return $false
    }
    try {
        $version = & $node -p "process.versions.node"
        $parts = $version.Trim().Split(".")
        return ([int]$parts[0] -gt 22) -or ([int]$parts[0] -eq 22 -and [int]$parts[1] -ge 12)
    }
    catch {
        return $false
    }
}

function Find-Node {
    $command = Get-Command node.exe -ErrorAction SilentlyContinue
    if ($command) {
        $directory = Split-Path -Parent $command.Source
        if (Node-Version-Is-Supported $directory) {
            return $directory
        }
    }
    $architecture = "x64"
    if ([System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture -eq "Arm64") {
        $architecture = "arm64"
    }
    $directory = Join-Path $NodeRoot "node-v$NodeVersion-win-$architecture"
    if (Node-Version-Is-Supported $directory) {
        return $directory
    }
    return $null
}

function Ensure-Node {
    $directory = Find-Node
    if ($directory) {
        return $directory
    }
    $architecture = "x64"
    if ([System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture -eq "Arm64") {
        $architecture = "arm64"
    }
    $folder = "node-v$NodeVersion-win-$architecture"
    $directory = Join-Path $NodeRoot $folder
    $archive = Join-Path $env:TEMP "$folder.zip"
    New-Item -ItemType Directory -Force -Path $NodeRoot | Out-Null
    Write-Host "Downloading Node.js $NodeVersion for the one-time frontend build..."
    Invoke-WebRequest -UseBasicParsing -Uri "https://nodejs.org/dist/v$NodeVersion/$folder.zip" -OutFile $archive
    Expand-Archive -LiteralPath $archive -DestinationPath $NodeRoot -Force
    Remove-Item -LiteralPath $archive -Force
    if (-not (Node-Version-Is-Supported $directory)) {
        throw "Node.js installation failed."
    }
    return $directory
}

function Ensure-Frontend {
    if (Test-Path -LiteralPath $FrontendIndex) {
        return
    }
    $nodeDirectory = Ensure-Node
    $npm = Join-Path $nodeDirectory "npm.cmd"
    if (-not (Test-Path -LiteralPath $npm)) {
        throw "npm was not found in $nodeDirectory."
    }
    $env:Path = "$nodeDirectory;$env:Path"
    Write-Host "Building the web interface. This is required only once per checkout..."
    Push-Location $Frontend
    try {
        & $npm ci
        if ($LASTEXITCODE -ne 0) {
            throw "Frontend dependency installation failed."
        }
        & $npm run build
        if ($LASTEXITCODE -ne 0) {
            throw "Frontend build failed."
        }
    }
    finally {
        Pop-Location
    }
}

if ($Operation -eq "setup") {
    Ensure-Python
    Ensure-Frontend
    Write-Host "Navi Windows setup is complete."
    exit 0
}

if ($Operation -eq "start") {
    Ensure-Python
    Ensure-Frontend
}
elseif (-not (Test-Path -LiteralPath $Python)) {
    Write-Host "Navi is not set up for native Windows yet. Run start-windows.cmd first."
    exit 1
}

$env:PYTHONPATH = $Backend
& $Python -m navi.launcher $Operation
exit $LASTEXITCODE
