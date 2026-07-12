@echo off
rem Daily EWS batch (post-v2 gap G5): runs every configured vertical and logs output.
rem Register with Windows Task Scheduler (see README "Operations") to run each
rem weekday evening after market close.

setlocal enabledelayedexpansion
cd /d "%~dp0.."
set PYTHONUTF8=1
set HF_HUB_DISABLE_SYMLINKS_WARNING=1
if not exist outputs\logs mkdir outputs\logs

for %%V in (steel aluminum) do (
    echo [!date! !time!] running %%V ...
    ".venv\Scripts\python.exe" -m src.pipeline --config "config\%%V.yaml" ^
        >> "outputs\logs\%%V_daily.log" 2>&1
    if errorlevel 1 (
        echo [!date! !time!] %%V FAILED - see outputs\logs\%%V_daily.log
    ) else (
        echo [!date! !time!] %%V done
    )
)
endlocal
