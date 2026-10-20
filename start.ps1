$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\pythonw.exe"

if (-not (Test-Path -LiteralPath $VenvPython)) {
    Write-Error "The runtime is not installed. Run .\install.ps1 first."
}

$MainScript = Join-Path $ProjectRoot "main.py"
Start-Process -FilePath $VenvPython -ArgumentList ('"{0}"' -f $MainScript) -WorkingDirectory $ProjectRoot
