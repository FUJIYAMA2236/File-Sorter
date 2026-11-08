# exe 版（Python 不要・フォルダ形式）をビルドする。
#   powershell -ExecutionPolicy Bypass -File tools\build_exe.ps1
# 出力: dist\FileSortKun\FileSortKun.exe（フォルダごと配布。portable.txt 入りでポータブルモード）
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

python -m pip install --disable-pip-version-check -q -r requirements.txt -r requirements-dev.txt

$version = (python -c "import filesorter; print(filesorter.__version__)").Trim()
Write-Output "ビルド: FileSortKun v$version"

python -m PyInstaller --noconfirm --clean --windowed `
    --name FileSortKun `
    --icon "$root\assets\icon.ico" `
    --add-data "$root\assets;assets" `
    --collect-data sv_ttk `
    --exclude-module unittest --exclude-module pydoc --exclude-module test `
    --distpath "$root\dist" --workpath "$root\build\pyinstaller" --specpath "$root\build" `
    "$root\run.pyw"
if ($LASTEXITCODE -ne 0) { throw "PyInstaller のビルドに失敗しました" }

$out = Join-Path $root "dist\FileSortKun"
Set-Content -Path (Join-Path $out "portable.txt") -Encoding UTF8 -Value "このファイルがあると、設定と履歴をこのフォルダの data に保存します（ポータブルモード）。`r`n削除すると %APPDATA%\FileSortKun に保存します。"
Copy-Item README.md, LICENSE -Destination $out -Force

$zip = Join-Path $root "dist\FileSortKun-v$version-win64.zip"
if (Test-Path $zip) { Remove-Item $zip }
Compress-Archive -Path $out -DestinationPath $zip
$size = (Get-ChildItem $out -Recurse | Measure-Object Length -Sum).Sum / 1MB
Write-Output ("完了: {0}（{1:N1} MB）" -f $out, $size)
Write-Output "配布用 ZIP: $zip"
