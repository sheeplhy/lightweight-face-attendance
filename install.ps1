$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

$PythonVersion = python -c "import platform; print(platform.python_version())"
if ($LASTEXITCODE -ne 0) {
    throw "Python was not found. Install 64-bit Python 3.11 or 3.12 first."
}
if ($PythonVersion -notmatch '^3\.(11|12)\.') {
    throw "Unsupported Python $PythonVersion. Install 64-bit Python 3.11 or 3.12."
}

if (-not (Test-Path -LiteralPath $VenvPython)) {
    python -m venv (Join-Path $ProjectRoot ".venv")
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create the Python virtual environment."
    }
}

& $VenvPython -m pip install -r (Join-Path $ProjectRoot "requirements.txt")
if ($LASTEXITCODE -ne 0) {
    throw "Dependency installation failed. Check network access and Python version."
}
Write-Host "Installation completed. Run .\start.ps1 to launch the application."
