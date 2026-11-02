# Benten Python Sample

A Python client and command-line sample for the [Benten](https://bententechnologies.com) Authentication and Authorization Platform. It sends the same four approval requests as the .NET sample ([benten-client-sample](https://github.com/bentenauth/benten-client-sample)):

- login;
- credit card payment;
- IoT setting change;
- generic notification.

The user gets a notification in the free Benten App on their phone and taps **Allow** or **Deny**. Your code gets the answer back, or **TIMEOUT** after 60 seconds.

Benten has no language-specific parts: everything is HTTPS and WebSockets with JSON. `benten.py` is about 300 lines with one dependency, so you can copy it into your project, or use it as a guide for another language.

## Requirements

- Python 3.8 or later, on any OS.
- `pip install -r requirements.txt` (installs `websocket-client`).
- A Benten developer account and an **API key**: sign up and create a key on the [developer dashboard](https://bententechnologies.com/developers.html).
- A phone with the Benten App installed and its phone number registered ([iOS](https://apps.apple.com/us/app/benten-app/id1508036928), [Android](https://play.google.com/store/apps/details?id=com.bententechnologies.maui.bentenapp)).

## Try it

```sh
pip install -r requirements.txt
export BENTEN_API_KEY=bnt_sk_...      # Windows: set BENTEN_API_KEY=bnt_sk_...
export BENTEN_PHONE=6501234567        # the phone number registered in the Benten App

python sample.py notify               # works right away: needs only the phone number
python sample.py                      # interactive menu
```

Each request type needs a different amount of setup first. The generic notification needs only the phone number. The other three need something registered for that phone first; it's a one-time step per user.

| Request | Command | Register first |
|---|---|---|
| Generic notification | `notify --line "Amount=$2,000" --line "Payee=Acme"` | Nothing |
| Login | `login --username alice@example.com` | `register-account --username alice@example.com --first Alice --last Smith` |
| Credit card payment | `pay --amount '$99.99' --retailer "Sample Store"` | `register-card --number 4111... --zip 94016 --first Alice --last Smith` |
| IoT setting change | `iot --name Thermostat1 --mac AB:CD:EF:12:34:56 --change "72°F to 68°F"` | `register-iot --name Thermostat1 --mac AB:CD:EF:12:34:56` |

`python sample.py list` shows the accounts and cards you've registered for the phone. `python sample.py <command> --help` shows every option.

Sample output:

```
[14:02:11] Sending generic notification approval request (Document Approval)...
[14:02:16] [APPROVED] User tapped Allow.
```

## Use it in your code

```python
from benten import BentenClient, BentenError

benten = BentenClient(api_key)          # create once and reuse: it caches the access token

result = benten.request_generic(
    "United States", "6501234567",
    subtype="Wire transfer",
    lines=[("Amount", "$2,000.00"), ("Payee", "Acme Supplies")])

if result.approved:
    ...                                  # the user tapped Allow
elif result.outcome == "Denied":
    ...
elif result.outcome == "TimedOut":
    ...                                  # no answer within 60 seconds
else:
    print(result.error_code, result.error_message)   # e.g. BSC4022: no Benten App for that number
```

Every request returns an `ApprovalResult`. Its `outcome` is `Approved`, `Denied`, `TimedOut` or `Error`, and only an explicit Allow counts as approved. Problems that stop a request being sent (a bad API key, the network, a rejected registration) raise `BentenError`, which has `.code` (e.g. `BSC4076`) and `.status` (the HTTP status).

| Method | What it does |
|---|---|
| `request_login(country, phone, username)` | Ask the user to approve a sign-in. |
| `request_card_payment(credit_card_id, amount, retailer)` | Ask the card holder to approve a payment. |
| `request_iot_setting(name, mac_address, setting_change)` | Ask the owner to approve a device setting change. |
| `request_generic(country, phone, subtype, lines)` | Ask the user to approve anything; up to three `(label, value)` lines. |
| `register_online_account(...)`, `register_credit_card(...)`, `register_iot_device(...)` | One-time setup per user for login, card and IoT requests. |
| `list_online_accounts(country, phone)`, `list_credit_cards(country, phone)` | What you have registered for a phone. |

## How it works

1. `POST https://api.bententechnologies.com/v2/auth/token` with `{"ApiKey": "bnt_sk_..."}` returns a one-hour access token. The client caches it and renews it 5 minutes before it expires.
2. An approval request opens a WebSocket, for example `wss://api.bententechnologies.com/v2/clients/generic/notification/`. It sends one JSON message: the request fields, the token in `JWT`, and a fresh `RequestId`.
3. The server pushes the request to the user's phone and replies once on the same WebSocket. The reply's `Response` is `Allow`, `Deny`, `NoResponse` or a status code such as `BSC4022`.
4. Registration and lists are ordinary HTTPS `POST`s with the token as `Authorization: Bearer ...`.

Field-by-field details are in the [developer guide](https://bententechnologies.com/dev-guide.html).

## License

MIT. See [LICENSE](LICENSE).
