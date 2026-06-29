#!/usr/bin/env bash
# run_me.sh

set -e

export PYTHONIOENCODING="utf-8"
export PYTHONUTF8="1"

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
ENV_NAME="${ZGRAPH_ENV:-dev}"
PASS_ARGS=()

while [ "$#" -gt 0 ]; do
  case "$1" in
    --env)
      shift
      if [ "$#" -eq 0 ]; then
        echo "Error: --env requires a config name, for example: --env dev" >&2
        exit 2
      fi
      ENV_NAME="$1"
      ;;
    --env=*)
      ENV_NAME="${1#--env=}"
      ;;
    *)
      PASS_ARGS+=("$1")
      ;;
  esac
  shift
done

export ZGRAPH_ENV="$ENV_NAME"
CONFIG_PATH="$SCRIPT_DIR/env/$ENV_NAME.env"
if [ ! -f "$CONFIG_PATH" ]; then
  echo "Error: zgraph env config not found: $CONFIG_PATH" >&2
  if [ -d "$SCRIPT_DIR/env" ]; then
    AVAILABLE_ENVS="$(find "$SCRIPT_DIR/env" -maxdepth 1 -type f -name '*.env' -exec basename {} .env \; | sort | awk 'BEGIN { first=1 } { if (!first) printf ", "; printf "%s", $0; first=0 }')"
    if [ -n "$AVAILABLE_ENVS" ]; then
      echo "Available env configs: $AVAILABLE_ENVS" >&2
    else
      echo "No env/*.env files were found. Create env/$ENV_NAME.env or pass --env <name>." >&2
    fi
  else
    echo "No env directory was found at $SCRIPT_DIR/env." >&2
  fi
  exit 2
fi

set -a
. "$CONFIG_PATH"
set +a

PYTHON_EXE="C:/environments/miniconda/envs/minidev/python.exe"
PYTHON_PATH_MODE="windows"
if [ ! -f "$PYTHON_EXE" ]; then
  if [ -f "/mnt/c/environments/miniconda/envs/minidev/python.exe" ]; then
    PYTHON_EXE="/mnt/c/environments/miniconda/envs/minidev/python.exe"
  else
    PYTHON_EXE="python"
    PYTHON_PATH_MODE="posix"
  fi
fi

SCRIPT_DIR_FOR_PY="$SCRIPT_DIR"
if [ "$PYTHON_PATH_MODE" = "windows" ]; then
  if command -v cygpath >/dev/null 2>&1; then
    SCRIPT_DIR_FOR_PY="$(cygpath -w "$SCRIPT_DIR")"
  elif command -v wslpath >/dev/null 2>&1; then
    SCRIPT_DIR_FOR_PY="$(wslpath -w "$SCRIPT_DIR")"
  fi
fi

if [ "$PYTHON_PATH_MODE" = "windows" ]; then
  : "${ZGRAPH_HOME:=$SCRIPT_DIR_FOR_PY\\.zgraph}"
  : "${ZGRAPH_DATA_DIR:=$ZGRAPH_HOME\\data}"
  : "${ZGRAPH_LAYER_CONFIG:=$SCRIPT_DIR_FOR_PY\\zgraph.config.default.yaml}"
  : "${ZGRAPH_TMP_STORE_PATH:=$ZGRAPH_HOME\\storage}"
  : "${ZGRAPH_MEDIA_TTL_SECONDS:=3600}"
  : "${ZGRAPH_MEDIA_CLEANUP_INTERVAL_SECONDS:=300}"
  : "${ZGRAPH_STORAGE_PROVIDERS:=localfs}"
  APPS_PATH="$ZGRAPH_HOME\\apps"
  SKILLS_PATH="$ZGRAPH_HOME\\skills"
  REQUIREMENTS_PATH="$SCRIPT_DIR_FOR_PY\\requirements.txt"
  MAIN_PATH="$SCRIPT_DIR_FOR_PY\\main.py"
else
  : "${ZGRAPH_HOME:=$SCRIPT_DIR/.zgraph}"
  : "${ZGRAPH_DATA_DIR:=$ZGRAPH_HOME/data}"
  : "${ZGRAPH_LAYER_CONFIG:=$SCRIPT_DIR/zgraph.config.default.yaml}"
  : "${ZGRAPH_TMP_STORE_PATH:=$ZGRAPH_HOME/storage}"
  : "${ZGRAPH_MEDIA_TTL_SECONDS:=3600}"
  : "${ZGRAPH_MEDIA_CLEANUP_INTERVAL_SECONDS:=300}"
  : "${ZGRAPH_STORAGE_PROVIDERS:=localfs}"
  APPS_PATH="$ZGRAPH_HOME/apps"
  SKILLS_PATH="$ZGRAPH_HOME/skills"
  REQUIREMENTS_PATH="$SCRIPT_DIR/requirements.txt"
  MAIN_PATH="$SCRIPT_DIR/main.py"
fi
export ZGRAPH_HOME ZGRAPH_DATA_DIR ZGRAPH_LAYER_CONFIG
export ZGRAPH_TMP_STORE_PATH ZGRAPH_MEDIA_TTL_SECONDS ZGRAPH_MEDIA_CLEANUP_INTERVAL_SECONDS ZGRAPH_STORAGE_PROVIDERS

for arg in "${PASS_ARGS[@]}"; do
  if [ "$arg" = "--auto-approve" ]; then
    export ZGRAPH_AUTO_APPROVE_INTERRUPTS="true"
  fi
done

echo "zgraph config loaded:"
echo "ZGRAPH_ENV          = $ZGRAPH_ENV"
echo "BASE_URL            = $BASE_URL"
echo "MODEL_NAME          = $MODEL_NAME"
echo "LLM_PROVIDER        = $LLM_PROVIDER"
echo "MAX_ROUNDS          = $MAX_ROUNDS"
echo "ZGRAPH_STREAM       = $ZGRAPH_STREAM"
echo "HOST                = $HOST"
echo "PORT                = $PORT"
echo "ZGRAPH_HOME         = $ZGRAPH_HOME"
echo "ZGRAPH_DATA_DIR     = $ZGRAPH_DATA_DIR"
echo "ZGRAPH_LAYER_CONFIG = $ZGRAPH_LAYER_CONFIG"
echo "ZGRAPH_HOME/apps    = $APPS_PATH"
echo "ZGRAPH_HOME/skills  = $SKILLS_PATH"
echo "ZGRAPH_AUTO_APPROVE_INTERRUPTS = $ZGRAPH_AUTO_APPROVE_INTERRUPTS"
echo "SKILL_SEARCH        = $SKILL_SEARCH"
echo "SKILL_TOP_K         = $SKILL_TOP_K"
echo "ZGRAPH_TOKENIZER_STRATEGY = $ZGRAPH_TOKENIZER_STRATEGY"
echo "TOOL_TOP_K          = $TOOL_TOP_K"
echo "TOOL_MIN_SCORE      = $TOOL_MIN_SCORE"
echo "ZGRAPH_LOG_LEVEL    = $ZGRAPH_LOG_LEVEL"
echo "ZBZN_BASE_URL       = $ZBZN_BASE_URL"
echo "WHITELIST           = $WHITELIST"
echo "APIKEY              = ******"
echo "EMBEDDING_API_KEY   = ******"
echo "RERANK_API_KEY      = ******"
echo "ZBZN_API_KEY        = ******"
echo "now starting..."

if ! "$PYTHON_EXE" --version > /dev/null 2>&1; then
  echo "Error: Python interpreter not found. Expected C:/environments/miniconda/envs/minidev/python.exe, /mnt/c/environments/miniconda/envs/minidev/python.exe, or a python command on PATH." >&2
  exit 2
fi

"$PYTHON_EXE" -c "import langgraph, langchain, langchain_openai, openai, pydantic, yaml" > /dev/null 2>&1 || {
  echo "installing zgraph dependencies..."
  "$PYTHON_EXE" -m pip install -r "$REQUIREMENTS_PATH" || {
    echo "Error: dependency installation failed. Check Python, pip, network access, and requirements.txt." >&2
    exit 1
  }
}

"$PYTHON_EXE" "$MAIN_PATH" "${PASS_ARGS[@]}"
