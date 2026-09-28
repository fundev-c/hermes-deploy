@echo off
REM ── Local fallback model for Hermes ────────────────────────────────────────
REM Serves Qwen3-1.7B on an OpenAI-compatible endpoint at
REM http://127.0.0.1:8080/v1, matching the `custom` entry at the end of
REM fallback_providers in config.yaml.
REM
REM --jinja is REQUIRED: without it llama-server ignores the model's chat
REM template and will not emit tool calls at all. Do NOT pass --chat-template;
REM forcing one discards the tools and the conversation.
REM
REM Why Qwen3 and not xLAM-2-1b-fc-r: xLAM emits tool calls as a JSON array,
REM which llama.cpp's peg parser rejects with HTTP 500 ("output does not match
REM the expected peg-native format") even though the call itself is correct.
REM This GGUF is ggml-org's own build, so llama.cpp parses it natively.
REM Bonus: apache-2.0 rather than xLAM's cc-by-nc-4.0.
REM
REM Context capped at 8192 to keep the working set near 1.6 GB on 8 GB of RAM.

set LLAMA_DIR=C:\Users\dlteam\AppData\Local\Microsoft\WinGet\Packages\ggml.llamacpp_Microsoft.Winget.Source_8wekyb3d8bbwe
set MODEL=C:\Users\dlteam\AppData\Local\llmfit\models\Qwen3-1.7B-Q4_K_M.gguf

"%LLAMA_DIR%\llama-server.exe" ^
  -m "%MODEL%" ^
  --alias Qwen3-1.7B ^
  --host 127.0.0.1 --port 8080 ^
  -c 8192 -t 4 -ngl 0 ^
  --jinja ^
  --cache-reuse 256
