@echo off
chcp 65001 >nul 2>&1
setlocal enabledelayedexpansion
title 大肥鲸洲琴工具包 - 创建快捷方式

set "HERE=%~dp0"
set "EXE=%HERE%大肥鲸洲琴工具包.exe"
set "LINKNAME=大肥鲸洲琴工具包.lnk"

if not exist "%EXE%" (
    echo.
    echo   [错误] 没有找到 大肥鲸洲琴工具包.exe
    echo   请把这个脚本放在与 大肥鲸洲琴工具包.exe 相同的文件夹里再运行。
    echo.
    pause
    exit /b 1
)

echo.
echo   大肥鲸洲琴工具包 - 创建快捷方式
echo   ----------------------------------------
echo   程序位置: %EXE%
echo.

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ws = New-Object -ComObject WScript.Shell;" ^
  "$exe = '%EXE%'; $wd = '%HERE%';" ^
  "$targets = @();" ^
  "$targets += [Environment]::GetFolderPath('Desktop');" ^
  "$targets += (Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs');" ^
  "foreach ($dir in $targets) {" ^
  "  if (-not (Test-Path $dir)) { continue }" ^
  "  $lnk = $ws.CreateShortcut((Join-Path $dir '%LINKNAME%'));" ^
  "  $lnk.TargetPath = $exe;" ^
  "  $lnk.WorkingDirectory = $wd;" ^
  "  $lnk.IconLocation = \"$exe,0\";" ^
  "  $lnk.Description = '三角洲行动口琴自动演奏';" ^
  "  $lnk.Save();" ^
  "  Write-Host ('  已创建: ' + (Join-Path $dir '%LINKNAME%'));" ^
  "}"

if errorlevel 1 (
    echo.
    echo   [失败] 创建快捷方式时出错。
    echo   可以手动操作: 右键 DeltaHarp.exe -^> 发送到 -^> 桌面快捷方式
    echo.
) else (
    echo.
    echo   完成！桌面上和开始菜单里都能找到「DeltaHarp」。
    echo.
)
pause
