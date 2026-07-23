@echo off
setlocal

start "Quant Server" cmd /k "cd /d ""%~dp0server"" && cargo run"
start "Quant Web" cmd /k "cd /d ""%~dp0web"" && bun run dev"

endlocal
