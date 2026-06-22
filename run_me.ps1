# run_me.ps1

[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$EnvName = if ($env:ZGRAPH_ENV) { $env:ZGRAPH_ENV } else { "dev" }
$PassArgs = [System.Collections.Generic.List[string]]::new()

for ($i = 0; $i -lt $args.Count; $i++) {
  $arg = $args[$i]
  if ($arg -eq "--env") {
    if ($i + 1 -ge $args.Count) {
      Write-Host "Error: --env requires a config name, for example: --env dev"
      exit 2
    }
    $EnvName = $args[$i + 1]
    $i += 1
    continue
  }
  if ($arg.StartsWith("--env=")) {
    $EnvName = $arg.Substring("--env=".Length)
    continue
  }
  $PassArgs.Add($arg)
}

function Import-DotEnv {
  param([string]$Path)
  foreach ($line in Get-Content -Path $Path -Encoding UTF8) {
    $trimmed = $line.Trim()
    if (-not $trimmed -or $trimmed.StartsWith("#")) {
      continue
    }
    $equals = $trimmed.IndexOf("=")
    if ($equals -lt 1) {
      continue
    }
    $key = $trimmed.Substring(0, $equals).Trim()
    $value = $trimmed.Substring($equals + 1).Trim()
    if (
      ($value.StartsWith('"') -and $value.EndsWith('"')) -or
      ($value.StartsWith("'") -and $value.EndsWith("'"))
    ) {
      $value = $value.Substring(1, $value.Length - 2)
    }
    [Environment]::SetEnvironmentVariable($key, $value, "Process")
  }
}

function Get-AvailableEnvNames {
  $EnvDir = Join-Path $ScriptDir "env"
  if (-not (Test-Path $EnvDir)) {
    return ""
  }
  $Names = Get-ChildItem -Path $EnvDir -Filter "*.env" | ForEach-Object { $_.BaseName }
  return ($Names -join ", ")
}

$env:ZGRAPH_ENV = $EnvName
$ConfigPath = Join-Path $ScriptDir "env\$EnvName.env"
if (-not (Test-Path $ConfigPath)) {
  Write-Host "Error: zgraph env config not found: $ConfigPath"
  $AvailableEnvNames = Get-AvailableEnvNames
  if ($AvailableEnvNames) {
    Write-Host "Available env configs: $AvailableEnvNames"
  } else {
    Write-Host "No env/*.env files were found. Create env\$EnvName.env or pass --env <name>."
  }
  exit 2
}
Import-DotEnv -Path $ConfigPath

if (-not $env:ZGRAPH_HOME) {
  $env:ZGRAPH_HOME = Join-Path $ScriptDir ".zgraph"
}
if (-not $env:ZGRAPH_DATA_DIR) {
  $env:ZGRAPH_DATA_DIR = Join-Path $env:ZGRAPH_HOME "data"
}
if (-not $env:ZGRAPH_LAYER_CONFIG) {
  $env:ZGRAPH_LAYER_CONFIG = Join-Path $ScriptDir "zgraph.config.default.yaml"
}
if ($PassArgs -contains "--auto-approve") {
  $env:ZGRAPH_AUTO_APPROVE_INTERRUPTS = "true"
}

Write-Host "zgraph config loaded:"
Write-Host "ZGRAPH_ENV          = $env:ZGRAPH_ENV"
Write-Host "BASE_URL            = $env:BASE_URL"
Write-Host "MODEL_NAME          = $env:MODEL_NAME"
Write-Host "LLM_PROVIDER        = $env:LLM_PROVIDER"
Write-Host "MAX_ROUNDS          = $env:MAX_ROUNDS"
Write-Host "ZGRAPH_STREAM       = $env:ZGRAPH_STREAM"
Write-Host "HOST                = $env:HOST"
Write-Host "PORT                = $env:PORT"
Write-Host "ZGRAPH_HOME         = $env:ZGRAPH_HOME"
Write-Host "ZGRAPH_DATA_DIR     = $env:ZGRAPH_DATA_DIR"
Write-Host "ZGRAPH_LAYER_CONFIG = $env:ZGRAPH_LAYER_CONFIG"
Write-Host "ZGRAPH_HOME\apps    = $env:ZGRAPH_HOME\apps"
Write-Host "ZGRAPH_HOME\skills  = $env:ZGRAPH_HOME\skills"
Write-Host "ZGRAPH_AUTO_APPROVE_INTERRUPTS = $env:ZGRAPH_AUTO_APPROVE_INTERRUPTS"
Write-Host "SKILL_SEARCH        = $env:SKILL_SEARCH"
Write-Host "SKILL_TOP_K         = $env:SKILL_TOP_K"
Write-Host "ZGRAPH_TOKENIZER_STRATEGY = $env:ZGRAPH_TOKENIZER_STRATEGY"
Write-Host "TOOL_TOP_K          = $env:TOOL_TOP_K"
Write-Host "TOOL_MIN_SCORE      = $env:TOOL_MIN_SCORE"
Write-Host "ZGRAPH_LOG_LEVEL    = $env:ZGRAPH_LOG_LEVEL"
Write-Host "ZBZN_BASE_URL       = $env:ZBZN_BASE_URL"
Write-Host "WHITELIST           = $env:WHITELIST"
Write-Host "APIKEY              = ******"
Write-Host "EMBEDDING_API_KEY   = ******"
Write-Host "RERANK_API_KEY      = ******"
Write-Host "ZBZN_API_KEY        = ******"
Write-Host "now starting..."

$PythonExe = "C:/environments/miniconda/envs/minidev/python.exe"
if (-not (Test-Path $PythonExe)) {
  $PythonExe = "python"
}

$PythonOk = $true
try {
  & $PythonExe --version *> $null
  if ($LASTEXITCODE -ne 0) {
    $PythonOk = $false
  }
} catch {
  $PythonOk = $false
}
if (-not $PythonOk) {
  Write-Host "Error: Python interpreter not found. Expected C:/environments/miniconda/envs/minidev/python.exe or a 'python' command on PATH."
  exit 2
}

& $PythonExe -c "import langgraph, langchain, langchain_openai, openai, pydantic, yaml" *> $null
if ($LASTEXITCODE -ne 0) {
  Write-Host "installing zgraph dependencies..."
  & $PythonExe -m pip install -r (Join-Path $ScriptDir "requirements.txt")
  if ($LASTEXITCODE -ne 0) {
    Write-Host "Error: dependency installation failed. Check Python, pip, network access, and requirements.txt."
    exit $LASTEXITCODE
  }
}

$MainArgs = $PassArgs.ToArray()
& $PythonExe (Join-Path $ScriptDir "main.py") @MainArgs
exit $LASTEXITCODE
