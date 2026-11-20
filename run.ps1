$ErrorActionPreference = 'Stop'
$python = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
if (!(Test-Path -LiteralPath $python)) { throw 'Execute setup.ps1 primeiro.' }
& $python -X utf8 (Join-Path $PSScriptRoot 'scripts/studio.py') @args
exit $LASTEXITCODE
