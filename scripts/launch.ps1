[CmdletBinding()]
param(
    [switch]$NoBrowser,
    [switch]$NoDialog,
    [switch]$Stop
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$script:Root = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$script:RuntimeDir = Join-Path $script:Root '.runtime'
$script:LauncherLog = Join-Path $script:RuntimeDir 'launcher.log'
$script:StatePath = Join-Path $script:RuntimeDir 'server.json'
$script:PythonVersion = '3.14.7'
$script:NodeVersion = '24.21.0'

function Write-LauncherLog {
    param([string]$Message)
    $timestamp = [DateTime]::Now.ToString('yyyy-MM-dd HH:mm:ss.fff')
    Add-Content -LiteralPath $script:LauncherLog -Encoding UTF8 -Value "[$timestamp] $Message"
}

function Get-StringSha256 {
    param([string]$Text)
    $algorithm = [System.Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($Text)
        return ([BitConverter]::ToString($algorithm.ComputeHash($bytes))).Replace('-', '').ToLowerInvariant()
    }
    finally {
        $algorithm.Dispose()
    }
}

function Get-TreeFingerprint {
    param([string]$Directory)
    $fullRoot = [System.IO.Path]::GetFullPath($Directory).TrimEnd([System.IO.Path]::DirectorySeparatorChar)
    $records = New-Object System.Collections.Generic.List[string]
    foreach ($file in (Get-ChildItem -LiteralPath $fullRoot -Recurse -File | Sort-Object FullName)) {
        $relative = $file.FullName.Substring($fullRoot.Length).TrimStart([System.IO.Path]::DirectorySeparatorChar)
        if ($relative -match '^(node_modules|dist)(\\|/)' -or $relative -match '\.tsbuildinfo$') {
            continue
        }
        $hash = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        $records.Add(($relative.Replace('\', '/') + '|' + $hash))
    }
    return (Get-StringSha256 ([string]::Join("`n", $records)))
}

function Read-Stamp {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return ''
    }
    return (Get-Content -LiteralPath $Path -Raw).Trim()
}

function Write-Stamp {
    param([string]$Path, [string]$Value)
    Set-Content -LiteralPath $Path -Encoding ASCII -NoNewline -Value $Value
}

function ConvertTo-ProcessArgument {
    param([AllowEmptyString()][string]$Argument)
    if ($Argument.Length -gt 0 -and $Argument -notmatch '[\s"]') {
        return $Argument
    }
    $builder = New-Object System.Text.StringBuilder
    [void]$builder.Append('"')
    $backslashes = 0
    foreach ($character in $Argument.ToCharArray()) {
        if ($character -eq '\') {
            $backslashes++
            continue
        }
        if ($character -eq '"') {
            if ($backslashes -gt 0) {
                [void]$builder.Append((('\' * ($backslashes * 2)) -join ''))
            }
            [void]$builder.Append('\')
            [void]$builder.Append('"')
            $backslashes = 0
            continue
        }
        if ($backslashes -gt 0) {
            [void]$builder.Append((('\' * $backslashes) -join ''))
            $backslashes = 0
        }
        [void]$builder.Append($character)
    }
    if ($backslashes -gt 0) {
        [void]$builder.Append((('\' * ($backslashes * 2)) -join ''))
    }
    [void]$builder.Append('"')
    return $builder.ToString()
}

function Invoke-LoggedCommand {
    param(
        [string]$FilePath,
        [string[]]$Arguments,
        [string]$WorkingDirectory,
        [string]$Description
    )
    Write-LauncherLog "$Description 시작"
    $argumentLine = (($Arguments | ForEach-Object { ConvertTo-ProcessArgument $_ }) -join ' ')
    $startInfo = New-Object System.Diagnostics.ProcessStartInfo
    $startInfo.FileName = $FilePath
    $startInfo.Arguments = $argumentLine
    $startInfo.WorkingDirectory = $WorkingDirectory
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $utf8 = New-Object System.Text.UTF8Encoding($false)
    $startInfo.StandardOutputEncoding = $utf8
    $startInfo.StandardErrorEncoding = $utf8
    $startInfo.EnvironmentVariables['PYTHONUTF8'] = '1'
    $startInfo.EnvironmentVariables['PYTHONIOENCODING'] = 'utf-8'
    $commandDirectory = Split-Path -Parent $FilePath
    $startInfo.EnvironmentVariables['PATH'] = $commandDirectory + [System.IO.Path]::PathSeparator + $startInfo.EnvironmentVariables['PATH']
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $startInfo
    try {
        if (-not $process.Start()) {
            throw "$Description 프로세스를 시작하지 못했습니다."
        }
        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.ReadToEndAsync()
        $process.WaitForExit()
        $stdout = $stdoutTask.Result
        $stderr = $stderrTask.Result
        $exitCode = $process.ExitCode
    }
    finally {
        $process.Dispose()
    }
    if ($stdout) { Add-Content -LiteralPath $script:LauncherLog -Encoding UTF8 -Value $stdout.TrimEnd() }
    if ($stderr) { Add-Content -LiteralPath $script:LauncherLog -Encoding UTF8 -Value $stderr.TrimEnd() }
    if ($exitCode -ne 0) {
        throw "$Description 실패 (종료 코드 $exitCode). 로그: $script:LauncherLog"
    }
    Write-LauncherLog "$Description 완료"
}

function Download-File {
    param([string]$Url, [string]$Destination)
    Write-LauncherLog "다운로드: $Url"
    $client = New-Object System.Net.WebClient
    try {
        $client.DownloadFile($Url, $Destination)
    }
    finally {
        $client.Dispose()
    }
}

function Download-Text {
    param([string]$Url)
    $client = New-Object System.Net.WebClient
    try {
        return $client.DownloadString($Url)
    }
    finally {
        $client.Dispose()
    }
}

function Expand-ZipArchive {
    param([string]$Archive, [string]$Destination)
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [System.IO.Compression.ZipFile]::ExtractToDirectory($Archive, $Destination)
}

function Install-ProjectNode {
    $toolsDir = Join-Path $script:Root '.tools'
    New-Item -ItemType Directory -Path $toolsDir -Force | Out-Null
    $nodeDir = Join-Path $toolsDir 'node'
    $archiveName = "node-v$($script:NodeVersion)-win-x64.zip"
    $baseUrl = "https://nodejs.org/dist/v$($script:NodeVersion)"
    $nonce = [guid]::NewGuid().ToString('N')
    $archivePath = Join-Path $script:RuntimeDir "node-$nonce.zip"
    $stagingPath = Join-Path $script:RuntimeDir "node-$nonce"
    try {
        $sums = Download-Text "$baseUrl/SHASUMS256.txt"
        $pattern = '(?im)^([0-9a-f]{64})\s+' + [regex]::Escape($archiveName) + '\s*$'
        $match = [regex]::Match($sums, $pattern)
        if (-not $match.Success) {
            throw "Node SHA256 값을 공식 SHASUMS256.txt에서 찾지 못했습니다."
        }
        Download-File "$baseUrl/$archiveName" $archivePath
        $actualHash = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash
        if (-not $actualHash.Equals($match.Groups[1].Value, [StringComparison]::OrdinalIgnoreCase)) {
            throw 'Node 다운로드 SHA256 검증에 실패했습니다.'
        }
        New-Item -ItemType Directory -Path $stagingPath -Force | Out-Null
        Expand-ZipArchive $archivePath $stagingPath
        $extractedPath = Join-Path $stagingPath "node-v$($script:NodeVersion)-win-x64"
        if (-not (Test-Path -LiteralPath (Join-Path $extractedPath 'node.exe') -PathType Leaf)) {
            throw 'Node 압축 파일에 node.exe가 없습니다.'
        }
        if (Test-Path -LiteralPath $nodeDir) {
            Remove-Item -LiteralPath $nodeDir -Recurse -Force
        }
        Move-Item -LiteralPath $extractedPath -Destination $nodeDir
        Write-LauncherLog "프로젝트 Node v$($script:NodeVersion) 설치 완료"
    }
    finally {
        if (Test-Path -LiteralPath $archivePath) { Remove-Item -LiteralPath $archivePath -Force }
        if (Test-Path -LiteralPath $stagingPath) { Remove-Item -LiteralPath $stagingPath -Recurse -Force }
    }
}

function Ensure-ProjectNode {
    $nodeExe = Join-Path $script:Root '.tools/node/node.exe'
    $valid = $false
    if (Test-Path -LiteralPath $nodeExe -PathType Leaf) {
        try {
            $reported = (& $nodeExe --version 2>$null).Trim()
            $valid = ($LASTEXITCODE -eq 0 -and $reported -eq "v$($script:NodeVersion)")
        }
        catch {
            $valid = $false
        }
    }
    if (-not $valid) {
        Install-ProjectNode
    }
    return $nodeExe
}

function Install-ProjectPython {
    $toolsDir = Join-Path $script:Root '.tools'
    New-Item -ItemType Directory -Path $toolsDir -Force | Out-Null
    $pythonDir = Join-Path $toolsDir 'python'
    $version = $script:PythonVersion
    $registrationUrl = "https://api.nuget.org/v3/registration5-semver1/python/$version.json"
    $packageUrl = "https://api.nuget.org/v3-flatcontainer/python/$version/python.$version.nupkg"
    $nonce = [guid]::NewGuid().ToString('N')
    $archivePath = Join-Path $script:RuntimeDir "python-$nonce.zip"
    $stagingPath = Join-Path $script:RuntimeDir "python-$nonce"
    try {
        $registration = (Download-Text $registrationUrl) | ConvertFrom-Json
        $entry = $registration.catalogEntry
        if ($entry -is [string]) {
            $entry = (Download-Text $entry) | ConvertFrom-Json
        }
        $expectedHash = $registration.packageHash
        $hashAlgorithm = $registration.packageHashAlgorithm
        if (-not $expectedHash -and $entry) { $expectedHash = $entry.packageHash }
        if (-not $hashAlgorithm -and $entry) { $hashAlgorithm = $entry.packageHashAlgorithm }
        if (-not $expectedHash -or $hashAlgorithm -ne 'SHA512') {
            throw 'NuGet 등록 정보에서 Python 패키지 SHA512를 찾지 못했습니다.'
        }
        Download-File $packageUrl $archivePath
        $algorithm = [System.Security.Cryptography.SHA512]::Create()
        try {
            $stream = [System.IO.File]::OpenRead($archivePath)
            try {
                $actualHash = [Convert]::ToBase64String($algorithm.ComputeHash($stream))
            }
            finally {
                $stream.Dispose()
            }
        }
        finally {
            $algorithm.Dispose()
        }
        if (-not $actualHash.Equals([string]$expectedHash, [StringComparison]::Ordinal)) {
            throw 'Python 다운로드 SHA512 검증에 실패했습니다.'
        }
        New-Item -ItemType Directory -Path $stagingPath -Force | Out-Null
        Expand-ZipArchive $archivePath $stagingPath
        if (-not (Test-Path -LiteralPath (Join-Path $stagingPath 'tools/python.exe') -PathType Leaf)) {
            throw 'Python NuGet 패키지에 tools/python.exe가 없습니다.'
        }
        if (Test-Path -LiteralPath $pythonDir) {
            Remove-Item -LiteralPath $pythonDir -Recurse -Force
        }
        Move-Item -LiteralPath $stagingPath -Destination $pythonDir
        $stagingPath = ''
        Write-LauncherLog "프로젝트 Python $version 설치 완료"
    }
    finally {
        if (Test-Path -LiteralPath $archivePath) { Remove-Item -LiteralPath $archivePath -Force }
        if ($stagingPath -and (Test-Path -LiteralPath $stagingPath)) {
            Remove-Item -LiteralPath $stagingPath -Recurse -Force
        }
    }
}

function Ensure-ProjectPython {
    $pythonExe = Join-Path $script:Root '.tools/python/tools/python.exe'
    $valid = $false
    if (Test-Path -LiteralPath $pythonExe -PathType Leaf) {
        try {
            $reported = (& $pythonExe -c 'import platform; print(platform.python_version())' 2>$null).Trim()
            $valid = ($LASTEXITCODE -eq 0 -and $reported -eq $script:PythonVersion)
        }
        catch {
            $valid = $false
        }
    }
    if (-not $valid) {
        Install-ProjectPython
    }
    return $pythonExe
}

function Ensure-PythonEnvironment {
    $basePython = Ensure-ProjectPython
    $venvDir = Join-Path $script:Root '.venv'
    $venvPython = Join-Path $venvDir 'Scripts/python.exe'
    $configPath = Join-Path $venvDir 'pyvenv.cfg'
    $expectedHome = [System.IO.Path]::GetFullPath((Join-Path $script:Root '.tools/python/tools'))
    $homeMatches = $false
    if (Test-Path -LiteralPath $configPath -PathType Leaf) {
        $homeLine = Get-Content -LiteralPath $configPath -Encoding UTF8 | Where-Object { $_ -match '^home\s*=' } | Select-Object -First 1
        if ($homeLine) {
            $configuredHome = [System.IO.Path]::GetFullPath(($homeLine -replace '^home\s*=\s*', ''))
            $homeMatches = $configuredHome.Equals($expectedHome, [StringComparison]::OrdinalIgnoreCase)
        }
    }
    $venvRuns = $false
    if ($homeMatches -and (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
        try {
            & $venvPython -c 'import sys; raise SystemExit(0 if sys.prefix != sys.base_prefix else 1)' 2>$null
            $venvRuns = ($LASTEXITCODE -eq 0)
        }
        catch {
            $venvRuns = $false
        }
    }
    $venvChanged = $false
    if (-not $venvRuns) {
        Write-LauncherLog '프로젝트 가상 환경을 생성하거나 현재 경로에 맞게 복구합니다.'
        try {
            Invoke-LoggedCommand $basePython @('-m', 'venv', '--upgrade', $venvDir) $script:Root 'Python 가상 환경 복구'
        }
        catch {
            Invoke-LoggedCommand $basePython @('-m', 'venv', '--clear', $venvDir) $script:Root 'Python 가상 환경 재생성'
        }
        $venvChanged = $true
    }
    if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
        throw '가상 환경 Python 실행 파일을 만들지 못했습니다.'
    }

    $requirements = Join-Path $script:Root 'requirements.txt'
    $requirementsHash = (Get-FileHash -LiteralPath $requirements -Algorithm SHA256).Hash.ToLowerInvariant()
    $dependencyFingerprint = "$($script:PythonVersion)|$requirementsHash"
    $stampPath = Join-Path $script:RuntimeDir 'python-dependencies.sha256'
    $importsWork = $false
    try {
        Push-Location -LiteralPath $script:Root
        & $venvPython -c 'import fastapi, uvicorn, google.genai, truststore, backend.server' 2>$null
        $importsWork = ($LASTEXITCODE -eq 0)
    }
    catch {
        $importsWork = $false
    }
    finally {
        Pop-Location
    }
    if ($venvChanged -or -not $importsWork -or (Read-Stamp $stampPath) -ne $dependencyFingerprint) {
        Invoke-LoggedCommand $venvPython @('-m', 'pip', 'install', '--disable-pip-version-check', '-r', 'requirements.txt') $script:Root 'Python 패키지 확인'
        & $venvPython -c 'import fastapi, uvicorn, google.genai, truststore, backend.server' 2>$null
        if ($LASTEXITCODE -ne 0) {
            throw 'Python 패키지 설치 후 필수 모듈을 불러오지 못했습니다.'
        }
        Write-Stamp $stampPath $dependencyFingerprint
    }
    return $venvPython
}

function Ensure-Frontend {
    param([string]$NodeExe)
    $frontendDir = Join-Path $script:Root 'frontend'
    $npmCmd = Join-Path (Split-Path -Parent $NodeExe) 'npm.cmd'
    if (-not (Test-Path -LiteralPath $npmCmd -PathType Leaf)) {
        throw '프로젝트 Node에 npm.cmd가 없습니다.'
    }
    $lockPath = Join-Path $frontendDir 'package-lock.json'
    $lockHash = (Get-FileHash -LiteralPath $lockPath -Algorithm SHA256).Hash.ToLowerInvariant()
    $dependenciesFingerprint = "$($script:NodeVersion)|$lockHash"
    $dependenciesStamp = Join-Path $script:RuntimeDir 'frontend-dependencies.sha256'
    $vitePath = Join-Path $frontendDir 'node_modules/vite/bin/vite.js'
    if ((Read-Stamp $dependenciesStamp) -ne $dependenciesFingerprint -or -not (Test-Path -LiteralPath $vitePath -PathType Leaf)) {
        Invoke-LoggedCommand $npmCmd @('ci', '--no-audit', '--no-fund') $frontendDir '프런트엔드 패키지 확인'
        Write-Stamp $dependenciesStamp $dependenciesFingerprint
    }

    $sourceFingerprint = Get-TreeFingerprint $frontendDir
    $buildFingerprint = "$($script:NodeVersion)|$sourceFingerprint"
    $buildStamp = Join-Path $script:RuntimeDir 'frontend-build.sha256'
    $indexPath = Join-Path $frontendDir 'dist/index.html'
    if ((Read-Stamp $buildStamp) -ne $buildFingerprint -or -not (Test-Path -LiteralPath $indexPath -PathType Leaf)) {
        Invoke-LoggedCommand $npmCmd @('run', 'build') $frontendDir '프런트엔드 프로덕션 빌드'
        if (-not (Test-Path -LiteralPath $indexPath -PathType Leaf)) {
            throw '프런트엔드 빌드가 dist/index.html을 만들지 못했습니다.'
        }
        Write-Stamp $buildStamp $buildFingerprint
    }
}

function Get-ServerState {
    if (-not (Test-Path -LiteralPath $script:StatePath -PathType Leaf)) {
        return $null
    }
    try {
        $state = Get-Content -LiteralPath $script:StatePath -Raw | ConvertFrom-Json
        $processId = 0
        $port = 0
        if (-not [int]::TryParse([string]$state.pid, [ref]$processId) -or $processId -le 0 -or
            -not [int]::TryParse([string]$state.port, [ref]$port) -or $port -lt 8765 -or $port -gt 8775 -or
            [string]$state.instance_id -notmatch '^[0-9a-f]{32}$') {
            return $null
        }
        return $state
    }
    catch {
        Write-LauncherLog "server.json 읽기 실패: $($_.Exception.Message)"
        return $null
    }
}

function Remove-ServerState {
    if (Test-Path -LiteralPath $script:StatePath) {
        Remove-Item -LiteralPath $script:StatePath -Force
    }
}

function Get-Health {
    param([int]$Port)
    try {
        return Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/health" -Method Get -TimeoutSec 2 -UseBasicParsing
    }
    catch {
        return $null
    }
}

function Test-VerifiedState {
    param($State)
    if (-not $State) { return $false }
    try {
        Get-Process -Id ([int]$State.pid) -ErrorAction Stop | Out-Null
    }
    catch {
        return $false
    }
    $health = Get-Health ([int]$State.port)
    return ($health -and $health.app -eq 'summary-to-video' -and $health.ready -eq $true -and
        $health.instance_id -eq [string]$State.instance_id)
}

function Test-RecordedLauncherProcess {
    param($State)
    if (-not $State) { return $false }
    try {
        $processInfo = Get-CimInstance -ClassName Win32_Process -Filter "ProcessId = $([int]$State.pid)"
        if (-not $processInfo) { return $false }
        $expectedPython = [System.IO.Path]::GetFullPath((Join-Path $script:Root '.venv/Scripts/python.exe'))
        $actualPython = [System.IO.Path]::GetFullPath([string]$processInfo.ExecutablePath)
        $commandLine = [string]$processInfo.CommandLine
        return ($actualPython.Equals($expectedPython, [StringComparison]::OrdinalIgnoreCase) -and
            $commandLine.Contains('backend.server') -and
            $commandLine.Contains([string]$State.instance_id))
    }
    catch {
        return $false
    }
}

function Wait-RecordedServer {
    param($State, [int]$Seconds)
    $deadline = [DateTime]::UtcNow.AddSeconds($Seconds)
    while ([DateTime]::UtcNow -lt $deadline) {
        try {
            Get-Process -Id ([int]$State.pid) -ErrorAction Stop | Out-Null
        }
        catch {
            return 'exited'
        }
        $health = Get-Health ([int]$State.port)
        if ($health -and $health.app -eq 'summary-to-video' -and $health.ready -eq $true -and
            $health.instance_id -eq [string]$State.instance_id) {
            return 'ready'
        }
        Start-Sleep -Milliseconds 150
    }
    return 'timeout'
}

function Test-PortAvailable {
    param([int]$Port)
    $listener = $null
    try {
        $listener = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback, $Port)
        $listener.Server.ExclusiveAddressUse = $true
        $listener.Start()
        return $true
    }
    catch {
        return $false
    }
    finally {
        if ($listener) { $listener.Stop() }
    }
}

function Open-AppBrowser {
    param([int]$Port)
    if (-not $NoBrowser) {
        Start-Process "http://127.0.0.1:$Port/" | Out-Null
    }
}

function Write-ServerState {
    param([int]$ProcessId, [int]$Port, [string]$InstanceId)
    $temporaryPath = "$($script:StatePath).$([guid]::NewGuid().ToString('N')).tmp"
    $state = [ordered]@{ pid = $ProcessId; port = $Port; instance_id = $InstanceId }
    $state | ConvertTo-Json | Set-Content -LiteralPath $temporaryPath -Encoding UTF8
    Move-Item -LiteralPath $temporaryPath -Destination $script:StatePath -Force
}

function Stop-AppServer {
    $state = Get-ServerState
    if (-not $state) {
        if (Test-Path -LiteralPath $script:StatePath) {
            Write-LauncherLog '식별할 수 없는 server.json만 제거했습니다. 어떤 프로세스도 종료하지 않았습니다.'
            Remove-ServerState
        }
        Write-Output '실행 중인 앱 서버 기록이 없습니다.'
        return
    }
    if (-not (Test-VerifiedState $state)) {
        $processExists = $true
        try {
            Get-Process -Id ([int]$state.pid) -ErrorAction Stop | Out-Null
        }
        catch {
            $processExists = $false
        }
        if (-not $processExists) {
            Remove-ServerState
            Write-LauncherLog '종료된 서버의 오래된 server.json을 제거했습니다.'
            Write-Output '앱 서버는 이미 종료되어 있습니다.'
            return
        }
        throw '기록된 프로세스의 앱/인스턴스 신원을 확인할 수 없어 종료하지 않았습니다.'
    }

    $port = [int]$state.port
    $baseUrl = "http://127.0.0.1:$port"
    $session = Invoke-RestMethod -Uri "$baseUrl/api/session" -Method Get -TimeoutSec 3 -UseBasicParsing
    $health = Get-Health $port
    if (-not $health -or $health.app -ne 'summary-to-video' -or
        $health.instance_id -ne [string]$state.instance_id) {
        throw '종료 직전 앱 서버 신원이 바뀌어 요청을 보내지 않았습니다.'
    }
    $headers = @{ 'X-App-Token' = [string]$session.token }
    Invoke-RestMethod -Uri "$baseUrl/api/shutdown" -Method Post -Headers $headers -TimeoutSec 5 -UseBasicParsing | Out-Null
    Write-LauncherLog "PID $($state.pid), 포트 $port 서버에 정상 종료를 요청했습니다."

    $deadline = [DateTime]::UtcNow.AddSeconds(15)
    while ([DateTime]::UtcNow -lt $deadline) {
        $processExists = $true
        try { Get-Process -Id ([int]$state.pid) -ErrorAction Stop | Out-Null }
        catch { $processExists = $false }
        $currentHealth = Get-Health $port
        $sameServer = ($currentHealth -and $currentHealth.app -eq 'summary-to-video' -and
            $currentHealth.instance_id -eq [string]$state.instance_id)
        if (-not $processExists -or -not $sameServer) {
            Remove-ServerState
            Write-Output '앱 서버를 종료했습니다.'
            return
        }
        Start-Sleep -Milliseconds 150
    }
    throw "앱 서버가 제한 시간 안에 종료되지 않았습니다. 상태 기록을 보존했습니다: $script:StatePath"
}

function Start-AppServer {
    $state = Get-ServerState
    if (Test-VerifiedState $state) {
        Write-LauncherLog "기존 서버 재사용: PID $($state.pid), 포트 $($state.port)"
        Open-AppBrowser ([int]$state.port)
        Write-Output "앱 서버가 이미 실행 중입니다: http://127.0.0.1:$($state.port)/"
        return
    }
    if ($state -and (Test-RecordedLauncherProcess $state)) {
        Write-LauncherLog "아직 준비 중인 기존 서버를 기다립니다: PID $($state.pid), 포트 $($state.port)"
        $waitResult = Wait-RecordedServer $state 30
        if ($waitResult -eq 'ready') {
            Write-LauncherLog "기존 서버 준비 완료: PID $($state.pid), 포트 $($state.port)"
            Open-AppBrowser ([int]$state.port)
            Write-Output "앱 서버가 준비되었습니다: http://127.0.0.1:$($state.port)/"
            return
        }
        if ($waitResult -eq 'timeout') {
            throw "시작한 앱 서버가 아직 실행 중이지만 준비되지 않았습니다. 중복 실행을 막기 위해 상태 기록을 보존했습니다: $script:StatePath"
        }
    }
    if (Test-Path -LiteralPath $script:StatePath) {
        Write-LauncherLog '오래되었거나 신원이 일치하지 않는 server.json을 제거했습니다. 어떤 프로세스도 종료하지 않았습니다.'
        Remove-ServerState
    }

    $venvPython = Ensure-PythonEnvironment
    $nodeExe = Ensure-ProjectNode
    Ensure-Frontend $nodeExe

    $stdoutPath = Join-Path $script:RuntimeDir 'server.stdout.log'
    $stderrPath = Join-Path $script:RuntimeDir 'server.stderr.log'
    foreach ($port in 8765..8775) {
        if (-not (Test-PortAvailable $port)) { continue }
        $instanceId = [guid]::NewGuid().ToString('N')
        Write-LauncherLog "서버 시작 시도: 포트 $port, 인스턴스 $instanceId"
        $process = Start-Process -FilePath $venvPython -ArgumentList @('-m', 'backend.server', '--port', $port, '--instance-id', $instanceId) `
            -WorkingDirectory $script:Root -WindowStyle Hidden -RedirectStandardOutput $stdoutPath `
            -RedirectStandardError $stderrPath -PassThru
        Write-ServerState $process.Id $port $instanceId
        $deadline = [DateTime]::UtcNow.AddSeconds(30)
        while ([DateTime]::UtcNow -lt $deadline) {
            if ($process.HasExited) {
                Write-LauncherLog "포트 $port 서버 프로세스가 준비 전에 종료되었습니다 (코드 $($process.ExitCode))."
                $recorded = Get-ServerState
                if ($recorded -and [string]$recorded.instance_id -eq $instanceId) {
                    Remove-ServerState
                }
                break
            }
            $health = Get-Health $port
            if ($health -and $health.app -eq 'summary-to-video' -and $health.ready -eq $true -and
                $health.instance_id -eq $instanceId) {
                Write-LauncherLog "서버 준비 완료: PID $($process.Id), 포트 $port"
                Open-AppBrowser $port
                Write-Output "앱 서버를 시작했습니다: http://127.0.0.1:$port/"
                return
            }
            Start-Sleep -Milliseconds 150
        }
        if (-not $process.HasExited) {
            throw "시작한 앱 서버(PID $($process.Id), 포트 $port)가 아직 실행 중이지만 준비되지 않았습니다. 중복 실행을 막기 위해 상태 기록을 보존했습니다: $script:StatePath"
        }
    }
    throw "사용 가능한 포트(8765~8775)에서 앱 서버를 시작하지 못했습니다. 로그: $stderrPath"
}

New-Item -ItemType Directory -Path $script:RuntimeDir -Force | Out-Null
Set-Location -LiteralPath $script:Root
[System.Net.ServicePointManager]::SecurityProtocol = [System.Net.ServicePointManager]::SecurityProtocol -bor [System.Net.SecurityProtocolType]::Tls12

$mutex = $null
$ownsMutex = $false
$failure = $null
try {
    $normalizedRoot = ($script:Root).ToLowerInvariant()
    $mutexName = 'Local\SummaryToVideo-' + (Get-StringSha256 $normalizedRoot).Substring(0, 24)
    $mutex = New-Object System.Threading.Mutex($false, $mutexName)
    try {
        $ownsMutex = $mutex.WaitOne([TimeSpan]::FromMinutes(5))
    }
    catch [System.Threading.AbandonedMutexException] {
        $ownsMutex = $true
        Write-LauncherLog '이전 실행이 비정상 종료되어 남은 뮤텍스를 인계받았습니다.'
    }
    if (-not $ownsMutex) {
        throw '다른 시작/종료 작업이 끝나기를 기다리는 시간이 초과되었습니다.'
    }
    if ($Stop) {
        Stop-AppServer
    }
    else {
        Start-AppServer
    }
}
catch {
    $failure = $_
    Write-LauncherLog ("오류: " + $_.Exception.ToString())
}
finally {
    if ($ownsMutex -and $mutex) { $mutex.ReleaseMutex() }
    if ($mutex) { $mutex.Dispose() }
}

if ($failure) {
    $message = "$($failure.Exception.Message)`n`n자세한 로그: $script:LauncherLog"
    if (-not $NoDialog) {
        try {
            Add-Type -AssemblyName System.Windows.Forms
            [System.Windows.Forms.MessageBox]::Show($message, 'Market Brief 실행 오류', 'OK', 'Error') | Out-Null
        }
        catch {
            # The command-line error below is still available if the dialog cannot be shown.
        }
    }
    Write-Error $message
    exit 1
}
