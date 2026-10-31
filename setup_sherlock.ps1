$ErrorActionPreference = "Stop"

$environment = Join-Path $PSScriptRoot ".sherlock-venv"
$python = Join-Path $environment "Scripts\python.exe"

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw "uv não encontrado. Instale o uv ou use Python 3.14 para criar o ambiente isolado do Sherlock."
}

if (-not (Test-Path $python)) {
    uv venv --python 3.14 $environment
    if ($LASTEXITCODE -ne 0) {
        throw "Não foi possível criar o ambiente isolado do Sherlock."
    }
}

uv pip install --python $python "sherlock-project==0.16.2"
if ($LASTEXITCODE -ne 0) {
    throw "Não foi possível instalar o Sherlock."
}

& $python -m sherlock_project --version
if ($LASTEXITCODE -ne 0) {
    throw "Sherlock foi instalado, mas o teste de versão falhou."
}
