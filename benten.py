# ============================================================================
# This file is part of the Benten Authentication and Authorization Platform.
# Copyright (c) 2024 Benten Technologies.
#
# Licensed under the MIT License. See LICENSE in the repository root for full
# license text.
# ============================================================================
"""
A small Python client for the Benten API.

Benten talks plain HTTPS and WebSockets with JSON, so any language can use
it. This module does what the .NET SDK (Benten.SDK) does:

  * exchanges your API key for a one-hour access token, caches it, and
    renews it 5 minutes before it expires;
  * sends approval requests (login, credit card payment, IoT setting
    change, generic notification) over a WebSocket and waits for the
    user's answer: Allow, Deny, or NoResponse after 60 seconds;
  * registers a user's online account, credit card or IoT device with
    Benten, and lists what is registered.

Only dependency: websocket-client (pip install websocket-client).

    from benten import BentenClient
    benten = BentenClient(api_key)
    result = benten.request_generic(
        "United States", "6501234567",
        subtype="Document approval",
        lines=[("Document", "Q3 report"), ("Requested by", "Finance")])
    if result.approved:
        ...
"""

import json
import threading
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

import websocket  # pip install websocket-client

DEFAULT_SERVER_URL = "https://api.bententechnologies.com/v2/"

# Registration replies meaning "saved, but the push to the phone failed".
# The record exists, so these are not treated as errors.
SAVED_BUT_NOT_PUSHED = ("BSC4028", "BSC4054")

# The server waits 60 seconds for the user; wait a little longer so its
# NoResponse reply arrives before we give up.
APPROVAL_WAIT_SECONDS = 75
CONNECT_TIMEOUT_SECONDS = 15
HTTP_TIMEOUT_SECONDS = 30
TOKEN_REFRESH_MARGIN_SECONDS = 5 * 60

STATUS_CODES = {
    "BSC4008": "Username not found.",
    "BSC4019": "Invalid token.",
    "BSC4020": "No registered user found for the given client ID.",
    "BSC4021": "Client account is inactive. Contact Benten support.",
    "BSC4022": "No registered device found for the given country and phone number.",
    "BSC4023": "That account is already registered for this phone number.",
    "BSC4024": "Failed to create the online account.",
    "BSC4026": "Card number/zip code already exists.",
    "BSC4027": "Failed to create the credit card record.",
    "BSC4028": "The card was saved, but the phone could not be told about it yet.",
    "BSC4029": "IoT device is already associated with the given country and phone number.",
    "BSC4030": "Failed to create the IoT device record.",
    "BSC4032": "Account is inactive. Contact Benten support.",
    "BSC4049": "Invalid token: missing client ID.",
    "BSC4051": "Credit card type not supported.",
    "BSC4052": "Bearer token missing.",
    "BSC4054": "The IoT device was saved, but the phone could not be told about it yet.",
    "BSC4056": "Device is not active. Activate the device first.",
    "BSC4057": "No login account associated with the client was found for this device.",
    "BSC4058": "Client is not authorized to request login permission for this device.",
    "BSC4059": "System currently unavailable. Contact Benten support if this persists.",
    "BSC4060": "Invalid Benten credit card ID.",
    "BSC4061": "Unauthorized credit card transaction request.",
    "BSC4062": "No IoT device found for the given name and MAC address.",
    "BSC4063": "Unauthorized IoT setting change request.",
    "BSC4064": "No devices configured for the given IoT device.",
    "BSC4073": "Invalid request. The server could not read the request message.",
    "BSC4074": "Request message is too large.",
    "BSC4075": "API key is required.",
    "BSC4076": "API key is invalid or has been revoked. Check the key, or create a new one on the developer dashboard.",
    "BSC4079": "Too many notifications sent to this phone. Try again in a few minutes.",
    "BSC5000": "Internal server error. Try again later.",
}


def describe(code: str) -> str:
    """A readable description of a Benten status code such as BSC4022."""
    return STATUS_CODES.get(code, "Benten status code " + code)


def _is_status_code(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 7 and value.startswith("BSC") and value[3:].isdigit()


class BentenError(Exception):
    """A request that could not be completed: bad key, network error, a
    rejected registration, and so on. `code` is the BSCxxxx code if the
    server sent one."""

    def __init__(self, message: str, code: str = "", status: int = 0):
        super().__init__(message)
        self.code = code
        self.status = status


@dataclass
class ApprovalResult:
    """The user's answer to an approval request.

    outcome is one of "Approved", "Denied", "TimedOut" or "Error".
    Only an explicit Allow counts as approved."""

    outcome: str
    request_id: str
    error_code: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def approved(self) -> bool:
        return self.outcome == "Approved"

    @property
    def error_message(self) -> str:
        return describe(self.error_code) if self.error_code else ""

    def __str__(self) -> str:
        if self.outcome == "Error":
            return "Error {}: {}".format(self.error_code, self.error_message)
        return self.outcome


def _parse_json(text: str) -> Any:
    """The API sometimes returns a JSON string that itself holds JSON."""
    if not text:
        return None
    value = json.loads(text)
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("{") or stripped.startswith("["):
            return json.loads(stripped)
    return value


def _normalize_base(url: str) -> Tuple[str, str]:
    """Returns (https_base, wss_base), both ending in /v2/ (or the given version)."""
    url = url.strip()
    if url.startswith("wss://"):
        url = "https://" + url[len("wss://"):]
    elif url.startswith("ws://"):
        url = "http://" + url[len("ws://"):]
    elif not url.startswith("http://") and not url.startswith("https://"):
        url = "https://" + url
    if not url.endswith("/"):
        url += "/"
    last = url.rstrip("/").rsplit("/", 1)[-1]
    if not (len(last) >= 2 and last[0] == "v" and last[1:].isdigit()):
        url += "v2/"
    wss = "wss://" + url[len("https://"):] if url.startswith("https://") else "ws://" + url[len("http://"):]
    return url, wss


class BentenClient:
    """Talks to the Benten API with one API key. Create one and reuse it:
    it caches the access token. Safe to use from several threads."""

    def __init__(self, api_key: str, server_url: str = DEFAULT_SERVER_URL):
        if not api_key or not api_key.strip():
            raise ValueError("An API key is required. Create one on the Benten developer dashboard.")
        self._api_key = api_key.strip()
        self._https, self._wss = _normalize_base(server_url)
        self._token: Optional[str] = None
        self._token_expires = 0.0
        self._lock = threading.Lock()

    # ── Access token ──────────────────────────────────────────────────────

    def _access_token(self) -> str:
        with self._lock:
            if self._token and time.time() < self._token_expires:
                return self._token
            body = self._http("POST", "auth/token", {"ApiKey": self._api_key}, auth=False)
            token = body.get("Token") if isinstance(body, dict) else None
            if not token:
                raise BentenError("The server did not return an access token.")
            try:
                life = float(body.get("ExpiresIn") or 3600)
            except (TypeError, ValueError):
                life = 3600.0
            self._token = token
            # Renew 5 minutes early, or halfway through a very short token's life.
            self._token_expires = time.time() + life - min(TOKEN_REFRESH_MARGIN_SECONDS, life / 2)
            return token

    def _forget_token(self, token: str) -> None:
        with self._lock:
            if self._token == token:
                self._token = None

    # ── HTTPS ─────────────────────────────────────────────────────────────

    def _http(self, method: str, path: str, payload: Optional[dict] = None, auth: bool = True) -> Any:
        try:
            return self._http_once(method, path, payload, auth)
        except BentenError as e:
            # A refused token was dropped in _http_once; get a new one and retry once.
            if auth and e.code == "BSC4019":
                return self._http_once(method, path, payload, auth)
            raise

    def _http_once(self, method: str, path: str, payload: Optional[dict], auth: bool) -> Any:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        token = None
        if auth:
            token = self._access_token()
            headers["Authorization"] = "Bearer " + token
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(self._https + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
                text = response.read().decode("utf-8", errors="replace")
            try:
                return _parse_json(text)
            except ValueError:
                raise BentenError("The Benten API sent a reply that isn't JSON: {}".format(text[:200])) from None
        except urllib.error.HTTPError as e:
            text = e.read().decode("utf-8", errors="replace")
            try:
                body = _parse_json(text)
            except ValueError:
                body = None
            code = body.get("Response", "") if isinstance(body, dict) else ""
            if auth and code == "BSC4019" and token:
                self._forget_token(token)
            if _is_status_code(code):
                raise BentenError("{}: {}".format(code, describe(code)), code, e.code) from None
            message = (code or text or e.reason or "").strip()
            raise BentenError("HTTP {}: {}".format(e.code, message), "", e.code) from None
        except urllib.error.URLError as e:
            raise BentenError("Could not reach the Benten API: {}".format(e.reason)) from None
        except OSError as e:  # timeouts, resets
            raise BentenError("Could not reach the Benten API: {}".format(e)) from None

    # ── Approval requests (WebSocket) ─────────────────────────────────────

    def _approval(self, path: str, request: dict) -> ApprovalResult:
        for attempt in range(2):
            token = self._access_token()
            result = self._send_approval(path, dict(request, JWT=token))
            # The server checks the token before notifying anyone, so a
            # rejected token can be renewed and the request sent once more.
            if result.error_code == "BSC4019" and attempt == 0:
                self._forget_token(token)
                continue
            return result
        return result

    def _send_approval(self, path: str, request: dict) -> ApprovalResult:
        request_id = str(uuid.uuid4())
        request["RequestId"] = request_id
        try:
            ws = websocket.create_connection(self._wss + path, timeout=CONNECT_TIMEOUT_SECONDS)
        except Exception as e:  # noqa: BLE001 (connection errors come in many types)
            raise BentenError("Could not connect to the Benten API: {}".format(e)) from None
        try:
            ws.settimeout(APPROVAL_WAIT_SECONDS)
            try:
                ws.send(json.dumps(request))
                text = ws.recv()
            except websocket.WebSocketTimeoutException:
                raise BentenError("No reply from the Benten API within {} seconds.".format(APPROVAL_WAIT_SECONDS)) from None
            except websocket.WebSocketConnectionClosedException:
                raise BentenError("The Benten API closed the connection without replying.") from None
            except (websocket.WebSocketException, OSError) as e:
                raise BentenError("The connection to the Benten API failed: {}".format(e)) from None
            if not text:
                raise BentenError("The Benten API closed the connection without replying.")
        finally:
            try:
                ws.close()
            except Exception:  # noqa: BLE001
                pass
        return self._to_result(text, request_id)

    @staticmethod
    def _to_result(text: Any, request_id: str) -> ApprovalResult:
        if isinstance(text, bytes):
            text = text.decode("utf-8", errors="replace")
        try:
            reply = _parse_json(text)
        except ValueError:
            reply = None
        if not isinstance(reply, dict):
            code = text.strip() if isinstance(text, str) and _is_status_code(text.strip()) else ""
            return ApprovalResult("Error", request_id, code or "BSC5000", {"Raw": text})
        answer = str(reply.get("Response") or reply.get("Error") or "")
        rid = str(reply.get("RequestId") or reply.get("_id") or request_id)
        if answer.lower() == "allow":
            return ApprovalResult("Approved", rid, raw=reply)
        if answer.lower() == "deny":
            return ApprovalResult("Denied", rid, raw=reply)
        if answer.lower() == "noresponse":
            return ApprovalResult("TimedOut", rid, raw=reply)
        return ApprovalResult("Error", rid, answer if _is_status_code(answer) else "BSC5000", reply)

    def request_login(self, country: str, phone_number: str, username: str = "") -> ApprovalResult:
        """Ask the user to approve signing in. The user's account (username)
        must be registered for this phone first: see register_online_account."""
        return self._approval("clients/loginrequest/phonenumber", {
            "RequestType": "LoginRequest", "Country": country,
            "PhoneNumber": phone_number, "Username": username})

    def request_card_payment(self, credit_card_id: str, amount: str, retailer: str) -> ApprovalResult:
        """Ask the card holder to approve a payment. credit_card_id comes
        from list_credit_cards."""
        return self._approval("clients/creditcard/transaction", {
            "RequestType": "CCTransaction", "CreditCardId": credit_card_id,
            "Amount": amount, "Retailer": retailer})

    def request_iot_setting(self, name: str, mac_address: str, setting_change: str) -> ApprovalResult:
        """Ask the owner to approve a setting change on a registered IoT device."""
        return self._approval("clients/iotsetting/update", {
            "RequestType": "IoTSettingChange", "Name": name,
            "MacAddress": mac_address, "SettingChange": setting_change})

    def request_generic(self, country: str, phone_number: str, subtype: str,
                        lines: Sequence[Tuple[str, str]]) -> ApprovalResult:
        """Ask the user to approve anything. Needs only their phone number.
        lines: up to three (label, value) pairs shown on the phone."""
        request = {"RequestType": "GenericNotification", "Country": country,
                   "PhoneNumber": phone_number, "NotificationSubType": subtype}
        for i, (label, value) in enumerate(list(lines)[:3], start=1):
            request["Line{}Placeholder".format(i)] = label
            request["Line{}".format(i)] = value
        return self._approval("clients/generic/notification/", request)

    # ── Registering what a user can approve (HTTPS) ───────────────────────

    def register_online_account(self, country: str, phone_number: str, username: str,
                                first_name: str, last_name: str, description: str = "") -> None:
        """Register the user's account on your site, so login requests for
        this username can be sent to their phone."""
        self._register("clients/onlineaccount/create", {
            "DeviceCountry": country, "DevicePhoneNumber": phone_number, "Username": username,
            "FirstName": first_name, "LastName": last_name, "Description": description})

    def register_credit_card(self, country: str, phone_number: str, card_number: str, zip_code: str,
                             first_name: str, last_name: str, description: str = "") -> None:
        """Register a card, so payments with it can be sent to the holder's
        phone. Benten keeps only a hash of the number and its last four digits."""
        self._register("clients/creditcard/create", {
            "DeviceCountry": country, "DevicePhoneNumber": phone_number, "CardNumber": card_number,
            "ZipCode": zip_code, "FirstName": first_name, "LastName": last_name,
            "Description": description})

    def register_iot_device(self, country: str, phone_number: str, name: str, mac_address: str,
                            description: str = "") -> None:
        """Register an IoT device, so setting changes can be sent to its owner."""
        self._register("clients/iot/create", {
            "DeviceCountry": country, "DevicePhoneNumber": phone_number, "Name": name,
            "MacAddress": mac_address, "Description": description})

    def _register(self, path: str, payload: dict) -> None:
        try:
            self._http("POST", path, payload)
        except BentenError as e:
            if e.code not in SAVED_BUT_NOT_PUSHED:
                raise

    def list_online_accounts(self, country: str, phone_number: str) -> List[str]:
        """Usernames you have registered for this phone."""
        body = self._http("POST", "clients/onlineaccount/info", {"Country": country, "PhoneNumber": phone_number})
        return list(body.get("Usernames") or []) if isinstance(body, dict) else []

    def list_credit_cards(self, country: str, phone_number: str) -> List[Dict[str, str]]:
        """Cards you have registered for this phone: each has CreditCardId,
        Number ("Ending in 1234"), CardProvider and CardLogoUri."""
        body = self._http("POST", "clients/creditcard/info", {"Country": country, "PhoneNumber": phone_number})
        return list(body.get("CreditCards") or []) if isinstance(body, dict) else []
