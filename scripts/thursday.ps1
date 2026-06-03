param(
    [Parameter(Position = 0)]
    [ValidateSet("start", "stop", "restart", "status", "logs", "reset-workspace")]
    [string]$Action = "status",

    [switch]$NoDocker,
    [switch]$StopDocker,
    [switch]$Force
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = Split-Path -Parent $ScriptDir
$EnvPath = Join-Path $Root ".env"

function Read-EnvFile {
    param([string]$Path)

    $values = @{}
    if (-not (Test-Path -LiteralPath $Path)) {
        return $values
    }

    Get-Content -LiteralPath $Path | ForEach-Object {
        $line = $_.Trim()
        if (-not $line -or $line.StartsWith("#") -or -not $line.Contains("=")) {
            return
        }
        $key, $value = $line.Split("=", 2)
        $values[$key.Trim()] = $value.Trim().Trim('"').Trim("'")
    }
    return $values
}

$Config = Read-EnvFile -Path $EnvPath

function Config-Value {
    param(
        [string]$Name,
        [string]$Default
    )
    if ($Config.ContainsKey($Name) -and $Config[$Name]) {
        return $Config[$Name]
    }
    return $Default
}

$AgentName = Config-Value "AGENT_NAME" "thursday"
$ServerHost = Config-Value "SERVER_HOST" "127.0.0.1"
$ServerPort = [int](Config-Value "SERVER_PORT" "8787")
$LogDirRaw = Config-Value "LOG_DIR" "logs"
$DockerContainer = Config-Value "DOCKER_CONTAINER_NAME" "Thursday"
$DockerImage = Config-Value "DOCKER_IMAGE" "ubuntu:24.04"
$DockerWorkdir = Config-Value "DOCKER_WORKDIR" "/workspace"

if ([System.IO.Path]::IsPathRooted($LogDirRaw)) {
    $LogDir = $LogDirRaw
} else {
    $LogDir = Join-Path $Root $LogDirRaw
}

$PidFile = Join-Path $LogDir "$AgentName.pid.json"
$StdoutLog = Join-Path $LogDir "server.log"
$StderrLog = Join-Path $LogDir "server.err.log"
$LogDbRaw = Config-Value "LOG_DB_FILE" "thursday_logs.sqlite3"
if ([System.IO.Path]::IsPathRooted($LogDbRaw)) {
    $LogDb = $LogDbRaw
} else {
    $LogDb = Join-Path $LogDir $LogDbRaw
}
$LmStudioRawLogRaw = Config-Value "LMSTUDIO_RAW_LOG_FILE" "lmstudio_raw.jsonl"
if ([System.IO.Path]::IsPathRooted($LmStudioRawLogRaw)) {
    $LmStudioRawLog = $LmStudioRawLogRaw
} else {
    $LmStudioRawLog = Join-Path $LogDir $LmStudioRawLogRaw
}
$ServerScript = Join-Path $ScriptDir "server.py"
$LocalPythonRaw = Config-Value "LOCAL_PYTHON" ".venv\Scripts\python.exe"
if ([System.IO.Path]::IsPathRooted($LocalPythonRaw)) {
    $LocalPython = $LocalPythonRaw
} else {
    $LocalPython = Join-Path $Root $LocalPythonRaw
}

function Ensure-LogDir {
    New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
}

function Write-Info {
    param([string]$Message)
    Write-Host "[$AgentName] $Message"
}

function Get-PidRecord {
    if (-not (Test-Path -LiteralPath $PidFile)) {
        return $null
    }
    try {
        return Get-Content -Raw -LiteralPath $PidFile | ConvertFrom-Json
    } catch {
        return $null
    }
}

function Get-ManagedProcess {
    $record = Get-PidRecord
    if (-not $record -or -not $record.pid) {
        return $null
    }

    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$($record.pid)" -ErrorAction SilentlyContinue
    if (-not $process) {
        return $null
    }

    $commandLine = [string]$process.CommandLine
    $expectedScript = [System.IO.Path]::GetFullPath($ServerScript)
    $matchesServer = $commandLine -like "*server.py*"
    $matchesRoot = $commandLine -like "*$Root*"

    if (-not $matchesServer -or -not $matchesRoot) {
        return $null
    }

    return $process
}

function Clear-StalePid {
    $record = Get-PidRecord
    if ($record -and -not (Get-ManagedProcess)) {
        Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
    }
}

function Get-PortOwner {
    try {
        return Get-NetTCPConnection -LocalPort $ServerPort -ErrorAction SilentlyContinue |
            Where-Object { $_.State -eq "Listen" } |
            Select-Object -First 1
    } catch {
        return $null
    }
}

function Test-DockerRunning {
    try {
        $running = docker inspect -f "{{.State.Running}}" $DockerContainer 2>$null
        return $LASTEXITCODE -eq 0 -and $running.Trim().ToLowerInvariant() -eq "true"
    } catch {
        return $false
    }
}

function Ensure-DockerWorkspace {
    if ($NoDocker) {
        return
    }

    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        throw "Docker is not available on PATH."
    }

    if (Test-DockerRunning) {
        Write-Info "Docker workspace '$DockerContainer' is running."
        return
    }

    docker inspect $DockerContainer *> $null
    if ($LASTEXITCODE -eq 0) {
        Write-Info "Starting existing Docker workspace '$DockerContainer'."
        docker start $DockerContainer | Out-Null
        return
    }

    Write-Info "Creating Docker workspace '$DockerContainer'."
    docker run -dit --name $DockerContainer -w $DockerWorkdir $DockerImage bash | Out-Null
}

function Reset-DockerWorkspace {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        throw "Docker is not available on PATH."
    }

    if (-not $Force) {
        $answer = Read-Host "This will delete Docker container '$DockerContainer' and recreate an empty $DockerImage workspace. Type RESET to continue"
        if ($answer -ne "RESET") {
            Write-Info "Workspace reset cancelled."
            return
        }
    }

    docker inspect $DockerContainer *> $null
    if ($LASTEXITCODE -eq 0) {
        if (Test-DockerRunning) {
            Write-Info "Stopping Docker workspace '$DockerContainer'."
            docker stop $DockerContainer | Out-Null
        }
        Write-Info "Removing Docker workspace '$DockerContainer'."
        docker rm $DockerContainer | Out-Null
    } else {
        Write-Info "Docker workspace '$DockerContainer' does not exist; creating it fresh."
    }

    Write-Info "Creating Docker workspace '$DockerContainer' from '$DockerImage'."
    docker run -dit --name $DockerContainer -w $DockerWorkdir $DockerImage bash | Out-Null
    docker exec $DockerContainer mkdir -p $DockerWorkdir | Out-Null
    Write-Info "Fresh workspace ready at docker://$DockerContainer$DockerWorkdir"
}

function Get-PythonPath {
    if (Test-Path -LiteralPath $LocalPython) {
        return $LocalPython
    }
    return (Get-Command python -ErrorAction Stop).Source
}

function Start-Thursday {
    Ensure-LogDir
    Clear-StalePid

    $existing = Get-ManagedProcess
    if ($existing) {
        Write-Info "Already running. PID $($existing.ProcessId), URL http://$ServerHost`:$ServerPort"
        return
    }

    $portOwner = Get-PortOwner
    if ($portOwner -and -not $Force) {
        throw "Port $ServerPort is already owned by PID $($portOwner.OwningProcess). Use -Force only after checking it is safe."
    }

    Ensure-DockerWorkspace

    $python = Get-PythonPath
    $process = Start-Process `
        -FilePath $python `
        -ArgumentList @($ServerScript) `
        -WorkingDirectory $Root `
        -WindowStyle Hidden `
        -RedirectStandardOutput $StdoutLog `
        -RedirectStandardError $StderrLog `
        -PassThru

    $record = [ordered]@{
        pid = $process.Id
        agent = $AgentName
        started_at = (Get-Date).ToString("o")
        root = $Root
        script = $ServerScript
        host = $ServerHost
        port = $ServerPort
        stdout = $StdoutLog
        stderr = $StderrLog
        python = $python
        docker_container = $DockerContainer
        docker_workdir = $DockerWorkdir
    }
    $record | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $PidFile -Encoding UTF8

    Start-Sleep -Seconds 2
    $started = Get-ManagedProcess
    if (-not $started) {
        throw "Start failed. Check $StderrLog"
    }
    Write-Info "Started. PID $($process.Id), URL http://$ServerHost`:$ServerPort"
}

function Stop-Thursday {
    $process = Get-ManagedProcess
    if (-not $process) {
        Clear-StalePid
        Write-Info "Not running."
        return
    }

    Write-Info "Stopping PID $($process.ProcessId)."
    $dotnetProcess = Get-Process -Id $process.ProcessId -ErrorAction SilentlyContinue
    if ($dotnetProcess -and $dotnetProcess.MainWindowHandle -ne 0) {
        $null = $dotnetProcess.CloseMainWindow()
        $dotnetProcess.WaitForExit(5000)
    }

    if (Get-Process -Id $process.ProcessId -ErrorAction SilentlyContinue) {
        Stop-Process -Id $process.ProcessId -Force
    }

    for ($i = 0; $i -lt 20; $i++) {
        if (-not (Get-Process -Id $process.ProcessId -ErrorAction SilentlyContinue)) {
            break
        }
        Start-Sleep -Milliseconds 250
    }

    if (Get-Process -Id $process.ProcessId -ErrorAction SilentlyContinue) {
        throw "Failed to stop PID $($process.ProcessId)."
    }

    Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
    Write-Info "Stopped."

    if ($StopDocker) {
        if (Test-DockerRunning) {
            Write-Info "Stopping Docker workspace '$DockerContainer'."
            docker stop $DockerContainer | Out-Null
        } else {
            Write-Info "Docker workspace '$DockerContainer' is already stopped."
        }
    }
}

function Show-Status {
    Clear-StalePid
    $process = Get-ManagedProcess
    $portOwner = Get-PortOwner
    $dockerStatus = if (Test-DockerRunning) { "running" } else { "not running" }

    if ($process) {
        Write-Info "Server: running"
        Write-Host "  PID:       $($process.ProcessId)"
        Write-Host "  URL:       http://$ServerHost`:$ServerPort"
        Write-Host "  Started:   $($process.CreationDate)"
        Write-Host "  Command:   $($process.CommandLine)"
    } else {
        Write-Info "Server: stopped"
    }

    if ($portOwner) {
        Write-Host "  Port:      $ServerPort listening by PID $($portOwner.OwningProcess)"
    } else {
        Write-Host "  Port:      $ServerPort not listening"
    }

    Write-Host "  Docker:    $DockerContainer $dockerStatus"
    Write-Host "  Workspace: docker://$DockerContainer$DockerWorkdir"
    Write-Host "  PID file:  $PidFile"
    Write-Host "  Logs:      $LogDir"
    Write-Host "  Log DB:    $LogDb"
    Write-Host "  LM raw DB: $LogDb"
    Write-Host "  Legacy raw import: $LmStudioRawLog"
}

function Show-Logs {
    Ensure-LogDir
    Write-Host "== sqlite logs =="
    if (Test-Path -LiteralPath $LogDb) {
        $python = Get-PythonPath
    $script = @'
import sqlite3
import sys

db_path = sys.argv[1]
with sqlite3.connect(db_path) as conn:
    rows = conn.execute(
        "SELECT created_at, level, source, logger, message FROM logs ORDER BY created_at DESC, id DESC LIMIT 80"
    ).fetchall()
for created_at, level, source, logger, message in reversed(rows):
    print(f"{created_at} {level:<8} [{source}/{logger}] {message}")
'@
        $script | & $python - $LogDb
    } else {
        Write-Host "No SQLite log database yet: $LogDb"
    }
    Write-Host "== server stdout fallback =="
    if (Test-Path -LiteralPath $StdoutLog) {
        Get-Content -LiteralPath $StdoutLog -Tail 20
    }
    Write-Host "== server stderr fallback =="
    if (Test-Path -LiteralPath $StderrLog) {
        Get-Content -LiteralPath $StderrLog -Tail 20
    }
    Write-Host "== lmstudio raw sqlite rows =="
    if (Test-Path -LiteralPath $LogDb) {
        $python = Get-PythonPath
        $script = @'
import json
import sqlite3
import sys

db_path = sys.argv[1]
with sqlite3.connect(db_path) as conn:
    rows = conn.execute(
        """
        SELECT id, created_at, request_id, status_code, elapsed_ms, model, finish_reason
        FROM lmstudio_endpoint_logs
        ORDER BY created_at DESC, id DESC
        LIMIT 10
        """
    ).fetchall()
for row_id, created_at, request_id, status_code, elapsed_ms, model, finish_reason in reversed(rows):
    print(f"{created_at} raw#{row_id} status={status_code} finish={finish_reason} elapsed_ms={elapsed_ms} request_id={request_id} model={model}")
'@
        $script | & $python - $LogDb
    }
}

switch ($Action) {
    "start" { Start-Thursday }
    "stop" { Stop-Thursday }
    "restart" {
        Stop-Thursday
        Start-Thursday
    }
    "status" { Show-Status }
    "logs" { Show-Logs }
    "reset-workspace" { Reset-DockerWorkspace }
}
