param(
    [ValidateSet('stopped', 'running', 'starting_ready', 'starting_timeout', 'starting_exited', 'stale')]
    [string]$Case
)

$ErrorActionPreference = 'Stop'
$tokens = $null
$parseErrors = $null
$launcher = Join-Path $PSScriptRoot '../scripts/launch.ps1'
$tree = [System.Management.Automation.Language.Parser]::ParseFile($launcher, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count -ne 0) { throw 'Launcher PowerShell syntax errors' }
$toggleAst = $tree.Find({
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Toggle-AppServer'
}, $true)
if (-not $toggleAst) { throw 'Toggle-AppServer not found' }
Invoke-Expression $toggleAst.Extent.Text

# Load only the actual decision function. All OS/network actions are isolated doubles.
$script:scenario = $Case
$script:starts = 0
$script:stops = 0
$script:waits = 0
function Get-ServerState {
    if ($script:scenario -ne 'stopped') { return @{pid=1; port=8765; instance_id='fixture'} }
}
function Test-VerifiedState {
    param($State)
    return $script:scenario -eq 'running'
}
function Test-RecordedLauncherProcess {
    param($State)
    return $script:scenario.StartsWith('starting_')
}
function Wait-RecordedServer {
    param($State, [int]$Seconds)
    if ($Seconds -ne 30) { throw 'Unexpected startup timeout' }
    $script:waits++
    return $script:scenario.Substring(9)
}
function Start-AppServer { $script:starts++ }
function Stop-AppServer { $script:stops++ }

$failed = $false
try { Toggle-AppServer } catch { $failed = $true }
$wantStart = [int]($Case -in @('stopped', 'starting_exited', 'stale'))
$wantStop = [int]($Case -in @('running', 'starting_ready'))
$wantWait = [int]$Case.StartsWith('starting_')
if ($script:starts -ne $wantStart -or $script:stops -ne $wantStop -or
    $script:waits -ne $wantWait -or $failed -ne ($Case -eq 'starting_timeout')) {
    throw "Toggle routing failed: $Case"
}
Write-Output "PASS: $Case"
