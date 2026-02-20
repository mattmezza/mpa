#!/usr/bin/env python3
"""Contacts CLI — query providers directly (Google + CardDAV).

Usage examples:
  python3 /app/tools/contacts.py list --provider google --output json
  python3 /app/tools/contacts.py search --provider icloud --query "Alice" --output json
  python3 /app/tools/contacts.py get --provider google --id people/c123 --output json
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

import requests
import vobject
import yaml
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.config import _resolve_env_vars  # noqa: E402
from tools.contacts_auth import get_google_access_token  # noqa: E402


def _load_contacts_providers_from_db(db_path: str) -> dict[str, dict]:
    try:
        db = sqlite3.connect(db_path)
        row = db.execute(
            "SELECT value FROM config WHERE key = ?", ("contacts.providers",)
        ).fetchone()
        db.close()
    except Exception:
        return {}
    if not row:
        return {}
    try:
        providers = json.loads(row[0])
    except Exception:
        return {}
    if not isinstance(providers, list):
        return {}
    return {p.get("name", ""): p for p in providers if isinstance(p, dict)}


def _load_contacts_providers(
    config_path: str = "config.yml", db_path: str = "data/config.db"
) -> dict[str, dict]:
    providers = _load_contacts_providers_from_db(db_path)
    if providers:
        return providers
    load_dotenv()
    path = Path(config_path)
    if not path.exists():
        print("Error: config.yml not found", file=sys.stderr)
        sys.exit(1)
    raw = yaml.safe_load(path.read_text()) or {}
    resolved = _resolve_env_vars(raw)
    providers = resolved.get("contacts", {}).get("providers", [])
    return {p.get("name", ""): p for p in providers if isinstance(p, dict)}


def _flatten_vcard(card: vobject.base.Component) -> dict[str, object]:
    fn = ""
    if hasattr(card, "fn"):
        fn = str(card.fn.value)
    names = []
    if hasattr(card, "n"):
        n = card.n.value
        parts = [n.given, n.additional, n.family]
        names = [p for p in parts if p]
    phones = []
    if hasattr(card, "tel"):
        tel_items = card.tel if isinstance(card.tel, list) else [card.tel]
        for tel in tel_items:
            phones.append(str(tel.value))
    emails = []
    if hasattr(card, "email"):
        email_items = card.email if isinstance(card.email, list) else [card.email]
        for em in email_items:
            emails.append(str(em.value))
    return {
        "full_name": fn,
        "name_parts": names,
        "phones": phones,
        "emails": emails,
    }


def _carddav_list(provider: dict) -> list[dict[str, object]]:
    import caldav

    client = caldav.DAVClient(
        url=provider.get("url", ""),
        username=provider.get("username", ""),
        password=provider.get("password", ""),
    )
    principal = client.principal()
    books = principal.addressbooks()
    contacts: list[dict[str, object]] = []
    for book in books:
        for obj in book.contacts():
            try:
                card = vobject.readOne(obj.data)
            except Exception:
                continue
            data = _flatten_vcard(card)
            data["id"] = obj.url
            data["source"] = provider.get("name", "")
            contacts.append(data)
    return contacts


def _carddav_search(provider: dict, query: str) -> list[dict[str, object]]:
    query_lower = query.lower()
    return [
        c
        for c in _carddav_list(provider)
        if query_lower in (c.get("full_name") or "").lower()
        or any(query_lower in str(p).lower() for p in c.get("phones", []))
        or any(query_lower in str(e).lower() for e in c.get("emails", []))
    ]


def _carddav_get(provider: dict, contact_id: str) -> dict[str, object] | None:
    import caldav

    client = caldav.DAVClient(
        url=provider.get("url", ""),
        username=provider.get("username", ""),
        password=provider.get("password", ""),
    )
    obj = caldav.objects.Contact(client=client, url=contact_id)
    try:
        obj.load()
    except Exception:
        return None
    card = vobject.readOne(obj.data)
    data = _flatten_vcard(card)
    data["id"] = contact_id
    data["source"] = provider.get("name", "")
    return data


def _google_headers(db_path: str) -> dict[str, str]:
    token = get_google_access_token(db_path=db_path)
    return {"Authorization": f"Bearer {token}"}


def _google_list(provider: dict, db_path: str) -> list[dict[str, object]]:
    url = "https://people.googleapis.com/v1/people/me/connections"
    params = {
        "personFields": "names,emailAddresses,phoneNumbers",
        "pageSize": 1000,
    }
    contacts: list[dict[str, object]] = []
    page_token = None
    while True:
        if page_token:
            params["pageToken"] = page_token
        resp = requests.get(url, headers=_google_headers(db_path), params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        for person in data.get("connections", []) or []:
            contacts.append(_google_person_to_contact(person, provider))
        page_token = data.get("nextPageToken")
        if not page_token:
            break
    return contacts


def _google_person_to_contact(person: dict, provider: dict) -> dict[str, object]:
    names = person.get("names") or []
    full_name = names[0].get("displayName") if names else ""
    phones = [p.get("value") for p in (person.get("phoneNumbers") or []) if p.get("value")]
    emails = [e.get("value") for e in (person.get("emailAddresses") or []) if e.get("value")]
    return {
        "id": person.get("resourceName", ""),
        "full_name": full_name or "",
        "phones": phones,
        "emails": emails,
        "source": provider.get("name", ""),
    }


def _google_search(provider: dict, query: str, db_path: str) -> list[dict[str, object]]:
    url = "https://people.googleapis.com/v1/people:searchContacts"
    params = {
        "query": query,
        "readMask": "names,emailAddresses,phoneNumbers",
        "pageSize": 30,
    }
    resp = requests.get(url, headers=_google_headers(db_path), params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    results = data.get("results") or []
    contacts = []
    for result in results:
        person = result.get("person") or {}
        contacts.append(_google_person_to_contact(person, provider))
    return contacts


def _google_get(provider: dict, contact_id: str, db_path: str) -> dict[str, object] | None:
    url = f"https://people.googleapis.com/v1/{contact_id}"
    params = {"personFields": "names,emailAddresses,phoneNumbers"}
    resp = requests.get(url, headers=_google_headers(db_path), params=params, timeout=30)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return _google_person_to_contact(resp.json(), provider)


def _select_provider(providers: dict[str, dict], name: str) -> dict:
    if name not in providers:
        available = ", ".join(sorted(providers.keys())) if providers else "(none configured)"
        print(f"Error: provider '{name}' not found. Available: {available}", file=sys.stderr)
        sys.exit(1)
    return providers[name]


def _is_google(provider: dict) -> bool:
    return provider.get("type") == "google_contacts"


def main() -> None:
    parser = argparse.ArgumentParser(description="Contacts CLI")
    parser.add_argument("--config", default="config.yml", help="Path to config.yml")
    parser.add_argument("--db", default="data/config.db", help="Path to config DB")
    sub = parser.add_subparsers(dest="cmd", required=True)

    list_cmd = sub.add_parser("list", help="List all contacts")
    list_cmd.add_argument("--provider", "-p", required=True)
    list_cmd.add_argument("--output", "-o", choices=["json", "text"], default="json")

    search_cmd = sub.add_parser("search", help="Search contacts")
    search_cmd.add_argument("--provider", "-p", required=True)
    search_cmd.add_argument("--query", "-q", required=True)
    search_cmd.add_argument("--output", "-o", choices=["json", "text"], default="json")

    get_cmd = sub.add_parser("get", help="Get contact details")
    get_cmd.add_argument("--provider", "-p", required=True)
    get_cmd.add_argument("--id", required=True)
    get_cmd.add_argument("--output", "-o", choices=["json", "text"], default="json")

    args = parser.parse_args()
    providers = _load_contacts_providers(args.config, args.db)
    provider = _select_provider(providers, args.provider)

    if args.cmd == "list":
        if _is_google(provider):
            results = _google_list(provider, db_path=args.db)
        else:
            results = _carddav_list(provider)
    elif args.cmd == "search":
        if _is_google(provider):
            results = _google_search(provider, args.query, db_path=args.db)
        else:
            results = _carddav_search(provider, args.query)
    else:
        if _is_google(provider):
            results = _google_get(provider, args.id, db_path=args.db)
        else:
            results = _carddav_get(provider, args.id)

    if args.output == "json":
        print(json.dumps(results, indent=2, ensure_ascii=False))
    else:
        if isinstance(results, list):
            for item in results:
                name = item.get("full_name") or "(no name)"
                phones = ", ".join(item.get("phones", []))
                emails = ", ".join(item.get("emails", []))
                print(f"{name}  {phones}  {emails}")
        elif isinstance(results, dict):
            print(json.dumps(results, indent=2, ensure_ascii=False))
        else:
            print("Not found")


if __name__ == "__main__":
    main()
