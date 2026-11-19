# Choose models and recover from provider errors

OpenAI requests use `max_completion_tokens`, including the connection test. Custom OpenAI-compatible endpoints and Anthropic retain their own token parameters.

## Browse Ollama models

1. Open **Tools → Ollama Model Installer**.
2. Leave **Model name / search** empty and select **Browse** for newest entries, or enter a search such as `llama` first.
3. Select a result in the compact list, then select **Pull model** explicitly. Selection alone never installs anything.

The catalog requires the backend and internet access. It reads the official Ollama newest-model search page, returns up to 20 results, and may become unavailable if upstream markup changes. Manual model entry and pulling remain available.

## Enable cloud models

Sign in on the machine running Ollama with `ollama signin`, then register models with Pull model or the CLI, for example:

```sh
ollama pull deepseek-v4.1-flash:cloud
ollama pull glm-5.3-flash:cloud
ollama pull kimi-k3.1-pro:cloud
```

Availability depends on your account and the current Ollama catalog. Cloud models execute remotely: questions, history and retrieved course excerpts can leave this machine. Do not submit sensitive content unless authorized.

## Query fallback

Both backend chat and direct-mode search-console queries try installed/registered cloud models first. The selected model leads its cloud or local group. On failure, the next cloud registration is tried, followed by installed local chat models. Duplicate and known embedding-only names are skipped. No example model is assumed installed.

Backend query rewriting follows the same priority and falls back to the original question if every rewrite attempt fails. Answers are buffered until an attempt completes, so partial content from a failed model is never mixed into another model's answer. The successful model is reported in completion metadata or the direct-mode log. Errors, empty output and incomplete streams trigger fallback. An overall request time limit can end the chain before every candidate is tried; the UI reports failure rather than silently returning a partial answer.

## Offline checks

```sh
python -m unittest discover -s tests -p 'test_*.py'
node tests/test_ollama_ui.js
node --check frontend/assets/es_test.js
```

These checks use mocked providers. They do not establish live account access, model availability or visual browser rendering.
