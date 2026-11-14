param(
    [ValidateSet('cpu', 'cuda')][string]$Device = 'cuda',
    [string]$Python = 'python',
    [switch]$SkipInstall,
    [switch]$SkipVerification
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
Push-Location $PSScriptRoot
try {
    $env:PYTHONUTF8 = '1'
    $environmentName = if ($Device -eq 'cuda') { '.venv-wmeter' } else { '.venv' }
    $lockFile = if ($Device -eq 'cuda') { 'requirements-win-cu124.lock' } else { 'requirements-win-cpu.lock' }
    $venvPython = Join-Path $PSScriptRoot "$environmentName\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $venvPython)) {
        if ($SkipInstall) { throw 'Environment does not exist. Remove -SkipInstall.' }
        & $Python -c "import sys; assert sys.version_info[:2] == (3, 11), 'Pass -Python with a Python 3.11 executable'"
        if ($LASTEXITCODE -ne 0) { throw 'Python 3.11 is required.' }
        & $Python -m venv $environmentName
        if ($LASTEXITCODE -ne 0) { throw 'Virtual environment creation failed.' }
    }
    & $venvPython -c "import sys,platform; assert sys.version_info[:2] == (3,11) and sys.platform == 'win32' and platform.machine().lower() in ('amd64','x86_64'), 'Requires Windows x64 / Python 3.11'; assert sys.prefix != sys.base_prefix, 'Expected isolated venv'"
    if ($LASTEXITCODE -ne 0) { throw 'Unsupported environment.' }
    if (-not $SkipInstall) {
        & $venvPython -m pip install --disable-pip-version-check --require-hashes --only-binary=:all: -r $lockFile
        if ($LASTEXITCODE -ne 0) { throw 'Locked dependency installation failed.' }
    }
    & $venvPython -m pip check
    if ($LASTEXITCODE -ne 0) { throw 'Dependency consistency check failed.' }
    if (-not $SkipVerification) {
        & $venvPython scripts/verify_project.py --environment-only
        if ($LASTEXITCODE -ne 0) { throw 'Environment verification failed.' }
        $assetFiles = @('data\manifest.json','data\cache\pred_metadata.json','models\selection.json','models\detector.pt','models\reader.pt')
        $missingAssets = @($assetFiles | Where-Object { -not (Test-Path -LiteralPath $_) })
        if ($missingAssets.Count -eq 0) {
            & $venvPython scripts/verify_project.py
            if ($LASTEXITCODE -ne 0) { throw 'Research asset verification failed.' }
        } else {
            Write-Host 'Research assets are not bundled in the source repository. See data/README.md and models/README.md.'
        }
    }
    Write-Host "Ready: $venvPython ; see README.md for prediction, training and evaluation."
} finally {
    Pop-Location
}
