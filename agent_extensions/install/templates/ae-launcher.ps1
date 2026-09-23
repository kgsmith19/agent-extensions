# managed-by: agent-extensions (rendered from templates/ae-launcher.ps1)
if (-not $env:AGENT_EXTENSIONS_DIR) { $env:AGENT_EXTENSIONS_DIR = "{{INSTALL_DIR}}" }
$env:PYTHONPATH = "{{INSTALL_DIR}}$(if ($env:PYTHONPATH) { [IO.Path]::PathSeparator + $env:PYTHONPATH })"
& python -m agent_extensions.install @args
exit $LASTEXITCODE
