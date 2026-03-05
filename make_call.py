#!/usr/bin/env python3
"""
make_call.py - Developer CLI for triggering outbound calls.

Places a call by hitting the server's POST /make-call endpoint.
Reads SERVER_EXTERNAL_URL and ENDPOINT_SECRET from .env automatically.

Usage:
  # Simplest - uses default mock lead (Alex Mitchell)
  python make_call.py --to "+15551234567"

  # With custom lead name
  python make_call.py --to "+15551234567" --lead-name "John Smith"

  # Point at a different server (e.g., deployed on Fly.io)
  python make_call.py --to "+15551234567" --server "https://my-app.fly.dev"

  # With full custom lead from a JSON file
  python make_call.py --to "+15551234567" --lead-file custom_lead.json

Prerequisites:
  - The server must be running: python main.py
  - SERVER_EXTERNAL_URL and ENDPOINT_SECRET must be set in .env (or pass --server/--secret)
"""
import argparse
import json
import os
import sys
import urllib.request
import urllib.error

from dotenv import load_dotenv

load_dotenv()


def main():
    parser = argparse.ArgumentParser(
        description="Place an outbound call via the voice agent server",
    )
    parser.add_argument(
        "--to",
        required=True,
        help="Phone number to call (E.164 format, e.g. +15551234567)",
    )
    parser.add_argument(
        "--server",
        default=None,
        help="Server URL (default: reads SERVER_EXTERNAL_URL from .env)",
    )
    parser.add_argument(
        "--secret",
        default=None,
        help="Endpoint secret for authentication (default: reads ENDPOINT_SECRET from .env)",
    )
    parser.add_argument(
        "--lead-name",
        default=None,
        help='Custom lead name (e.g. "John Smith"). Uses default mock lead for other fields.',
    )
    parser.add_argument(
        "--lead-file",
        default=None,
        help="Path to a JSON file with custom lead data",
    )
    args = parser.parse_args()

    # Resolve server URL
    server_url = args.server or os.getenv("SERVER_EXTERNAL_URL")
    if not server_url:
        print("ERROR: No server URL. Set SERVER_EXTERNAL_URL in .env or use --server")
        sys.exit(1)

    # Resolve endpoint secret
    secret = args.secret or os.getenv("ENDPOINT_SECRET")

    # Build request body
    body = {"to": args.to}

    if args.lead_file:
        try:
            with open(args.lead_file) as f:
                body["lead"] = json.load(f)
        except FileNotFoundError:
            print(f"ERROR: Lead file not found: {args.lead_file}")
            sys.exit(1)
        except json.JSONDecodeError as e:
            print(f"ERROR: Invalid JSON in lead file: {e}")
            sys.exit(1)
    elif args.lead_name:
        # Split name into first/last
        parts = args.lead_name.strip().split(None, 1)
        body["lead"] = {
            "first_name": parts[0],
            "last_name": parts[1] if len(parts) > 1 else "",
        }

    # Make the request
    url = f"{server_url.rstrip('/')}/make-call"
    data = json.dumps(body).encode("utf-8")

    headers = {"Content-Type": "application/json"}
    if secret:
        headers["Authorization"] = f"Bearer {secret}"

    req = urllib.request.Request(url, data=data, headers=headers, method="POST")

    print(f"Calling {args.to} via {url}...")

    try:
        with urllib.request.urlopen(req) as response:
            result = json.loads(response.read().decode("utf-8"))
            print(f"\nCall initiated successfully!")
            print(f"  Call SID:  {result.get('call_sid', 'unknown')}")
            print(f"  Lead ID:  {result.get('lead_id', 'unknown')}")
            print(f"  Status:   {result.get('status', 'unknown')}")
            print(f"\nYour phone should ring shortly. Check server logs for the conversation.")
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8")
        try:
            error_data = json.loads(error_body)
            error_msg = error_data.get("error", error_body)
        except json.JSONDecodeError:
            error_msg = error_body

        if e.code == 401:
            print(f"ERROR: Authentication failed - {error_msg}")
            print("Check your ENDPOINT_SECRET in .env or use --secret")
        elif e.code == 400:
            print(f"ERROR: Bad request - {error_msg}")
        else:
            print(f"ERROR: Server returned {e.code} - {error_msg}")
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"ERROR: Could not connect to server at {url}")
        print(f"  {e.reason}")
        print("\nMake sure the server is running: python main.py")
        sys.exit(1)


if __name__ == "__main__":
    main()
