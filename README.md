# deploy-testing

Tests for prompt-cache reuse on DeepSeek V4.1 Flash (`deepseek-ai/deepseek-v4.1-flash-1017`). They compare Deploy with other hosts of the same model, along three paths:

1. **Direct API:** raw chat-completion calls.
2. **OpenCode CLI:** `opencode run` sessions that read files with the read tool.
3. **T3 Code → OpenCode:** real T3 Code threads using OpenCode as the agent.

All prompts are synthetic. No real data is sent anywhere.

## Requirements

- Python 3.10 or newer. Only the standard library is used.
- `DEPLOY_API_KEY` for the Deploy routes, and `OPENROUTER_API_KEY` for the comparison hosts.
- For the OpenCode test: the [`opencode`](https://opencode.ai) CLI on your PATH. The test writes its own project config, so you don't need to set up a provider.
- For the T3 test: [T3 Code](https://t3.codes) with its OpenCode provider.

## Run it

```sh
export DEPLOY_API_KEY=...
export OPENROUTER_API_KEY=...

# 1. Direct API: all routes in parallel, 2 runs × 4 prompt sizes × 9 calls each
python3 api_test.py --out results/api
#    or a subset, for example:
python3 api_test.py --routes deploy-global openrouter-deepinfra --runs 1 --out results/api

# 2. OpenCode CLI: a six-turn session per route, 4 runs each
python3 opencode_test.py --out results/opencode

# 3. T3 Code → OpenCode
mkdir -p ~/cache-test-project
python3 t3_test.py prepare --root ~/cache-test-project/t3-cache-test   # prints one task per thread
#    In T3 Code, open ~/cache-test-project as a project and start one thread per printed task,
#    using the OpenCode provider and the model it names. When all threads finish:
python3 t3_test.py extract --root ~/cache-test-project/t3-cache-test --out results/t3

# 4. Optional: counters from your own everyday OpenCode sessions on Deploy (counts only, no prompt text)
python3 session_counters.py --since 2026-11-10 --label my-machine --out results/sessions/my-machine.json

# Score everything, print a summary, and write analysis.json, charts and a request-ID index
python3 analyze.py --out report
```

Each script takes `--help`.

## What each test sends

**Direct API (`api_test.py`).** For each prompt size (about 2k, 8k, 37k and 110k tokens), the test sends:

1. one cold request
2. four immediate identical repeats
3. one repeat after a pause (default 20 seconds)
4. three conversation turns, each adding a short assistant reply and a new question

Every sequence starts with a fresh random marker, so nothing is shared between sequences, sizes, runs or routes.

Deploy routes use `reasoning_effort: "none"`. OpenRouter routes use `reasoning.enabled: false` and are pinned to a single host with `provider.only` and `allow_fallbacks: false`. Responses are capped at 16 tokens.

| Route | Endpoint |
|---|---|
| `deploy-global` | `https://api.deploy.com/api/v1` |
| `deploy-global-pref128` | same, with `cache_preference: 128` |
| `deploy-va1` | `https://api-va1.deploy.com/api/v1` |
| `deploy-sf1` | `https://api-sf1.deploy.com/api/v1` |
| `openrouter-deepinfra`, `-fireworks`, `-together`, `-nebius`, `-parasail` | OpenRouter, pinned to that host |
| `openrouter-deepseek` | OpenRouter, pinned to DeepSeek. Not in the default set: OpenRouter accounts whose data policy excludes providers that train on paid traffic can't reach it |

**OpenCode CLI (`opencode_test.py`).** Each session runs in a fresh temporary project. Three turns read synthetic files with the read tool, and three are short follow-ups. The routes are:

- `deploy`
- `deploy-pref128`
- `opencode-free`, which is OpenCode's own `opencode/deepseek-v4.1-flash-free`
- `openrouter-deepinfra`

Token counts come from OpenCode's database (`~/.local/share/opencode/opencode.db`, or `--db`).

**T3 Code → OpenCode (`t3_test.py`).** Each thread reads five synthetic files one at a time. Threads named `deploy-N` use Deploy, and threads named `free-N` use OpenCode's own endpoint.

## How requests are scored

Each request has a part that could have come from cache. For an identical repeat, that's the whole prompt. In a growing conversation or agent session, it's the previous request's prompt.

- **Reused the cache:** at least 50% of that reusable part came back as cached tokens.
- **Fully missed:** under 10% did.

Cached-token counts come from each provider's `usage.prompt_tokens_details.cached_tokens`, or from OpenCode's `cache.read`.

`report/deploy-request-index.csv` lists every Deploy call with its `x-request-id` and response ID, so individual requests can be traced on the server side.
