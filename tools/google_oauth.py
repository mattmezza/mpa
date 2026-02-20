#!/usr/bin/env python3
"""One-time OAuth 2.0 authorization for Google CalDAV.

Opens a browser for the user to grant calendar access, then saves the
refresh token to a JSON file that the calendar CLI tools use.

Usage:
    python3 tools/google_oauth.py \
        --client-id  "YOUR_CLIENT_ID" \
        --client-secret "YOUR_CLIENT_SECRET" \
        --token-file data/google_calendar_token.json

The token file is then referenced in config.yml (or the admin UI) as
the provider's `token_file` field.
"""

from __future__ import annotations

import argparse
import hashlib
import http.server
import json
import os
import secrets
import sys
import threading
import urllib.parse
import webbrowser
from base64 import urlsafe_b64encode
from pathlib import Path

import requests

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPES = ["https://www.googleapis.com/auth/calendar"]
REDIRECT_PORT = 8085
REDIRECT_URI = f"http://localhost:{REDIRECT_PORT}"


def _generate_pkce() -> tuple[str, str]:
    """Generate PKCE code_verifier and code_challenge."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


def _build_auth_url(client_id: str, code_challenge: str, state: str) -> str:
    params = {
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "code_challenge_method": "S256",
        "code_challenge": code_challenge,
        "state": state,
    }
    return f"{GOOGLE_AUTH_URL}?{urllib.parse.urlencode(params)}"


def _exchange_code(code: str, client_id: str, client_secret: str, code_verifier: str) -> dict:
    resp = requests.post(
        GOOGLE_TOKEN_URL,
        data={
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": REDIRECT_URI,
            "grant_type": "authorization_code",
            "code_verifier": code_verifier,
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def main():
    parser = argparse.ArgumentParser(description="Authorize Google Calendar via OAuth 2.0")
    parser.add_argument("--client-id", required=True, help="Google OAuth client ID")
    parser.add_argument("--client-secret", required=True, help="Google OAuth client secret")
    parser.add_argument(
        "--token-file",
        default="data/google_calendar_token.json",
        help="Path to save the token JSON (default: data/google_calendar_token.json)",
    )
    args = parser.parse_args()

    code_verifier, code_challenge = _generate_pkce()
    state = secrets.token_urlsafe(32)

    # Simple callback server to capture the authorization code
    auth_code = None
    received_state = None

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            nonlocal auth_code, received_state
            qs = urllib.parse.urlparse(self.path).query
            params = urllib.parse.parse_qs(qs)
            auth_code = params.get("code", [None])[0]
            received_state = params.get("state", [None])[0]

            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            if auth_code:
                self.wfile.write(
                    b"<html><body><h2>Authorization successful!</h2>"
                    b"<p>You can close this tab and return to the terminal.</p>"
                    b"</body></html>"
                )
            else:
                error = params.get("error", ["unknown"])[0]
                self.wfile.write(
                    f"<html><body><h2>Authorization failed: {error}</h2></body></html>".encode()
                )

        def log_message(self, *_args):
            pass  # suppress logs

    server = http.server.HTTPServer(("localhost", REDIRECT_PORT), Handler)
    thread = threading.Thread(target=server.handle_request)
    thread.start()

    auth_url = _build_auth_url(args.client_id, code_challenge, state)
    print(f"Opening browser for authorization...\n")
    print(f"If the browser doesn't open, visit this URL:\n{auth_url}\n")
    webbrowser.open(auth_url)

    thread.join(timeout=120)
    server.server_close()

    if not auth_code:
        print("Error: did not receive authorization code.", file=sys.stderr)
        sys.exit(1)

    if received_state != state:
        print("Error: state mismatch — possible CSRF attack.", file=sys.stderr)
        sys.exit(1)

    print("Exchanging authorization code for tokens...")
    token_data = _exchange_code(auth_code, args.client_id, args.client_secret, code_verifier)

    if "refresh_token" not in token_data:
        print("Warning: no refresh_token received. You may need to revoke access and retry.")
        print(f"Response: {json.dumps(token_data, indent=2)}")
        sys.exit(1)

    # Save token file
    token_file = Path(args.token_file)
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_out = {
        "client_id": args.client_id,
        "client_secret": args.client_secret,
        "refresh_token": token_data["refresh_token"],
        "token_type": "Bearer",
    }
    token_file.write_text(json.dumps(token_out, indent=2))
    print(f"\nToken saved to {token_file}")
    print("Calendar tools will now use OAuth 2.0 for this provider.")


if __name__ == "__main__":
    main()
