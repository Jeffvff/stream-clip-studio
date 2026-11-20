$ErrorActionPreference = 'Stop'
$python = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
if (!(Test-Path -LiteralPath $python)) {
    python -m venv (Join-Path $PSScriptRoot '.venv')
    if ($LASTEXITCODE) { exit $LASTEXITCODE }
}
$env:PYYAML_FORCE_LIBYAML = '0'
& $python -m pip install -r (Join-Path $PSScriptRoot 'requirements.txt')
if ($LASTEXITCODE) { exit $LASTEXITCODE }
& $python -m playwright install chromium
if ($LASTEXITCODE) { exit $LASTEXITCODE }
if ($args.Count -eq 0) { $args = @('onboard') }
& $python -X utf8 (Join-Path $PSScriptRoot 'scripts/studio.py') @args
exit $LASTEXITCODE
