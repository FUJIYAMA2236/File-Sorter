# 起動用ショートカット（ファイル仕分け君.lnk）をプロジェクトフォルダに作成する。
#   powershell -ExecutionPolicy Bypass -File tools\create_shortcut.ps1 [-StartMenu]
# -StartMenu を付けるとスタートメニューにも作成する。
param([switch]$StartMenu)

$root = Split-Path -Parent $PSScriptRoot
$python = (Get-Command python -ErrorAction Stop).Source
$pythonw = Join-Path (Split-Path -Parent $python) "pythonw.exe"
if (-not (Test-Path $pythonw)) { $pythonw = $python }

$targets = @(Join-Path $root "ファイル仕分け君.lnk")
if ($StartMenu) {
    $targets += Join-Path ([Environment]::GetFolderPath("Programs")) "ファイル仕分け君.lnk"
}
$shell = New-Object -ComObject WScript.Shell
foreach ($path in $targets) {
    $lnk = $shell.CreateShortcut($path)
    $lnk.TargetPath = $pythonw
    $lnk.Arguments = '"' + (Join-Path $root "run.pyw") + '"'
    $lnk.WorkingDirectory = $root
    $lnk.IconLocation = (Join-Path $root "assets\icon.ico") + ",0"
    $lnk.Description = "ダウンロードフォルダ自動仕分けツール"
    $lnk.Save()
    Write-Output "作成: $path"
}
