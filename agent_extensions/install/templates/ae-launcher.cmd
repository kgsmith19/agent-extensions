@echo off
rem managed-by: agent-extensions (rendered from templates/ae-launcher.cmd)
if not defined AGENT_EXTENSIONS_DIR set "AGENT_EXTENSIONS_DIR={{INSTALL_DIR}}"
if not defined PYTHONPATH set "PYTHONPATH={{INSTALL_DIR}}"
python -m agent_extensions.install %*
exit /b %ERRORLEVEL%
