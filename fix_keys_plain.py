#!/usr/bin/env python3
# fix_keys_plain.py
# Автоматически расшифровывает/заменяет зашифрованные api_key/api_secret в users.json
# Если FERNET_KEY есть в окружении — попробует расшифровать.
# Иначе — предложит ввести plain значения вручную.
#
# СДЕЛАЙ БЭКАП users.json ПЕРЕД ЗАПУСКОМ: копия будет создана автоматически.

import os
import json
import getpass
from pathlib import Path
from datetime import datetime

# --- DISABLED Fernet encryption: no-op wrappers (inserted by remove_encryption.py) ---
# def encrypt(value):
#     # encryption disabled - return plain value
#     return value
#
def decrypt(value):
    # decryption disabled - return plain value
    return value


USERS_FILE = os.getenv("USERS_FILE", "./users.json")
BACKUP_SUFFIX = ".bak-" + datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")


def load_users():
    p = Path(USERS_FILE)
    if not p.exists():
        print("users.json не найден:", USERS_FILE)
        raise SystemExit(1)
    return json.loads(p.read_text(encoding="utf-8"))


def save_users(users):
    p = Path(USERS_FILE)
    p.write_text(json.dumps(users, indent=4, ensure_ascii=False), encoding="utf-8")


def try_decrypt_with_fernet(fkey, cipher_text):
    """
    Try decrypting cipher_text using provided fkey (FERNET key).
    Returns (plaintext, None) on success, (None, error_message) on failure.
    """
    try:
        from cryptography.fernet import Fernet
    except Exception as e:
        return None, "cryptography not installed: " + str(e)
    try:
        key = fkey.encode() if isinstance(fkey, str) else fkey
        f = Fernet(key)
        # cipher_text expected to be str
        if isinstance(cipher_text, str):
            ct = cipher_text.encode()
        else:
            ct = cipher_text
        plain = f.decrypt(ct).decode("utf-8")
        return plain, None
    except Exception as e:
        return None, str(e)


def backup_file(path):
    p = Path(path)
    bak = str(p) + BACKUP_SUFFIX
    p.rename(bak)
    print("Создан бэкап:", bak)
    return bak


def main():
    print("FIX KEYS PLAIN — старт")
    print("Путь users.json:", USERS_FILE)
    users = load_users()
    # find any entries with api_key/api_secret starting with gAAAAA
    to_fix = []
    for uid, u in users.items():
        api = (u.get("api_key") or "")
        sec = (u.get("api_secret") or "")
        if isinstance(api, str) and api.startswith("gAAAAA"):
            to_fix.append(uid)
            continue
        if isinstance(sec, str) and sec.startswith("gAAAAA"):
            to_fix.append(uid)
            continue

    if not to_fix:
        print("Шифрованных записей не найдено. Ничего делать не нужно.")
        return

    print("Найдено пользователей с шифрованными ключами:", to_fix)
    # make backup
    bak = backup_file(USERS_FILE)

    fkey = os.getenv("FERNET_KEY")
    changed = False

    for uid in to_fix:
        u = users.get(uid, {})
        api = (u.get("api_key") or "")
        sec = (u.get("api_secret") or "")
        print("\n--- uid:", uid)
        # try decrypt if fkey present
        if fkey and isinstance(api, str) and api.startswith("gAAAAA"):
            plain_api, err = try_decrypt_with_fernet(fkey, api)
            if plain_api:
                print("Расшифрован api_key (FERNET_KEY) — заменяю.")
                users[uid]['api_key'] = plain_api
                changed = True
            else:
                print("Не удалось расшифровать api_key:", err)
        elif isinstance(api, str) and api.startswith("gAAAAA"):
            print("api_key зашифрован, но FERNET_KEY не задан — нужно ввести вручную или задать FERNET_KEY.")
        # same for secret
        if fkey and isinstance(sec, str) and sec.startswith("gAAAAA"):
            plain_sec, err = try_decrypt_with_fernet(fkey, sec)
            if plain_sec:
                print("Расшифрован api_secret (FERNET_KEY) — заменяю.")
                users[uid]['api_secret'] = plain_sec
                changed = True
            else:
                print("Не удалось расшифровать api_secret:", err)
        elif isinstance(sec, str) and sec.startswith("gAAAAA"):
            print("api_secret зашифрован, но FERNET_KEY не задан — нужно ввести вручную или задать FERNET_KEY.")

        # If still encrypted (no fkey or decrypt failed) — ask user to input
        if isinstance(users[uid].get('api_key', ''), str) and users[uid]['api_key'].startswith("gAAAAA"):
            new_api = input("Вставь plain api_key для uid " + str(uid) + " (или Enter, чтобы пропустить): ").strip()
            if new_api:
                users[uid]['api_key'] = new_api
                changed = True

        if isinstance(users[uid].get('api_secret', ''), str) and users[uid]['api_secret'].startswith("gAAAAA"):
            new_sec = getpass.getpass("Вставь plain api_secret (скрыто) для uid " + str(uid) + " (или Enter, чтобы пропустить): ").strip()
            if new_sec:
                users[uid]['api_secret'] = new_sec
                changed = True

    if changed:
        save_users(users)
        print("\nСохранён users.json с plain ключами. Если что-то пошло не так — файл бэкап:", bak)
    else:
        print("\nНе было заменено ни одного ключа. Бэкап сохранён:", bak)


if __name__ == "__main__":
    main()