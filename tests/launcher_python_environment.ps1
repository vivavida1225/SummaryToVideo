param([string]$FixtureRoot, [string]$Case)

$ErrorActionPreference = 'Stop'
$sourcePath = Join-Path $PSScriptRoot '../scripts/launch.ps1'
$ast = [System.Management.Automation.Language.Parser]::ParseFile($sourcePath, [ref]$null, [ref]$null)
foreach ($name in @('Ensure-PythonEnvironment', 'Read-Stamp', 'Write-Stamp')) {
    $definition = $ast.Find({ param($node)
        $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name
    }, $true)
    Invoke-Expression $definition.Extent.Text
}

$script:Root = [System.IO.Path]::GetFullPath($FixtureRoot)
$script:RuntimeDir = Join-Path $script:Root '.runtime'
$script:PythonVersion = '3.14.7'
$script:Calls = New-Object System.Collections.Generic.List[object]
$script:BasePython = Join-Path $script:Root '.tools/python/tools/python.exe'
$script:VenvPython = Join-Path $script:Root '.venv/Scripts/python.exe'
$script:ConfigPath = Join-Path $script:Root '.venv/pyvenv.cfg'
$script:NativeMarker = Join-Path $script:Root '.venv/Lib/site-packages/stale-cp312.pyd'
$utf8 = New-Object System.Text.UTF8Encoding($false)

function Write-Config([string]$HomePath, [string]$Version) {
    [System.IO.File]::WriteAllText($script:ConfigPath, "home = $HomePath`nversion = $Version`n", $utf8)
}
function Ensure-ProjectPython { return $script:BasePython }
function Write-LauncherLog { param($Message) }
function Invoke-LoggedCommand {
    param($File, $Arguments, $WorkingDirectory, $Description)
    $script:Calls.Add(@($Arguments))
    if ($Arguments -contains 'venv') {
        if ($Arguments -contains '--clear') {
            Remove-Item -LiteralPath $script:NativeMarker -Force -ErrorAction SilentlyContinue
        }
        New-Item -ItemType Directory -Path (Split-Path $script:VenvPython) -Force | Out-Null
        [System.IO.File]::WriteAllText($script:VenvPython, '', $utf8)
        Write-Config (Split-Path $script:BasePython) $script:PythonVersion
        $script:Broken = $false
    }
}

# Stub only the Python process boundary; exercise the real launcher's decisions.
Set-Item -LiteralPath ('Function:\' + $script:VenvPython) -Value {
    param($Flag, $Code)
    $global:LASTEXITCODE = if ($script:Broken) { 1 } else { 0 }
}
$script:Broken = $Case -eq 'broken'
New-Item -ItemType Directory -Path $script:RuntimeDir, (Split-Path $script:VenvPython), (Split-Path $script:NativeMarker) -Force | Out-Null
[System.IO.File]::WriteAllText((Join-Path $script:Root 'requirements.txt'), '', $utf8)
if ($Case -ne 'missing') {
    [System.IO.File]::WriteAllText($script:VenvPython, '', $utf8)
    [System.IO.File]::WriteAllText($script:NativeMarker, 'old native extension', $utf8)
    $fixtureHome = if ($Case -eq 'wrong_home') { Join-Path $script:Root 'other-python' } else { Split-Path $script:BasePython }
    $version = if ($Case -eq 'wrong_version') { '3.12.0' } elseif ($Case -eq 'missing_version') { '' } else { $script:PythonVersion }
    Write-Config $fixtureHome $version
}

$oldLocation = Get-Location
try {
    Set-Location -LiteralPath $script:Root
    $result = Ensure-PythonEnvironment
    if ($result -ne $script:VenvPython) { throw 'Unexpected environment path' }
    $creations = @($script:Calls | Where-Object { $_ -contains 'venv' })
    if ($Case -eq 'same_version') {
        if ($creations.Count -ne 0) { throw 'Compatible environment was recreated' }
        if (-not (Test-Path -LiteralPath $script:NativeMarker)) { throw 'Compatible packages were removed' }
    }
    else {
        if ($creations.Count -ne 1) { throw 'Incompatible environment was not recreated once' }
        if ($creations[0] -notcontains '--clear') { throw 'Incompatible environment used upgrade instead of clear' }
        if (Test-Path -LiteralPath $script:NativeMarker) { throw 'Stale native extension survived recreation' }
    }
    if (@($script:Calls | Where-Object { $_ -contains '--upgrade' }).Count) { throw 'Unsafe in-place upgrade requested' }
    Write-Output "PASS: $Case"
}
finally {
    Set-Location -LiteralPath $oldLocation
    Remove-Item -LiteralPath ('Function:\' + $script:VenvPython)
}
