@echo off
REM Start the local LLM for the web app's agent chat (the iPhone on the account page).
REM
REM     tools\start_llm.bat
REM
REM llama.cpp's llama-server on http://127.0.0.1:8080 with Qwen3.5-9B (Q4_K_M), the same model
REM and settings as the bot project's start_llm.bat, so only one of the two should run at a time:
REM both use port 8080, and both projects can share whichever one is up.
REM Sampling (Qwen's recommended settings) is sent per request by web\src\lib\server\agent.ts.
REM --jinja is required: it turns on Qwen's chat template, which the agent's tool calls need.

set LLAMA=%LOCALAPPDATA%\Programs\llama.cpp\llama-server.exe
set MODEL=%LOCALAPPDATA%\llama.cpp\models\Qwen3.5-9B-Q4_K_M.gguf

if not exist "%LLAMA%" (
	echo llama-server not found at %LLAMA%
	pause
	exit /b 1
)
if not exist "%MODEL%" (
	echo Model not found at %MODEL%
	pause
	exit /b 1
)

REM Already running (here or from the bot project)? Then there is nothing to start.
curl -s -m 2 http://127.0.0.1:8080/health >nul 2>&1 && (
	echo llama-server is already running on http://127.0.0.1:8080
	"%SystemRoot%\System32\timeout.exe" /t 3 >nul
	exit /b 0
)

title Qwen llama-server :8080
"%LLAMA%" ^
  -m "%MODEL%" ^
  --ctx-size 65536 ^
  --cache-type-k q8_0 --cache-type-v q8_0 ^
  --flash-attn on ^
  --n-gpu-layers 99 --load-mode none ^
  --jinja ^
  --host 127.0.0.1 --port 8080
