
from getpass import getpass
from pathlib import Path

import requests
from dotenv import set_key

BASE_URL = "https://fall2026.auplmscs.com"
ENV_FILE = Path(__file__).with_name(".env")

username = input("Your Moodle username: ").strip()
password = getpass("Your Moodle password (hidden): ")

try:
    response = requests.post(
        f"{BASE_URL}/login/token.php",
        data={
            "username": username,
            "password": password,
            "service": "moodle_mobile_app",
        },
        timeout=20,
    )

    try:
        result = response.json()
    except ValueError:
        result = {}

    token = result.get("token")

    if response.ok and token:
        set_key(str(ENV_FILE), "MOODLE_TOKEN", token)
        print("Success: token saved privately in .env.")
        print("Your password and token were not printed.")
    else:
        print("No token was issued.")
        print("HTTP status:", response.status_code)
        print("Moodle error:", result.get("error", "No JSON error returned"))
        print("Error code:", result.get("errorcode", "Not provided"))

except requests.RequestException as error:
    print("Connection failed:", error)
