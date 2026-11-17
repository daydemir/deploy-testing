"""Shared settings for the cache tests. Standard library only."""
import os
import random

DEPLOY_MODEL = "deepseek-ai/deepseek-v4.1-flash-1017"
OPENROUTER_MODEL = "deepseek/deepseek-v4.1-flash-1017"

# route id -> (base URL, extra): for Deploy, extra is cache_preference (None = default);
# for OpenRouter, extra is the single provider endpoint the request is pinned to.
API_ROUTES = {
    "deploy-global": ("https://api.deploy.com/api/v1", None),
    "deploy-global-pref128": ("https://api.deploy.com/api/v1", 128),
    "deploy-va1": ("https://api-va1.deploy.com/api/v1", None),
    "deploy-sf1": ("https://api-sf1.deploy.com/api/v1", None),
    "openrouter-deepinfra": ("https://openrouter.ai/api/v1", "deepinfra/fp8"),
    "openrouter-fireworks": ("https://openrouter.ai/api/v1", "fireworks"),
    "openrouter-together": ("https://openrouter.ai/api/v1", "together"),
    "openrouter-nebius": ("https://openrouter.ai/api/v1", "nebius/fp8"),
    "openrouter-parasail": ("https://openrouter.ai/api/v1", "parasail/fp8"),
    "openrouter-deepseek": ("https://openrouter.ai/api/v1", "deepseek"),
}

WORDS = ("amber birch cedar delta elm fern granite harbor iris juniper kite lilac maple north olive "
         "pine quartz river spruce tulip umber violet willow xylem yarrow zinc").split()


def api_key(route):
    name = "DEPLOY_API_KEY" if route.startswith("deploy") else "OPENROUTER_API_KEY"
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"Set {name} in the environment to test {route}.")
    return value


def records(seed, count):
    """Synthetic, inert text: numbered lines of random words."""
    rng = random.Random(seed)
    return [f"record {i:04d}: " + " ".join(rng.choices(WORDS, k=24)) + "." for i in range(count)]
