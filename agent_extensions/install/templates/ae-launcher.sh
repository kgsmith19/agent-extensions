#!/usr/bin/env bash
# managed-by: agent-extensions (rendered from templates/ae-launcher.sh)
AGENT_EXTENSIONS_DIR="{{INSTALL_DIR}}"
export AGENT_EXTENSIONS_DIR
PYTHONPATH="{{INSTALL_DIR}}${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONPATH
exec python3 -m agent_extensions.install "$@"
