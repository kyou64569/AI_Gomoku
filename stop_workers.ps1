# AI Gomoku - kill leftover uvicorn workers, including ORPHANED --reload children.
#
# Why this exists:
#   start.bat runs uvicorn with --reload, which spawns a worker through
#   multiprocessing.spawn. The listening socket belongs to that worker, but
#   netstat / Get-NetTCPConnection report the ORIGINAL parent pid.
#   If the parent dies first (killed directly, or the window closed), the worker
#   is orphaned: the port stays occupied, yet every port-based lookup points at a
#   dead pid, so `taskkill` silently does nothing and stop.bat wrongly reports
#   "ports are clean". The next start.bat then quietly picks another port.
#
#   Identification is done by command line, never by killing python.exe wholesale
#   (that would clobber the user's Jupyter or other projects):
#     1. any python running uvicorn with our app        -> "app.main:app"
#     2. orphaned spawn workers                         -> "spawn_main" AND
#                                                          ParentProcessId is gone
#   A healthy multiprocessing worker always has a live parent, so rule 2 does
#   not touch other projects' workers.
#
# Exit code = number of processes killed (0 = nothing found).

$ErrorActionPreference = 'SilentlyContinue'

$procs = Get-CimInstance Win32_Process -Filter "Name='python.exe'"
if (-not $procs) { exit 0 }

$alive = @{}
foreach ($p in $procs) { $alive[[int]$p.ProcessId] = $true }

$targets = @{}
foreach ($p in $procs) {
    $cl = [string]$p.CommandLine

    if ($cl -like '*app.main:app*') {
        $targets[[int]$p.ProcessId] = $true
        continue
    }

    if ($cl -like '*spawn_main*') {
        $parentAlive = $alive.ContainsKey([int]$p.ParentProcessId)
        if (-not $parentAlive) {
            # Double-check via the command line: only claim workers belonging to
            # a uvicorn parent that is no longer running.
            $targets[[int]$p.ProcessId] = $true
        }
    }
}

$killed = 0
foreach ($pidToKill in ($targets.Keys | Sort-Object)) {
    Stop-Process -Id $pidToKill -Force
    if ($?) {
        # Marker prefix so stop.bat can count kills by grepping the output.
        # Doing it this way avoids depending on how the host propagates the
        # exit code of "exit N" back through cmd.exe.
        Write-Output "GOMOKU-KILL $pidToKill"
        $killed++
    }
}

exit $killed
