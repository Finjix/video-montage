$procs = Get-CimInstance Win32_Process | Where-Object { $_.Name -match 'JianYing|CapCut|jianying|VideoEditor|lv' -or $_.ExecutablePath -match 'JianyingPro|CapCut' }
"== processes =="
$procs | Select-Object ProcessId, Name, ExecutablePath, CommandLine | Format-List

"== listening ports of those processes =="
$ids = $procs | ForEach-Object { $_.ProcessId }
Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object { $ids -contains $_.OwningProcess } |
  Select-Object LocalAddress, LocalPort, OwningProcess | Sort-Object LocalPort | Format-Table -AutoSize

"== named pipes containing jianying/capcut/lv =="
Get-ChildItem \\.\pipe\ -ErrorAction SilentlyContinue | Where-Object { $_.Name -match 'jianying|capcut|lv|ve_' } | Select-Object Name
