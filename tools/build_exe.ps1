# exe 版（Python 不要・フォルダ形式）をビルドする。
#   powershell -ExecutionPolicy Bypass -File tools\build_exe.ps1
# 出力: dist\FileSortKun\FileSortKun.exe（フォルダごと配布。portable.txt 入りでポータブルモード）
#       dist\FileSortKun-vX.Y.Z-win64.zip（配布用。data フォルダは含めない）
#
# dist\FileSortKun をそのまま使っている場合でも、設定・履歴（data フォルダ）はビルド中に退避して元に戻す。
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$out = Join-Path $root "dist\FileSortKun"
$keep = Join-Path $root "build\data-keep"

# 起動中の exe 版があると上書きできない（データも使用中）ため中止する
$running = Get-Process FileSortKun -ErrorAction SilentlyContinue |
    Where-Object { $_.Path -and $_.Path.StartsWith($out, [StringComparison]::OrdinalIgnoreCase) }
if ($running) {
    throw "dist\FileSortKun の FileSortKun.exe が起動中です。トレイのメニューから「終了」してから再実行してください。"
}

python -m pip install --disable-pip-version-check -q -r requirements.txt -r requirements-dev.txt

$version = (python -c "import filesorter; print(filesorter.__version__)").Trim()
Write-Output "ビルド: FileSortKun v$version"

# ポータブルモードの設定・履歴を退避（PyInstaller は出力フォルダを作り直すため）
$hadData = Test-Path (Join-Path $out "data")
if ($hadData) {
    if (Test-Path $keep) { throw "前回の退避データが残っています: $keep（中身を確認して戻すか削除してください）" }
    New-Item -ItemType Directory -Force (Split-Path $keep) | Out-Null
    Move-Item (Join-Path $out "data") $keep
    Write-Output "data フォルダを退避しました: $keep"
}

try {
    python -m PyInstaller --noconfirm --clean --windowed `
        --name FileSortKun `
        --icon "$root\assets\icon.ico" `
        --add-data "$root\assets;assets" `
        --collect-data sv_ttk `
        --exclude-module unittest --exclude-module pydoc --exclude-module test `
        --distpath "$root\dist" --workpath "$root\build\pyinstaller" --specpath "$root\build" `
        "$root\run.pyw"
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller のビルドに失敗しました" }

    Set-Content -Path (Join-Path $out "portable.txt") -Encoding UTF8 -Value "このファイルがあると、設定と履歴をこのフォルダの data に保存します（ポータブルモード）。`r`n削除すると %APPDATA%\FileSortKun に保存します。"
    Copy-Item README.md, LICENSE -Destination $out -Force

    # 配布用 ZIP（個人の data を含めないよう、退避中に作る）
    $zip = Join-Path $root "dist\FileSortKun-v$version-win64.zip"
    if (Test-Path $zip) { Remove-Item $zip }
    Compress-Archive -Path $out -DestinationPath $zip
    $size = (Get-ChildItem $out -Recurse | Measure-Object Length -Sum).Sum / 1MB
    Write-Output ("完了: {0}（{1:N1} MB）" -f $out, $size)
    Write-Output "配布用 ZIP: $zip"
}
finally {
    if ($hadData -and (Test-Path $keep)) {
        New-Item -ItemType Directory -Force $out | Out-Null
        Move-Item $keep (Join-Path $out "data")
        Write-Output "data フォルダを元に戻しました"
    }
}
