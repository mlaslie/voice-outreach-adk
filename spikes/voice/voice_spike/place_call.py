"""Place one real outbound test call through the deployed spike.

    uv run python -m voice_spike.place_call 1      # dial tester #1 from config/seed/allowlist.yaml

Only numbers listed in the gitignored seed allowlist can be dialled. Credentials
come from config/providers/twilio.yaml ``secret://`` references (Secret Manager),
or from VOA_SECRET_* environment variables for local runs.
"""

from __future__ import annotations

import asyncio
import sys

import yaml
from voa_core.config import find_config_dir, load_config, resolve_secret
from voa_core.logging import mask_phone
from voa_providers.base import AmdOptions, PlaceCallRequest
from voa_providers.twilio import TwilioVoice


async def main(index: int) -> None:
    cfg = load_config()
    seed = yaml.safe_load((find_config_dir() / "seed" / "allowlist.yaml").read_text())
    numbers = [n["phone"] for n in seed.get("numbers", [])]
    if not 1 <= index <= len(numbers):
        raise SystemExit(f"choose 1..{len(numbers)}")
    to = numbers[index - 1]
    tw = cfg.providers["twilio"]
    secret = lambda key: resolve_secret(tw[key], cfg.gcp.project_id)  # noqa: E731
    voice = TwilioVoice(
        account_sid=secret("account_sid"),
        api_key_sid=secret("api_key_sid"),
        api_key_secret=secret("api_key_secret"),
        from_number=tw["from_number"],
    )
    base = cfg.telephony.public_base_url.rstrip("/")
    amd = tw.get("amd", {})
    placed = await voice.place_call(
        PlaceCallRequest(
            to=to,
            answer_url=f"{base}/twilio/answer",
            status_url=f"{base}/twilio/status",
            amd=AmdOptions(
                amd.get("mode", "DetectMessageEnd"),
                amd.get("timeout_s", 55),
                amd.get("speech_end_threshold_ms", 1000),
            )
            if amd.get("enabled")
            else None,
        )
    )
    print(f"placed call {placed.call_id} to {mask_phone(to)}: {placed.status}")


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 1))
