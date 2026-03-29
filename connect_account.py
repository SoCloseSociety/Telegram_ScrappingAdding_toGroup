#!/usr/bin/env python3
"""
Connecte un compte Telegram et l'ajoute au config.json.
Usage: python connect_account.py
"""
import os, json, asyncio

try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

from telethon.sync import TelegramClient

CONFIG = 'config.json'

def main():
    print("\n🔌 Connexion d'un compte Telegram\n")

    api_id   = input("  API ID: ").strip()
    api_hash = input("  API Hash: ").strip()
    phone    = input("  Numéro de téléphone (ex: +33612345678): ").strip()

    print(f"\n  Connexion en cours pour {phone}...")
    print("  → Telegram va t'envoyer un code par SMS ou dans l'app.\n")

    client = TelegramClient(phone, int(api_id), api_hash)
    client.start(phone=phone)

    me = client.get_me()
    print(f"\n  ✅ Connecté en tant que: {me.first_name or ''} {me.last_name or ''} (@{me.username or 'N/A'})")
    print(f"     ID: {me.id}")
    client.disconnect()

    # Save to config
    if os.path.exists(CONFIG):
        with open(CONFIG) as f:
            config = json.load(f)
    else:
        config = {"accounts": [], "proxies": []}

    config['accounts'].append({
        "api_id": api_id,
        "api_hash": api_hash,
        "phone": phone,
        "blacklisted": False,
        "blacklist_time": None
    })

    with open(CONFIG, 'w') as f:
        json.dump(config, f, indent=4)

    print(f"\n  ✅ Compte sauvegardé dans {CONFIG}")
    print(f"  📱 Session: {phone}.session")
    print("\n  Tu peux maintenant utiliser ./run.sh ou le dashboard !\n")

if __name__ == "__main__":
    main()
