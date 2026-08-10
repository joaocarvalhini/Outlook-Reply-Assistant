# Regista o assistente no Agendador de Tarefas do Windows, de 2 em 2 minutos.
#
#   Abrir o PowerShell COMO ADMINISTRADOR e correr:
#     .\deploy\agendar-windows.ps1
#
#   Para remover:
#     Unregister-ScheduledTask -TaskName "tripat3s-assistente" -Confirm:$false
#
# É a forma de o pôr a correr numa máquina Windows durante a fase de testes.
# Em produção, num servidor Linux, usar antes os ficheiros systemd desta pasta.

$ErrorActionPreference = "Stop"

$nome = "tripat3s-assistente"
$projeto = Split-Path -Parent $PSScriptRoot
$correr = Join-Path $PSScriptRoot "correr.ps1"

if (-not (Test-Path $correr)) {
    throw "Nao encontrei $correr"
}

$acao = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$correr`"" `
    -WorkingDirectory $projeto

# Um gatilho único que se repete indefinidamente. É a forma de conseguir um
# intervalo de 2 minutos no Agendador — não há gatilho "de N em N minutos".
$gatilho = New-ScheduledTaskTrigger -Once -At (Get-Date) `
    -RepetitionInterval (New-TimeSpan -Minutes 2)

$definicoes = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -DontStopIfGoingOnBatteries `
    -AllowStartIfOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10) `
    -MultipleInstances IgnoreNew

# MultipleInstances IgnoreNew é o que impede duas passagens em simultâneo: se
# uma demorar mais do que os 2 minutos, a seguinte é ignorada em vez de correr
# por cima. ExecutionTimeLimit mata uma passagem encravada ao fim de 10 minutos.

if (Get-ScheduledTask -TaskName $nome -ErrorAction SilentlyContinue) {
    Write-Host "A tarefa '$nome' ja existe. A substituir."
    Unregister-ScheduledTask -TaskName $nome -Confirm:$false
}

Register-ScheduledTask `
    -TaskName $nome `
    -Action $acao `
    -Trigger $gatilho `
    -Settings $definicoes `
    -Description "Le a caixa de apoio da tripat3s e prepara rascunhos de resposta. Nunca envia." | Out-Null

Write-Host ""
Write-Host "Registada: $nome (de 2 em 2 minutos)"
Write-Host "Logs em:   $(Join-Path $projeto 'logs')"
Write-Host ""
Write-Host "Correr agora sem esperar:   Start-ScheduledTask -TaskName $nome"
Write-Host "Ver o estado:               Get-ScheduledTask -TaskName $nome"
Write-Host "Parar:                      Disable-ScheduledTask -TaskName $nome"
Write-Host "Remover:                    Unregister-ScheduledTask -TaskName $nome -Confirm:`$false"
