#!/usr/bin/env bash
# Envia o código para o servidor, mas só depois de confirmar que passa nos
# testes. Substitui o "git archive | ssh" feito à mão, que não tinha nenhum
# gate antes de escrever em produção -- ver Finding M-5.
#
#   deploy/enviar.sh                                 usa os valores por omissão abaixo
#   SERVIDOR=root@1.2.3.4 DESTINO=/opt/outra deploy/enviar.sh
#
# Corre a partir da raiz do repositório (onde está o .git). As duas
# verificações são gratuitas e demoram menos de um segundo -- não há razão
# de custo para as saltar, e é precisamente a ausência delas que o finding
# aponta.
#
# Não substitui o eval.py completo (esse gasta créditos e corre-se à parte,
# quando há alteração ao prompt ou à base de conhecimento -- ver
# docs/05-reliability/qa.md). Isto é só o mínimo que devia impedir sempre um
# deploy: o código corre, e a triagem determinística não regrediu.

set -euo pipefail

SERVIDOR="${SERVIDOR:-root@2.28.66.72}"
DESTINO="${DESTINO:-/opt/assistente}"
UTILIZADOR_REMOTO="${UTILIZADOR_REMOTO:-assistente}"
PYTHON="${PYTHON:-.venv/Scripts/python.exe}"

if [ ! -f "assistente.py" ]; then
  echo "Corre isto a partir da raiz do repositório (onde está o assistente.py)." >&2
  exit 1
fi

if [ ! -x "$PYTHON" ] && ! command -v "$PYTHON" >/dev/null 2>&1; then
  echo "Não encontrei o Python em '$PYTHON'. Define PYTHON=... se o venv estiver noutro sítio." >&2
  exit 1
fi

echo "== 1/3 · testes unitários =="
if ! "$PYTHON" -m unittest test_assistente -q; then
  echo
  echo "ABORTADO: os testes unitários falharam. Nada foi enviado para $SERVIDOR." >&2
  exit 1
fi

echo
echo "== 2/3 · triagem determinística (eval.py --triagem, grátis) =="
if ! "$PYTHON" eval.py --triagem; then
  echo
  echo "ABORTADO: a triagem regrediu. Nada foi enviado para $SERVIDOR." >&2
  exit 1
fi

echo
echo "== 3/4 · impacto na cache da Anthropic =="
# Só o PROMPT e a knowledge/ entram no prefixo em cache. Um deploy que mexa em
# docs, testes ou ferramentas satélite não invalida nada e é gratuito; um que
# mexa no prompt obriga a reescrever as ~31K do prefixo -- duas vezes, porque o
# núcleo e o dossiê têm entradas separadas (medido a 31/08/2026, ver
# docs/06-engineering/cost-optimization.md). Vale ~0,13 $ de cada vez.
#
# O \r\n é normalizado: o checkout no Windows tem CRLF e o servidor recebe LF
# via git archive, e sem isto a impressão digital nunca batia certo.
impressao() {
  "$1" -c "
import hashlib, pathlib
import assistente
h = hashlib.sha256(assistente.PROMPT.encode())
for f in sorted(pathlib.Path('knowledge').glob('*.md')):
    h.update(f.read_bytes().replace(b'\r\n', b'\n'))
print(h.hexdigest()[:12])
" 2>/dev/null
}

local_impressao="$(impressao "$PYTHON" || true)"
remota_impressao="$(ssh "$SERVIDOR" "cd '$DESTINO' 2>/dev/null && sudo -u '$UTILIZADOR_REMOTO' .venv/bin/python -c \"
import hashlib, pathlib
import assistente
h = hashlib.sha256(assistente.PROMPT.encode())
for f in sorted(pathlib.Path('knowledge').glob('*.md')):
    h.update(f.read_bytes().replace(b'\r\n', b'\n'))
print(h.hexdigest()[:12])
\"" 2>/dev/null || true)"

if [ -z "$local_impressao" ] || [ -z "$remota_impressao" ]; then
  echo "Não consegui comparar o prompt (local='$local_impressao' remoto='$remota_impressao')."
  echo "Segue na mesma -- isto é informativo, não é um gate."
elif [ "$local_impressao" = "$remota_impressao" ]; then
  echo "Prompt inalterado ($local_impressao) -- a cache continua quente, este deploy é grátis."
else
  echo "Prompt ALTERADO ($remota_impressao -> $local_impressao)."
  echo "A cache vai ser reescrita nas próximas passagens: ~0,13 \$ (duas entradas de ~31K)."
  echo
  echo "Se tiveres mais alterações à base de conhecimento para hoje, agrupa-as num"
  echo "só deploy em vez de uma de cada vez -- cada deploy separado paga isto outra"
  echo "vez. A 31/08/2026, 6 deploys espalhados pelo dia custaram 0,77 \$, um quarto"
  echo "da fatura desse dia."
fi

echo
echo "== 4/4 · a enviar para $SERVIDOR:$DESTINO =="
git archive HEAD | ssh "$SERVIDOR" "
  set -e
  tar -x -C '$DESTINO'
  chown -R '$UTILIZADOR_REMOTO:$UTILIZADOR_REMOTO' '$DESTINO'
"

echo
echo "Enviado. O .env e o assistente.db não fazem parte do git archive e ficaram intocados."
echo "Se requirements.txt mudou, falta correr o pip install no venv do servidor."
