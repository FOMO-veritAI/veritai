<#
.SYNOPSIS
  Sobe o FOMO + VeritAI no Kubernetes local e abre um túnel HTTPS grátis do Cloudflare.

.DESCRIPTION
  Custo zero: roda no Kubernetes do Docker Desktop e fica online só enquanto este script estiver aberto.
  O endereço do túnel (https://....trycloudflare.com) muda a cada execução.

    .\deploy\demo-local.ps1                      # sobe e abre o túnel
    .\deploy\demo-local.ps1 -AtualizarFrontend   # também aponta o frontend do GitHub Pages para o túnel
    .\deploy\demo-local.ps1 -SemTunel            # só no seu PC, em http://127.0.0.1:8000
    .\deploy\demo-local.ps1 -MostrarChaves       # chaves do publisher e do revisor (só no seu terminal)
    .\deploy\demo-local.ps1 -Apagar              # apaga tudo do cluster, inclusive as notícias

  As chaves são geradas na primeira execução e ficam só no Secret do cluster.
#>
param(
    [switch]$AtualizarFrontend,
    [switch]$SemTunel,
    [switch]$MostrarChaves,
    [switch]$Apagar,
    [string]$Contexto = "docker-desktop",
    [int]$Porta = 8000
)
# "Continue": no Windows PowerShell 5.1, com a saída redirecionada, o stderr do kubectl vira erro e,
# com "Stop", derrubaria o script. Os comandos nativos são conferidos pelo código de saída (Conferir).
$ErrorActionPreference = "Continue"
$ns = "fomo-local"
$manifesto = Join-Path $PSScriptRoot "k8s\local.yaml"

function Exigir($comando, $dica) {
    if (-not (Get-Command $comando -ErrorAction SilentlyContinue)) { throw "Falta o comando '$comando'. $dica" }
}

function Existe($argumentos) {
    & kubectl @argumentos 2>$null | Out-Null
    return $LASTEXITCODE -eq 0
}

function Conferir($mensagem) {
    if ($LASTEXITCODE -ne 0) { throw $mensagem }
}

function Chave {
    $bytes = New-Object byte[] 32
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    return [Convert]::ToBase64String($bytes).TrimEnd("=").Replace("+", "-").Replace("/", "_")
}

Exigir kubectl "Ative o Kubernetes no Docker Desktop (Settings > Kubernetes > Enable Kubernetes)."
# Nunca aplica em outro cluster por engano.
if ((kubectl config current-context) -ne $Contexto) {
    throw "O kubectl está no contexto '$(kubectl config current-context)'. Rode: kubectl config use-context $Contexto"
}

if ($Apagar) {
    kubectl delete namespace $ns --ignore-not-found
    Conferir "Não foi possível apagar o namespace $ns."
    return
}

if ($MostrarChaves) {
    foreach ($nome in "TAKTA_PUBLISHER_KEY", "TAKTA_REVIEWER_KEY") {
        $valor = kubectl -n $ns get secret fomo-chaves -o "jsonpath={.data.$nome}"
        "{0}: {1}" -f $nome, [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($valor))
    }
    return
}

if (-not $SemTunel) { Exigir cloudflared "Instale com: winget install Cloudflare.cloudflared" }
if ($AtualizarFrontend) { Exigir gh "Instale o GitHub CLI e rode: gh auth login" }

# O manifesto cria o namespace; a API do FOMO espera o Secret, criado logo em seguida.
kubectl apply -f $manifesto
Conferir "Falha ao aplicar $manifesto."
if (-not (Existe @("-n", $ns, "get", "secret", "fomo-chaves"))) {
    # Pela entrada padrão: as chaves não aparecem na linha de comando nem vão para arquivo.
    $secret = "apiVersion: v1`nkind: Secret`nmetadata:`n  name: fomo-chaves`n  namespace: $ns`nstringData:`n  TAKTA_PUBLISHER_KEY: $(Chave)`n  TAKTA_REVIEWER_KEY: $(Chave)`n"
    $secret | kubectl apply -f - | Out-Null
    $secret = $null
    Conferir "Não foi possível criar as chaves."
    Write-Host "Chaves novas criadas. Para vê-las: .\deploy\demo-local.ps1 -MostrarChaves"
}
Write-Host "Esperando os pods (na primeira vez, a imagem de cerca de 3,7 GB é baixada)..."
kubectl -n $ns rollout status deploy/veritai --timeout=900s
Conferir "A VeritAI não ficou pronta. Veja: kubectl -n $ns describe pods"
kubectl -n $ns rollout status deploy/fomo-api --timeout=300s
Conferir "A API do FOMO não ficou pronta. Veja: kubectl -n $ns describe pods"

$encaminhamento = Start-Process kubectl -ArgumentList "-n", $ns, "port-forward", "svc/fomo-api", "$($Porta):8000" -PassThru -WindowStyle Hidden -ErrorAction Stop
$tunel = $null
try {
    $pronto = $false
    for ($i = 0; $i -lt 30 -and -not $pronto; $i++) {
        try { $pronto = (Invoke-WebRequest "http://127.0.0.1:$Porta/health" -UseBasicParsing -TimeoutSec 3).StatusCode -eq 200 } catch { Start-Sleep 1 }
    }
    if (-not $pronto) { throw "A API do FOMO não respondeu em http://127.0.0.1:$Porta/health." }
    Write-Host "API do FOMO no seu PC: http://127.0.0.1:$Porta"

    if ($SemTunel) {
        Write-Host "Ctrl+C para encerrar."
        Wait-Process -Id $encaminhamento.Id
        return
    }

    $log = Join-Path $env:TEMP "fomo-cloudflared.log"
    Remove-Item $log -ErrorAction SilentlyContinue
    $tunel = Start-Process cloudflared -ArgumentList "tunnel", "--no-autoupdate", "--url", "http://127.0.0.1:$Porta" -PassThru -WindowStyle Hidden -RedirectStandardError $log -ErrorAction Stop
    $url = $null
    for ($i = 0; $i -lt 60 -and -not $url; $i++) {
        Start-Sleep 1
        if (Test-Path $log) {
            $achado = Select-String -Path $log -Pattern "https://[a-z0-9-]+\.trycloudflare\.com" | Select-Object -First 1
            if ($achado) { $url = $achado.Matches[0].Value }
        }
    }
    if (-not $url) { throw "O cloudflared não informou o endereço do túnel (veja $log)." }
    Write-Host ""
    Write-Host "API do FOMO online: $url" -ForegroundColor Green

    if ($AtualizarFrontend) {
        gh variable set FOMO_API_URL --body $url -R FOMO-veritAI/fomo
        Conferir "Não foi possível gravar FOMO_API_URL no repositório fomo."
        gh workflow run pages.yml -R FOMO-veritAI/fomo
        Conferir "Não foi possível disparar o workflow do Pages no repositório fomo."
        Write-Host "Frontend sendo publicado com esse endereço; em uns 2 min: https://fomo-veritai.github.io/fomo/"
    } else {
        Write-Host "Para o frontend usar este endereço, rode de novo com -AtualizarFrontend."
    }
    Write-Host "Online enquanto esta janela estiver aberta. Ctrl+C para encerrar."
    Wait-Process -Id $tunel.Id
}
finally {
    foreach ($processo in $tunel, $encaminhamento) {
        if ($processo -and -not $processo.HasExited) { Stop-Process -Id $processo.Id -Force }
    }
}
