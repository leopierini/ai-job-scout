#!/bin/bash
# Starts AI Job Scout. Double-click this file in Finder.
cd "$(dirname "$0")" || exit 1

echo "Starting AI Job Scout… the first launch downloads what it needs and can take a few minutes."

fail() {
  echo "$1"
  # Keep the window open so the message can be read. Skipped when checking the launcher.
  if [ -z "${JOBSCOUT_LAUNCHER_CHECK:-}" ]; then
    read -r -p "Press Enter to close" _
  fi
  exit 1
}

export PATH="$HOME/.local/bin:$PATH"
if ! command -v uv >/dev/null 2>&1; then
  echo "Installing uv, the tool that sets up Python for this app…"
  curl -LsSf https://astral.sh/uv/install.sh | sh || fail "Could not install uv. Check your internet connection and open this file again."
  export PATH="$HOME/.local/bin:$PATH"
  command -v uv >/dev/null 2>&1 || fail "uv was installed but could not be found. Close this window and open this file again."
fi

# The runtime lives next to the app data, outside this folder, so replacing this folder
# with a newer download keeps working.
if [ -n "${JOBSCOUT_HOME:-}" ]; then
  export UV_PROJECT_ENVIRONMENT="$JOBSCOUT_HOME/runtime"
else
  export UV_PROJECT_ENVIRONMENT="$HOME/Library/Application Support/ai-job-scout/runtime"
fi

uv sync --frozen --no-dev --no-editable --python 3.13 --reinstall-package ai-job-scout \
  || fail "Could not set up the app. Check your internet connection. If it still fails, you may have an old version of uv: run \"uv self update\" in a terminal (or reinstall uv from https://docs.astral.sh/uv/), then open this file again."

python="$UV_PROJECT_ENVIRONMENT/bin/python"
if [ -n "${JOBSCOUT_LAUNCHER_CHECK:-}" ]; then
  "$python" -m jobscout.cli paths || fail "The app was set up but could not run."
else
  "$python" -m jobscout.cli start || fail "The app stopped with an error."
fi
