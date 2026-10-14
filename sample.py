#!/usr/bin/env python3
# ============================================================================
# This file is part of the Benten Authentication and Authorization Platform.
# Copyright (c) 2024 Benten Technologies.
#
# Licensed under the MIT License. See LICENSE in the repository root for full
# license text.
# ============================================================================
"""
Benten sample client for Python: the same requests as the .NET sample
(benten-client-sample), from the command line.

    export BENTEN_API_KEY=bnt_sk_...        # Windows: set BENTEN_API_KEY=bnt_sk_...
    python sample.py                        # interactive menu
    python sample.py --phone 6501234567 notify --line "Document=Q3 report"
    python sample.py --phone 6501234567 login --username alice@example.com
    python sample.py --help                 # every command

The user with that phone number gets a notification in the Benten App and
taps Allow or Deny; the answer is printed here.
"""

import argparse
import os
import sys
from datetime import datetime

from benten import BentenClient, BentenError, ApprovalResult

DEFAULT_COUNTRY = "United States"


def log(message: str) -> None:
    print("[{}] {}".format(datetime.now().strftime("%H:%M:%S"), message), flush=True)


def show(result: ApprovalResult) -> int:
    if result.outcome == "Approved":
        log("[APPROVED] User tapped Allow.")
        return 0
    if result.outcome == "Denied":
        log("[DENIED] User tapped Deny.")
        return 1
    if result.outcome == "TimedOut":
        log("[TIMEOUT] User did not respond within 60 seconds.")
        return 1
    log("[ERROR] {}: {}".format(result.error_code, result.error_message))
    log("[RAW] {}".format(result.raw))
    return 2


# ── Commands ─────────────────────────────────────────────────────────────

def cmd_login(client, a):
    log("Sending login approval request for {}...".format(a.username or "this phone"))
    return show(client.request_login(a.country, a.phone, a.username))


def cmd_pay(client, a):
    card_id = a.card_id
    if not card_id:
        cards = client.list_credit_cards(a.country, a.phone)
        if not cards:
            log("No cards registered for this phone. Register one with: register-card")
            return 2
        card = cards[0]
        if len(cards) > 1 and sys.stdin.isatty():
            for i, c in enumerate(cards, start=1):
                print("  {}. {} {}".format(i, c.get("CardProvider", ""), c.get("Number", "")))
            card = cards[_choose("Card", len(cards)) - 1]
        card_id = card["CreditCardId"]
        log("Using {} {}".format(card.get("CardProvider", ""), card.get("Number", "")))
    log("Sending credit card payment approval request: {} at {}...".format(a.amount, a.retailer))
    return show(client.request_card_payment(card_id, a.amount, a.retailer))


def cmd_iot(client, a):
    log("Sending IoT setting change approval request for {}...".format(a.name))
    return show(client.request_iot_setting(a.name, a.mac, a.change))


def cmd_notify(client, a):
    lines = []
    for item in a.line or ["Document=Q3 Financial Report", "Requested By=Finance Team",
                           "Action=Approve for publication"]:
        label, _, value = item.partition("=")
        lines.append((label.strip(), value.strip()))
    log("Sending generic notification approval request ({})...".format(a.subtype))
    return show(client.request_generic(a.country, a.phone, a.subtype, lines))


def cmd_register_account(client, a):
    client.register_online_account(a.country, a.phone, a.username, a.first, a.last, a.description)
    log("Registered account {} for {}.".format(a.username, a.phone))
    return 0


def cmd_register_card(client, a):
    client.register_credit_card(a.country, a.phone, a.number, a.zip, a.first, a.last, a.description)
    log("Registered card ending in {} for {}.".format(a.number[-4:], a.phone))
    return 0


def cmd_register_iot(client, a):
    client.register_iot_device(a.country, a.phone, a.name, a.mac, a.description)
    log("Registered IoT device {} ({}) for {}.".format(a.name, a.mac, a.phone))
    return 0


def cmd_list(client, a):
    accounts = client.list_online_accounts(a.country, a.phone)
    cards = client.list_credit_cards(a.country, a.phone)
    log("Accounts you registered for {}: {}".format(a.phone, ", ".join(accounts) or "none"))
    if not cards:
        log("Cards you registered for {}: none".format(a.phone))
    for c in cards:
        log("Card: {} {}  (CreditCardId {})".format(c.get("CardProvider", ""), c.get("Number", ""), c.get("CreditCardId", "")))
    return 0


# ── Interactive menu ─────────────────────────────────────────────────────

def _ask(prompt, default=""):
    try:
        value = input("{}{}: ".format(prompt, " [{}]".format(default) if default else "")).strip()
    except EOFError:
        raise SystemExit(0)
    return value or default


def _choose(prompt, count):
    while True:
        value = _ask(prompt, "1")
        if value.isdigit() and 1 <= int(value) <= count:
            return int(value)


def menu(client, a):
    a.country = _ask("Country", a.country)
    a.phone = _ask("Phone number (registered in the Benten App)", a.phone)
    actions = [
        ("Login approval", lambda: cmd_login(client, _ns(a, username=_ask("Username being signed in", "user1@example.com")))),
        ("Credit card payment", lambda: cmd_pay(client, _ns(a, card_id="", amount=_ask("Amount", "$99.99"), retailer=_ask("Retailer", "Sample Retailer")))),
        ("IoT setting change", lambda: cmd_iot(client, _ns(a, name=_ask("Device name", "Thermostat1"), mac=_ask("MAC address", "AB:CD:EF:12:34:56"), change=_ask("Setting change", "Change temperature from 72°F to 68°F")))),
        ("Generic notification", lambda: cmd_notify(client, _ns(a, subtype=_ask("Title", "Document Approval"), line=None))),
        ("Register an account (for login)", lambda: cmd_register_account(client, _ns(a, username=_ask("Username"), first=_ask("First name"), last=_ask("Last name"), description="Python sample"))),
        ("Register a credit card", lambda: cmd_register_card(client, _ns(a, number=_ask("Card number"), zip=_ask("Zip code"), first=_ask("First name"), last=_ask("Last name"), description="Python sample"))),
        ("Register an IoT device", lambda: cmd_register_iot(client, _ns(a, name=_ask("Device name", "Thermostat1"), mac=_ask("MAC address", "AB:CD:EF:12:34:56"), description="Python sample"))),
        ("List what's registered for this phone", lambda: cmd_list(client, a)),
    ]
    while True:
        print()
        for i, (label, _) in enumerate(actions, start=1):
            print("  {}. {}".format(i, label))
        print("  0. Quit")
        choice = _ask("Choose", "0")
        if choice == "0":
            return 0
        if choice.isdigit() and 1 <= int(choice) <= len(actions):
            run(lambda: actions[int(choice) - 1][1]())


def _ns(base, **values):
    ns = argparse.Namespace(**vars(base))
    for k, v in values.items():
        setattr(ns, k, v)
    return ns


def run(action):
    try:
        return action()
    except BentenError as e:
        log("[ERROR] {}".format(e))
        return 2
    except KeyboardInterrupt:
        log("Cancelled.")
        return 130


# ── Arguments ────────────────────────────────────────────────────────────

def parse_args(argv):
    p = argparse.ArgumentParser(description="Benten sample client for Python.")
    p.add_argument("--api-key", default=os.environ.get("BENTEN_API_KEY", ""),
                   help="API key (bnt_sk_...). Default: the BENTEN_API_KEY environment variable.")
    p.add_argument("--server", default=os.environ.get("BENTEN_SERVER_URL", "https://api.bententechnologies.com/v2/"),
                   help="Benten API URL. Default: production.")
    p.add_argument("--country", default=os.environ.get("BENTEN_COUNTRY", DEFAULT_COUNTRY))
    p.add_argument("--phone", default=os.environ.get("BENTEN_PHONE", ""),
                   help="The user's phone number as registered in the Benten App, e.g. 6501234567.")
    sub = p.add_subparsers(dest="command")

    s = sub.add_parser("login", help="Ask the user to approve a sign-in.")
    s.add_argument("--username", default="", help="The username being signed in (registered with register-account).")
    s.set_defaults(func=cmd_login)

    s = sub.add_parser("pay", help="Ask the card holder to approve a payment.")
    s.add_argument("--card-id", default="", help="CreditCardId from 'list'. Default: the first card for --phone.")
    s.add_argument("--amount", default="$99.99")
    s.add_argument("--retailer", default="Sample Retailer")
    s.set_defaults(func=cmd_pay)

    s = sub.add_parser("iot", help="Ask the owner to approve an IoT setting change.")
    s.add_argument("--name", default="Thermostat1")
    s.add_argument("--mac", default="AB:CD:EF:12:34:56")
    s.add_argument("--change", default="Change temperature from 72°F to 68°F")
    s.set_defaults(func=cmd_iot)

    s = sub.add_parser("notify", help="Ask the user to approve anything (needs only --phone).")
    s.add_argument("--subtype", default="Document Approval", help="Title shown on the phone.")
    s.add_argument("--line", action="append", metavar="LABEL=VALUE",
                   help="Up to three lines, e.g. --line \"Amount=$2,000\". Repeat for each line.")
    s.set_defaults(func=cmd_notify)

    s = sub.add_parser("register-account", help="Register the user's account on your site, for login requests.")
    s.add_argument("--username", required=True)
    s.add_argument("--first", required=True)
    s.add_argument("--last", required=True)
    s.add_argument("--description", default="Python sample")
    s.set_defaults(func=cmd_register_account)

    s = sub.add_parser("register-card", help="Register a credit card, for payment requests.")
    s.add_argument("--number", required=True)
    s.add_argument("--zip", required=True)
    s.add_argument("--first", required=True)
    s.add_argument("--last", required=True)
    s.add_argument("--description", default="Python sample")
    s.set_defaults(func=cmd_register_card)

    s = sub.add_parser("register-iot", help="Register an IoT device, for setting-change requests.")
    s.add_argument("--name", default="Thermostat1")
    s.add_argument("--mac", default="AB:CD:EF:12:34:56")
    s.add_argument("--description", default="Python sample")
    s.set_defaults(func=cmd_register_iot)

    s = sub.add_parser("list", help="List the accounts and cards you registered for --phone.")
    s.set_defaults(func=cmd_list)
    return p.parse_args(argv)


def main(argv=None):
    a = parse_args(argv if argv is not None else sys.argv[1:])
    if not a.api_key:
        print("Set BENTEN_API_KEY or pass --api-key. Create a key at https://bententechnologies.com/developers.html")
        return 2
    client = BentenClient(a.api_key, a.server)
    if not a.command:
        return menu(client, a)
    if not a.phone and a.command not in ("pay", "iot"):
        print("Pass --phone (or set BENTEN_PHONE): the user's phone number as registered in the Benten App.")
        return 2
    return run(lambda: a.func(client, a))


if __name__ == "__main__":
    sys.exit(main())
