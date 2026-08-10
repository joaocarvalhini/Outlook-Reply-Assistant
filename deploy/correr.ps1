# Corre uma passagem do assistente e acrescenta a saída ao log.
#
# É este o ficheiro que o Agendador de Tarefas executa, e não o Python
# diretamente, por duas razões: o pythonw.exe descarta o stdout (perdia-se o
# log) e o python.exe faz piscar uma janela de consola de dois em dois minutos,
# o que é insuportável numa máquina de trabalho.

$ErrorActionPreference = "Stop"

# A pasta do projeto é a que está acima desta.
$projeto = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projeto ".venv\Scripts\python.exe"
$script = Join-Path $projeto "assistente.py"
$pastaLogs = Join-Path $projeto "logs"
$log = Join-Path $pastaLogs ("assistente-" + (Get-Date -Format "yyyy-MM") + ".log")

if (-not (Test-Path $pastaLogs)) {
    New-Item -ItemType Directory -Path $pastaLogs | Out-Null
}

if (-not (Test-Path $python)) {
    "$(Get-Date -Format s) | erro | .venv nao encontrado em $python" |
        Out-File -FilePath $log -Append -Encoding utf8
    exit 1
}

Set-Location $projeto
& $python $script 2>&1 | Out-File -FilePath $log -Append -Encoding utf8
exit $LASTEXITCODE
