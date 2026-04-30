"""List Gemini models visible to the configured API key."""

import argparse
import os

from dotenv import load_dotenv
from google import genai


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--filter",
        dest="name_filter",
        help="Only show models whose name contains this substring.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Show all visible models, including ones without generateContent support.",
    )
    return parser.parse_args()


def main() -> None:
    load_dotenv(".env")
    args = parse_args()

    with genai.Client(api_key=os.environ["GEMINI_API_KEY"]) as client:
        for model in client.models.list():
            name = getattr(model, "name", "")
            supported = list(getattr(model, "supported_actions", []) or [])
            if args.name_filter and args.name_filter not in name:
                continue
            if not args.all and "generateContent" not in supported:
                continue
            print(name)
            print(f"  supported_actions={','.join(supported) if supported else '-'}")


if __name__ == "__main__":
    main()
