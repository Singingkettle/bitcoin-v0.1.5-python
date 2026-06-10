# Launches two GUI nodes on localhost: node A (port 18444) and node B
# (port 18445, connected to A). Enable Options -> Generate Bitcoins on A,
# watch both status bars climb, then send coins between the windows.

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { $python = "python" }

Start-Process $python -ArgumentList "run_gui.py", "-datadir=data\nodeA", "-port=18444" -WorkingDirectory $PSScriptRoot
Start-Sleep -Seconds 2
Start-Process $python -ArgumentList "run_gui.py", "-datadir=data\nodeB", "-port=18445", "-connect=127.0.0.1:18444" -WorkingDirectory $PSScriptRoot
