# Checks Start.bat end to end in check mode: it must prepare its runtime outside the app folder,
# print the app data paths, and keep working after the app folder is replaced by a fresh copy
# (what happens when someone unpacks a newer download over the old one).
$ErrorActionPreference = 'Stop'

$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$work = Join-Path ([System.IO.Path]::GetTempPath()) ("jobscout-check-" + [guid]::NewGuid().ToString('N'))
$app = Join-Path $work 'app'
$appHome = Join-Path $work 'home'
New-Item -ItemType Directory -Path $appHome -Force | Out-Null

function Fail($message) {
  Write-Host "FAIL: $message"
  exit 1
}

function Copy-Repo {
  if (Test-Path $app) { Remove-Item -Recurse -Force $app }
  New-Item -ItemType Directory -Path $app | Out-Null
  # robocopy exit codes below 8 mean success.
  robocopy $root $app /E /XD .git node_modules .venv runtime .artifacts .pytest_cache .ruff_cache /NFL /NDL /NJH /NJS /NP | Out-Null
  if ($LASTEXITCODE -ge 8) { Fail "could not copy the app folder" }
}

function Start-Launcher {
  $env:JOBSCOUT_LAUNCHER_CHECK = '1'
  $env:JOBSCOUT_HOME = $appHome
  $output = & cmd.exe /c "`"$app\Start.bat`" < NUL 2>&1" | Out-String
  $code = $LASTEXITCODE
  Write-Host $output
  if ($code -ne 0) { Fail "the launcher exited with an error" }
  if (-not $output.Contains("App data folder: $appHome")) { Fail "output does not contain 'App data folder: $appHome'" }
  if (-not (Test-Path (Join-Path $appHome 'runtime\Scripts\python.exe'))) { Fail "no runtime found in $appHome\runtime" }
  if ((Join-Path $appHome 'runtime').StartsWith($app)) { Fail "the runtime is inside the app folder" }
  if (Test-Path (Join-Path $app '.venv')) { Fail "the launcher created .venv inside the app folder" }
}

try {
  Write-Host "== first launch =="
  Copy-Repo
  Start-Launcher

  Write-Host "== launch after replacing the app folder with a fresh copy =="
  Copy-Repo
  Start-Launcher

  Write-Host "OK: Start.bat passes before and after the app folder is replaced"
}
finally {
  Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue
}
