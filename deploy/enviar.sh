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
echo "== 3/3 · a enviar para $SERVIDOR:$DESTINO =="
git archive HEAD | ssh "$SERVIDOR" "
  set -e
  tar -x -C '$DESTINO'
  chown -R '$UTILIZADOR_REMOTO:$UTILIZADOR_REMOTO' '$DESTINO'
"

echo
echo "Enviado. O .env e o assistente.db não fazem parte do git archive e ficaram intocados."
echo "Se requirements.txt mudou, falta correr o pip install no venv do servidor."
