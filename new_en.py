import os
import json
import csv
import time
import random
import shutil
import asyncio
import sqlite3

# Python 3.10+ no longer creates an event loop automatically.
# Telethon's sync wrapper requires one to exist before any client.start() call.
try:
    _loop = asyncio.get_event_loop()
    if _loop.is_closed():
        raise RuntimeError("closed")
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

from telethon.sync import TelegramClient
from telethon.tl.functions.messages import GetDialogsRequest, GetHistoryRequest
from telethon.tl.types import (InputPeerEmpty, InputPeerChannel, InputPeerUser,
                                UserStatusRecently, UserStatusOnline, PeerChannel)
from telethon.errors.rpcerrorlist import (PeerFloodError, UserPrivacyRestrictedError,
                                           FloodWaitError, UserAlreadyParticipantError,
                                           SessionPasswordNeededError)
from telethon.tl.functions.channels import InviteToChannelRequest, GetFullChannelRequest
from colorama import Fore, Style, init as colorama_init

colorama_init()

# Load configuration
config_file = 'config.json'
if os.path.exists(config_file):
    with open(config_file, 'r') as f:
        config = json.load(f)
else:
    config = {
        "accounts": [],
        "proxies": []
    }

# Save configuration
def save_config():
    with open(config_file, 'w') as f:
        json.dump(config, f, indent=4)

# Connect a new account
def connect_new_account():
    api_id   = input('Enter API ID: ').strip()
    api_hash = input('Enter API Hash: ').strip()
    phone    = input('Enter phone number: ').strip()
    try:
        api_id_int = int(api_id)
    except ValueError:
        return '❌ API ID must be an integer.\n'
    if any(a['phone'] == phone for a in config['accounts']):
        return f'⚠️ Account {phone} is already registered.\n'
    client = TelegramClient(phone, api_id_int, api_hash)
    try:
        client.connect()
        if not client.is_user_authorized():
            client.send_code_request(phone)
            try:
                client.sign_in(phone, input('📱 Enter the code: '))
            except SessionPasswordNeededError:
                client.sign_in(password=input('🛡️ Enter 2FA password: '))
        config['accounts'].append({
            "api_id": api_id,
            "api_hash": api_hash,
            "phone": phone,
            "blacklisted": False,
            "blacklist_time": None
        })
        save_config()
        return f'✅ Account {phone} connected successfully.\n'
    except Exception as e:
        return f'❌ Error: {e}\n'
    finally:
        try: client.disconnect()
        except Exception: pass

# List connected accounts
def list_connected_accounts():
    if not config['accounts']:
        return "📋 0 connected accounts.\n"
    else:
        response = ""
        for idx, account in enumerate(config['accounts']):
            status = "🔴 Blacklisted" if account['blacklisted'] else "🟢 Active"
            response += f"{idx + 1}. {account['phone']} - {status}\n"
        return response

# Delete a connected account
def delete_connected_account():
    if not config['accounts']:
        return list_connected_accounts()
    print(list_connected_accounts())
    try:
        index = int(input('Enter the account number to delete: ')) - 1
    except ValueError:
        return '❌ Invalid number.\n'
    if 0 <= index < len(config['accounts']):
        phone = config['accounts'][index]['phone']
        del config['accounts'][index]
        save_config()
        return f'✅ Account {phone} deleted successfully.\n'
    return '❌ Invalid account number.\n'

# Quarantine a blacklisted account
def blacklist_account(index):
    if 0 <= index < len(config['accounts']):
        config['accounts'][index]['blacklisted'] = True
        config['accounts'][index]['blacklist_time'] = time.time()
        save_config()
        return f'✅ Account quarantined for 48 hours.\n'
    else:
        return '❌ Invalid account number.\n'

# Check blacklisted accounts to lift quarantine after 48 hours
def check_blacklisted_accounts():
    changed = False
    for account in config['accounts']:
        if account['blacklisted'] and account['blacklist_time'] and time.time() - account['blacklist_time'] > 48 * 3600:
            account['blacklisted'] = False
            account['blacklist_time'] = None
            changed = True
    if changed:
        save_config()

# Select an account and check for restrictions
def select_account_and_check_restrictions():
    if not config['accounts']:
        return list_connected_accounts()
    print(list_connected_accounts())
    try:
        index = int(input('Enter the account number to check: ')) - 1
    except ValueError:
        return '❌ Invalid number.\n'
    if not (0 <= index < len(config['accounts'])):
        return '❌ Invalid account number.\n'
    account = config['accounts'][index]
    if account['blacklisted']:
        return f"🔴 Account {account['phone']} is currently quarantined.\n"
    client = None
    try:
        client = get_client(account)
        client.get_me()
        return f"🟢 Account {account['phone']} is active and without restrictions.\n"
    except Exception as e:
        return f"🔴 Account {account['phone']} has restrictions: {get_restriction_details(e)}\n"
    finally:
        if client:
            try: client.disconnect()
            except Exception: pass

# Check all accounts and ask to quarantine
def check_all_accounts_restrictions():
    results = []
    for idx, account in enumerate(config['accounts']):
        if account['blacklisted']:
            results.append((idx, f"🔴 Account {account['phone']} is currently quarantined.\n"))
            continue
        client = None
        try:
            client = get_client(account)
            client.get_me()
            results.append((idx, f"🟢 Account {account['phone']} is active and without restrictions.\n"))
        except Exception as e:
            results.append((idx, f"🔴 Account {account['phone']} has restrictions: {get_restriction_details(e)}\n"))
        finally:
            if client:
                try: client.disconnect()
                except Exception: pass

    for idx, result in results:
        print(result)

    quarantine_choice = input("Do you want to quarantine accounts with restrictions for 48 hours? (y/n): ").lower()
    if quarantine_choice == 'y':
        for idx, result in results:
            if "restrictions" in result:
                print(blacklist_account(idx))

# Get restriction details
def get_restriction_details(exception):
    if "disconnected" in str(exception):
        return "The account is disconnected. Please check the account connection."
    elif "flood" in str(exception).lower():
        return "The account is temporarily restricted due to too many requests. Try again later."
    elif "privacy" in str(exception).lower():
        return "The account's privacy settings prevent this action."
    else:
        return str(exception)

# Check group restrictions before adding users
def check_group_restrictions(client, group):
    try:
        if not client.is_connected():
            client.connect()
        full_group = client(GetFullChannelRequest(group))
        if full_group.full_chat.restrictions:
            return "🔴 The group has restrictions that may prevent adding members.\n"
        return "🟢 The group is unrestricted for adding members.\n"
    except Exception as e:
        return f"❌ Error checking group restrictions: {e}\n"

# Add a proxy
def add_proxy():
    proxy = input('Enter the proxy (format: ip:port or ip:port:user:pass): ').strip()
    parts = proxy.split(':')
    if len(parts) not in (2, 4):
        return "❌ Invalid format. Use ip:port or ip:port:user:pass\n"
    try:
        int(parts[1])
    except ValueError:
        return "❌ Port must be an integer.\n"
    if proxy in config['proxies']:
        return "⚠️ Proxy already present.\n"
    config['proxies'].append(proxy)
    save_config()
    return f'✅ Proxy {proxy} added successfully.\n'

# Test a proxy
def test_proxy():
    if not config['accounts']:
        return '❌ No account available to test proxy.\n'
    proxy = input('Enter the proxy to test (format: ip:port or ip:port:user:pass): ').strip()
    parts = proxy.split(':')
    account = config['accounts'][0]
    import tempfile
    session = os.path.join(tempfile.gettempdir(), 'tg_proxy_test_en')
    tc = None
    try:
        if len(parts) == 2:
            tc = TelegramClient(session, int(account['api_id']), account['api_hash'],
                                proxy=('socks5', parts[0], int(parts[1])))
        elif len(parts) == 4:
            tc = TelegramClient(session, int(account['api_id']), account['api_hash'],
                                proxy=('socks5', parts[0], int(parts[1]), True, parts[2], parts[3]))
        else:
            return "❌ Invalid proxy format.\n"
        tc.connect()
        return f"🟢 Proxy {proxy} is working correctly.\n"
    except Exception as e:
        return f"🔴 Proxy {proxy} is not working: {e}\n"
    finally:
        if tc:
            try: tc.disconnect()
            except Exception: pass
        for ext in ('', '.session', '.session-journal'):
            try: os.remove(session + ext)
            except OSError: pass

# Delete a proxy
def delete_proxy():
    if not config['proxies']:
        return list_proxies()
    print(list_proxies())
    try:
        index = int(input('Enter the proxy number to delete: ')) - 1
    except ValueError:
        return '❌ Invalid number.\n'
    if 0 <= index < len(config['proxies']):
        proxy = config['proxies'].pop(index)
        save_config()
        return f'✅ Proxy {proxy} deleted successfully.\n'
    return '❌ Invalid proxy number.\n'

# List proxies
def list_proxies():
    if not config['proxies']:
        return "🌐 0 connected proxies.\n"
    else:
        response = ""
        for idx, proxy in enumerate(config['proxies']):
            response += f"{idx + 1}. {proxy}\n"
        return response

# Connect to the selected account
def get_client(account):
    api_id = int(account['api_id'])
    valid_proxies = [p for p in config['proxies'] if p and p.lower() != 'exit' and ':' in p]
    proxy = random.choice(valid_proxies) if valid_proxies else None
    if proxy:
        parts = proxy.split(':')
        if len(parts) == 2:
            client = TelegramClient(account['phone'], api_id, account['api_hash'],
                                    proxy=('socks5', parts[0], int(parts[1])))
        elif len(parts) == 4:
            client = TelegramClient(account['phone'], api_id, account['api_hash'],
                                    proxy=('socks5', parts[0], int(parts[1]), True, parts[2], parts[3]))
        else:
            client = TelegramClient(account['phone'], api_id, account['api_hash'])
    else:
        client = TelegramClient(account['phone'], api_id, account['api_hash'])
    client.start()
    return client

# Reset database connection
def reset_database_connection(client):
    try:
        client.disconnect()
        client.connect()
    except Exception as e:
        print(f"⚠️ Error resetting database connection: {e}")

# Check if the user is recently online
def is_user_online(status):
    return isinstance(status, (UserStatusOnline, UserStatusRecently))

# Scrape group members
def scrape_members(client, target_group, online_only=False):
    if not client.is_connected():
        client.connect()
    print(f'🔍 Retrieving members from group {target_group.title}...')
    try:
        all_participants = client.get_participants(target_group, aggressive=True)
        if online_only:
            all_participants = [user for user in all_participants if user.status and is_user_online(user.status)]
        with open("members.csv", "w", encoding='UTF-8') as f:
            writer = csv.writer(f, delimiter=",", lineterminator="\n")
            writer.writerow(['username', 'user_id', 'access_hash', 'name', 'group', 'group_id'])
            for user in all_participants:
                username = user.username if user.username else ""
                first_name = user.first_name if user.first_name else ""
                last_name = user.last_name if user.last_name else ""
                name = (first_name + ' ' + last_name).strip()
                writer.writerow([username, user.id, user.access_hash, name, target_group.title, target_group.id])
        return f'✅ Members saved to members.csv. Total number: {len(all_participants)}.\n'
    except Exception as e:
        return f"❌ Error retrieving members: {e}\n"

# Add members to a group
def add_members(client, target_group, input_file='members.csv', num_members=None, mode="normal"):
    if not client.is_connected():
        client.connect()
    print(f'➕ Adding members to group {target_group.title}...')
    users = []
    with open(input_file, encoding='UTF-8') as f:
        rows = csv.reader(f, delimiter=",", lineterminator="\n")
        next(rows, None)
        for row in rows:
            if len(row) >= 6:
                try:
                    users.append({'username': row[0], 'id': int(row[1]),
                                  'access_hash': int(row[2]), 'name': row[3],
                                  'group': row[4], 'group_id': row[5]})
                except (ValueError, IndexError):
                    pass

    try:
        num_members = len(users) if (num_members is None or num_members == '') else int(num_members)
    except (ValueError, TypeError):
        num_members = len(users)
    target_group_entity = InputPeerChannel(target_group.id, target_group.access_hash)
    added = privacy = errors = 0

    for user in users[:num_members]:
        try:
            print(f"➕ Adding {user['id']} ({user['username']})")
            client(InviteToChannelRequest(target_group_entity,
                                          [InputPeerUser(user['id'], user['access_hash'])]))
            added += 1
            print(f'✅ {user["name"]} added successfully')
            time.sleep(random.uniform(3, 15) if mode == "turbo" else random.uniform(15, 35))
        except UserAlreadyParticipantError:
            print(f"ℹ️ {user['username'] or user['id']} is already a member.")
        except PeerFloodError:
            return f"🚫 Flood error. {added} added. Try again after 24h.\n"
        except UserPrivacyRestrictedError:
            privacy += 1
            print(f"🔒 {user['username'] or user['id']}: privacy restricted.")
        except FloodWaitError as e:
            print(f"⏳ FloodWait: {e.seconds}s")
            time.sleep(e.seconds)
        except Exception as e:
            errors += 1
            print(f"⚠️ Unexpected error: {e}")
    return f"✅ Done. ✅{added} added, 🔒{privacy} privacy, ❌{errors} errors.\n"

# Choose a group from active groups we can scrape
def choose_group_from_active(client):
    if not client.is_connected():
        client.connect()
    result = client(GetDialogsRequest(offset_date=None, offset_id=0,
                                      offset_peer=InputPeerEmpty(), limit=200, hash=0))
    groups = [c for c in result.chats if hasattr(c, 'megagroup') and c.megagroup]
    if not groups:
        return None, "❌ No groups available.\n"
    print('Choose a group:')
    for i, g in enumerate(groups):
        print(f"  {i} - {g.title}")
    try:
        idx = int(input("📋 Enter a number: "))
        if not (0 <= idx < len(groups)):
            return None, "❌ Invalid number.\n"
        return groups[idx], ""
    except ValueError:
        return None, "❌ Invalid input.\n"

# Get a group by its name, ID or link
def get_group_by_name(client, group_identifier):
    for _ in range(3):  # Retry logic to handle database lock
        try:
            if not client.is_connected():
                client.connect()
            ident = group_identifier.strip()
            if ident.startswith("https://t.me/") or ident.startswith("@"):
                entity = client.get_entity(ident)
            elif ident.lstrip('-').isdigit():
                entity = client.get_entity(PeerChannel(abs(int(ident))))
            else:
                entity = client.get_entity(ident)
            return entity, ""
        except sqlite3.OperationalError as e:
            if "database is locked" in str(e):
                print("🔒 Database is locked, retrying...")
                reset_database_connection(client)
                time.sleep(2)
            else:
                return None, f"❌ DB error: {e}\n"
        except Exception as e:
            return None, f"❌ Group '{group_identifier}' not found: {e}\n"
    return None, "❌ Database is locked after multiple attempts.\n"

# Delete saved users
def delete_saved_users():
    if os.path.exists("members.csv"):
        os.remove("members.csv")
    return '🗑️ members.csv deleted successfully.\n'

# Display saved users
def display_saved_users():
    try:
        if not os.path.exists("members.csv"):
            return "❌ No saved users.\n"
        with open("members.csv", encoding='UTF-8') as f:
            rows = csv.reader(f, delimiter=",", lineterminator="\n")
            next(rows, None)
            users = [row for row in rows]
            if not users:
                return "📋 No saved users.\n"
            response = ""
            for row in users:
                if len(row) >= 4:
                    response += f"👤 {row[3]} (Username: {row[0]}, ID: {row[1]})\n"
            response += f"📊 Total number of scraped users: {len(users)}\n"
            return response
    except Exception as e:
        return f"❌ Error displaying saved users: {e}\n"

# Filter and remove inactive or fake accounts
def _is_bot_username(username):
    """Detect bots by username (ends with 'bot')."""
    if not username:
        return False
    return username.lower().endswith('bot')

def _is_spam_or_promo(username, name):
    """Detect spam/promo accounts by keywords."""
    import re
    combined = ((username or '') + ' ' + (name or '')).lower()
    spam_patterns = [
        r'marketing', r'sms\b', r'bulk', r'promo', r'advert',
        r'ambassador', r'airdrop', r'forex.*(signal|trade)',
        r'crypto.*(signal|trade)', r'earn.?money', r'make.?money',
        r'free.?(btc|eth|usdt|crypto)', r'investment.?plan',
        r'join.*cleaner', r'welcome.*bot',
    ]
    return any(re.search(p, combined) for p in spam_patterns)

def filter_and_remove_inactive_or_fake():
    try:
        with open("members.csv", encoding='UTF-8') as f:
            reader = csv.reader(f, delimiter=",", lineterminator="\n")
            next(reader, None)
            all_rows = list(reader)
    except FileNotFoundError:
        return "❌ members.csv not found.\n"
    except Exception as e:
        return f"❌ Error reading members.csv: {e}\n"

    if not all_rows:
        return "❌ members.csv is empty.\n"

    before = len(all_rows)
    seen_ids = set()
    active_users = []
    removed = {
        'no_id': 0, 'no_identity': 0, 'no_hash': 0,
        'duplicate': 0, 'malformed': 0, 'bot': 0, 'spam': 0,
    }
    for row in all_rows:
        if len(row) < 6:
            removed['malformed'] += 1
            continue
        try:
            uid = int(row[1])
            ahash = int(row[2])
        except ValueError:
            removed['malformed'] += 1
            continue
        if uid <= 0:
            removed['no_id'] += 1
            continue
        if not row[0] and not row[3].strip():
            removed['no_identity'] += 1
            continue
        if ahash == 0:
            removed['no_hash'] += 1
            continue
        if uid in seen_ids:
            removed['duplicate'] += 1
            continue
        if _is_bot_username(row[0]):
            removed['bot'] += 1
            continue
        if _is_spam_or_promo(row[0], row[3] if len(row) >= 4 else ''):
            removed['spam'] += 1
            continue
        seen_ids.add(uid)
        active_users.append(row)

    with open("members.csv", "w", encoding='UTF-8') as f:
        writer = csv.writer(f, delimiter=",", lineterminator="\n")
        writer.writerow(['username', 'user_id', 'access_hash', 'name', 'group', 'group_id'])
        for user in active_users:
            writer.writerow(user)

    total_removed = before - len(active_users)
    details = ", ".join(f"{v} {k}" for k, v in removed.items() if v > 0)
    return (
        f"✅ Filtered: {before} → {len(active_users)} active members"
        f" ({total_removed} removed{': ' + details if details else ''})\n"
    )

# Save scraped members to a new CSV file
def save_scrapped_members_as():
    new_file_name = input("Enter the name of the new CSV file (without extension): ") + '.csv'
    if os.path.exists("members.csv"):
        with open("members.csv", encoding='UTF-8') as f:
            rows = f.readlines()
        with open(new_file_name, "w", encoding='UTF-8') as f:
            f.writelines(rows)
        return f"✅ Members saved to {new_file_name}.\n"
    else:
        return "❌ No member data to save.\n"

# Add scraped members to an existing CSV file or create a new one
def save_scrapped_members_append_or_overwrite():
    new_file_name = input("Enter the name of the CSV file (without extension) : ") + '.csv'
    if os.path.exists(new_file_name):
        choice = input("The file already exists. Enter 'a' to append, 'o' to overwrite : ").lower()
        if choice == 'a':
            if not os.path.exists("members.csv"):
                return "❌ No member data to add.\n"
            existing_ids = set()
            if os.path.exists(new_file_name):
                with open(new_file_name, encoding='UTF-8') as f:
                    reader = csv.reader(f, delimiter=",", lineterminator="\n")
                    next(reader, None)
                    for row in reader:
                        if len(row) >= 2:
                            existing_ids.add(row[1])
            with open("members.csv", encoding='UTF-8') as f:
                reader = csv.reader(f, delimiter=",", lineterminator="\n")
                next(reader, None)
                new_rows = [r for r in reader if len(r) >= 2 and r[1] not in existing_ids]
            with open(new_file_name, "a", encoding='UTF-8') as f:
                writer = csv.writer(f, delimiter=",", lineterminator="\n")
                writer.writerows(new_rows)
            return f"✅ {len(new_rows)} members added to {new_file_name} (deduplicated).\n"
        elif choice == 'o':
            return save_scrapped_members_as()
        else:
            return "❌ Invalid choice.\n"
    else:
        return save_scrapped_members_as()

# Display, list, and delete backup CSV files
def manage_backup_files():
    backup_files = sorted([f for f in os.listdir() if f.endswith('.csv') and f != 'members.csv'])
    if not backup_files:
        return "❌ No backup files found.\n"
    print("Available backup files:")
    for i, file in enumerate(backup_files):
        print(f"  {i + 1}. {file}")
    choice = input("(v)iew  (d)elete  (u)se as members.csv  (q)uit: ").strip().lower()
    if choice == 'v':
        try:
            index = int(input("File number: ")) - 1
        except ValueError:
            return "❌ Invalid number.\n"
        if not (0 <= index < len(backup_files)):
            return "❌ Invalid file number.\n"
        with open(backup_files[index], encoding='UTF-8') as f:
            rows = list(csv.reader(f, delimiter=",", lineterminator="\n"))[1:]
        response = "".join(f"👤 {r[3] if len(r)>3 else '?'} (@{r[0] if r else '?'})\n" for r in rows)
        return response + f"📊 Total: {len(rows)} members.\n"
    elif choice == 'd':
        try:
            index = int(input("File number: ")) - 1
        except ValueError:
            return "❌ Invalid number.\n"
        if not (0 <= index < len(backup_files)):
            return "❌ Invalid file number.\n"
        fname = backup_files[index]
        os.remove(fname)
        return f"🗑️ {fname} deleted.\n"
    elif choice == 'u':
        try:
            index = int(input("File number: ")) - 1
        except ValueError:
            return "❌ Invalid number.\n"
        if not (0 <= index < len(backup_files)):
            return "❌ Invalid file number.\n"
        shutil.copy(backup_files[index], 'members.csv')
        return f"✅ {backup_files[index]} copied to members.csv.\n"
    return ""

# Clone messages from a competitor group
def clone_group_messages(client, source_group):
    if not client.is_connected():
        client.connect()
    print(f'🔍 Cloning messages from group {source_group.title}...')
    try:
        messages = []
        offset_id = 0
        limit = 100
        while True:
            history = client(GetHistoryRequest(
                peer=source_group,
                offset_id=offset_id,
                offset_date=None,
                add_offset=0,
                limit=limit,
                max_id=0,
                min_id=0,
                hash=0
            ))
            if not history.messages:
                break
            messages.extend(history.messages)
            offset_id = min(msg.id for msg in history.messages)

        with open("cloned_messages.csv", "w", encoding='UTF-8') as f:
            writer = csv.writer(f, delimiter=",", lineterminator="\n")
            writer.writerow(['message_id', 'from_id', 'message', 'date'])
            for message in messages:
                writer.writerow([message.id, message.from_id, message.message or '', message.date])
        return f'✅ Messages cloned saved to cloned_messages.csv. Total number: {len(messages)}.\n'
    except Exception as e:
        return f"❌ Error cloning messages: {e}\n"

# Send cloned messages to a group
def send_cloned_messages(client, target_group, input_file='cloned_messages.csv', mode="normal"):
    if not client.is_connected():
        client.connect()
    print(f'🚀 Sending cloned messages to group {target_group.title}...')
    sent = 0
    try:
        with open(input_file, encoding='UTF-8') as f:
            rows = list(csv.reader(f, delimiter=",", lineterminator="\n"))[1:]
        for row in rows:
            if len(row) < 3 or not row[2]:
                continue
            try:
                client.send_message(target_group, row[2])
                sent += 1
                time.sleep(random.uniform(5, 9) if mode == "rapid" else random.uniform(15, 25))
            except Exception as e:
                print(f"  ⚠️ {e}")
        return f"✅ {sent} messages sent.\n"
    except Exception as e:
        return f"❌ Error sending messages: {e}\n"

# Display cloned messages
def display_cloned_messages():
    try:
        if not os.path.exists("cloned_messages.csv"):
            return "❌ No cloned messages.\n"
        with open("cloned_messages.csv", encoding='UTF-8') as f:
            rows = csv.reader(f, delimiter=",", lineterminator="\n")
            next(rows, None)
            messages = [row for row in rows]
            if not messages:
                return "📋 No cloned messages.\n"
            response = ""
            for row in messages:
                if len(row) >= 4:
                    response += f"💬 {row[2]} (From ID: {row[1]}, Date: {row[3]})\n"
            response += f"📊 Total number of cloned messages: {len(messages)}.\n"
            return response
    except Exception as e:
        return f"❌ Error displaying cloned messages: {e}\n"

# Edit cloned messages
def edit_cloned_messages():
    try:
        if not os.path.exists("cloned_messages.csv"):
            return "❌ No cloned messages to edit.\n"
        with open("cloned_messages.csv", encoding='UTF-8') as f:
            rows = list(csv.reader(f, delimiter=",", lineterminator="\n"))
        if not rows:
            return "❌ No cloned messages to edit.\n"
        header = rows[0]
        messages = rows[1:]
        if not messages:
            return "📋 No cloned messages to edit.\n"

        print("Available cloned messages for editing:")
        for i, row in enumerate(messages):
            txt = row[2] if len(row) > 2 else ''
            print(f"{i + 1}. 💬 {txt[:80]}")

        try:
            index = int(input("Enter the message number to edit: ")) - 1
        except ValueError:
            return "❌ Invalid number.\n"
        if 0 <= index < len(messages):
            if len(messages[index]) < 3:
                return "❌ Malformed row, cannot edit.\n"
            new_message = input("Enter the new message content: ")
            messages[index][2] = new_message
            with open("cloned_messages.csv", "w", encoding='UTF-8') as f:
                writer = csv.writer(f, delimiter=",", lineterminator="\n")
                writer.writerow(header)
                writer.writerows(messages)
            return "✅ Message edited successfully.\n"
        else:
            return "❌ Invalid message number.\n"
    except Exception as e:
        return f"❌ Error editing cloned messages: {e}\n"

# Delete cloned messages
def delete_cloned_messages():
    try:
        if os.path.exists("cloned_messages.csv"):
            os.remove("cloned_messages.csv")
            return "🗑️ Cloned messages deleted successfully.\n"
        else:
            return "❌ No cloned messages to delete.\n"
    except Exception as e:
        return f"❌ Error deleting cloned messages: {e}\n"

# Clear orphan session cache files (not belonging to configured accounts)
def clear_cache():
    configured_phones = {a['phone'] for a in config['accounts']}
    orphans = [f for f in os.listdir()
               if f.endswith('.session') and not any(p in f for p in configured_phones)]
    if not orphans:
        return "🗑️ No orphan session files to remove.\n"
    for f in orphans:
        os.remove(f)
    return f"🗑️ {len(orphans)} orphan session file(s) removed.\n"

# Loading animation
def loading_animation(action, delay=0.1):
    while action['running']:
        for frame in r"-\|/-\|/":
            print("\rLoading " + frame, end="")
            time.sleep(delay)
    print("\rDone!           ")

# ─── CLI Helpers ──────────────────────────────────────────────────────────────
def _clear_screen():
    os.system('clear' if os.name != 'nt' else 'cls')


def _count_csv_members(path='members.csv'):
    if not os.path.exists(path):
        return 0
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return max(0, sum(1 for _ in f) - 1)
    except Exception:
        return 0


def _status_bar(last_result="", session_status=None):
    total   = len(config.get('accounts', []))
    active  = sum(1 for a in config.get('accounts', []) if not a.get('blacklisted'))
    blacked = total - active
    proxies = len(config.get('proxies', []))
    members = _count_csv_members()
    expired = sum(1 for _, e in session_status if e) if session_status else 0

    G, W, Y, R, rst = Fore.GREEN, Style.BRIGHT, Fore.YELLOW, Fore.RED, Style.RESET_ALL
    SEP = 54
    print(G + "╔" + "═" * SEP + "╗" + rst)
    title = "🤖  TELEGRAM MANAGER  🤖"
    print(G + "║" + rst + W + title.center(SEP) + rst + G + "║" + rst)
    print(G + "╠" + "═" * SEP + "╣" + rst)

    acct_str = f"  👤 {active}/{total} active"
    if blacked:
        acct_str += f"  {Y}⏸ {blacked} blacklisted{rst}"
    if expired:
        acct_str += f"  {R}⚠ {expired} expired{rst}"
    print(G + "║" + rst + acct_str)

    mem_c = Fore.GREEN if members > 0 else Fore.YELLOW
    prx_c = Fore.GREEN if proxies > 0 else Fore.WHITE
    print(G + "║" + rst +
          f"  👥 members: {mem_c}{members}{rst}"
          f"   🌐 proxies: {prx_c}{proxies}{rst}")

    if last_result:
        first_line = next((ln for ln in last_result.splitlines() if ln.strip()), "")
        if len(first_line) > SEP - 4:
            first_line = first_line[:SEP - 7] + "..."
        print(G + "║" + rst + f"  ↩  {first_line}")

    print(G + "╚" + "═" * SEP + "╝" + rst)


def _compact_menu():
    G, C, B, Y, M, LG, R, W, rst = (
        Fore.GREEN, Fore.CYAN, Fore.BLUE, Fore.YELLOW,
        Fore.MAGENTA, Fore.LIGHTGREEN_EX, Fore.RED,
        Style.BRIGHT, Style.RESET_ALL)
    print()
    print(f"  {G}👤 ACCOUNTS      {rst}8-11, 17, 19")
    print(f"  {C}🌐 PROXIES       {rst}12-14, 18")
    print(f"  {Y}👥 MEMBERS       {rst}1-5, 16")
    print(f"  {M}📁 CSV           {rst}6, 7, 16")
    print(f"  {LG}💬 MESSAGES     {rst}20-24")
    print(f"  {R}🗑️  OTHER         {rst}25")
    print()
    print(f"  {W}[?]{rst} full menu   {W}[15]{rst} quit")


def _full_menu():
    G, C, B, Y, M, LG, R, rst = (
        Fore.GREEN, Fore.CYAN, Fore.BLUE, Fore.YELLOW,
        Fore.MAGENTA, Fore.LIGHTGREEN_EX, Fore.RED, Style.RESET_ALL)
    print(G + "\n👤 ACCOUNT MANAGEMENT" + rst)
    print("   8 - 🔌 Connect a new account")
    print("   9 - 📋 List connected accounts")
    print("  10 - 🗑️  Delete a connected account")
    print("  11 - ⏸️  Quarantine a blacklisted account (48h)")
    print("  17 - 🔍 Select an account and check for restrictions")
    print("  19 - 🔍 Check restrictions on all accounts")

    print(C + "\n🌐 PROXY MANAGEMENT" + rst)
    print("  12 - 🛡️  Add a proxy")
    print("  13 - ❌ Delete a proxy")
    print("  14 - 🌐 List proxies")
    print("  18 - 🌐 Test a proxy")

    print(Y + "\n👥 USER MANAGEMENT" + rst)
    print("   1 - 🕵️  Scrape users")
    print("   2 - ➕ Add users to a group")
    print("   3 - 🗑️  Delete saved users")
    print("   4 - 👥 Display saved users")
    print("   5 - 🚮 Filter and remove inactive/fake users")

    print(M + "\n📁 CSV FILE MANAGEMENT" + rst)
    print("   6 - 💾 Save scraped members to a new CSV file")
    print("   7 - 📂 Manage backup CSV files")
    print("  16 - 💾 Save scraped members (append or overwrite)")

    print(LG + "\n💬 MESSAGE MANAGEMENT" + rst)
    print("  20 - 📋 Clone messages from a competitor group")
    print("  21 - 🚀 Send cloned messages to a group")
    print("  22 - 👁️  Display cloned messages")
    print("  23 - ✏️  Edit cloned messages")
    print("  24 - 🗑️  Delete cloned messages")

    print(R + "\n🗑️  OTHER" + rst)
    print("  25 - 🗑️  Clear cache")
    print("  15 - 🚪 Quit")


def _pager(text, page_size=22):
    """Display long text page by page."""
    lines = text.splitlines()
    if len(lines) <= page_size:
        print(text)
        return
    try:
        for i in range(0, len(lines), page_size):
            print("\n".join(lines[i:i + page_size]))
            if i + page_size < len(lines):
                remaining = len(lines) - i - page_size
                key = input(
                    f"\n  {Style.BRIGHT}── {remaining} lines remaining ──"
                    f"  [Enter] continue  [q] quit{Style.RESET_ALL}: "
                ).strip().lower()
                if key == 'q':
                    break
    except (KeyboardInterrupt, EOFError):
        pass


def _confirm(msg):
    """Ask user to confirm a destructive action. Returns True if confirmed."""
    try:
        ans = input(
            f"{Fore.RED}⚠️  {msg} (y/N): {Style.RESET_ALL}"
        ).strip().lower()
        return ans in ('y', 'yes')
    except (KeyboardInterrupt, EOFError):
        return False


def _startup_session_check():
    """Check all accounts for expired sessions at startup. Returns [(phone, expired), ...]."""
    results = []
    for acct in config.get('accounts', []):
        phone = acct.get('phone', '?')
        if not os.path.exists(f"{phone}.session"):
            results.append((phone, True))
            continue
        try:
            c = TelegramClient(phone, int(acct['api_id']), acct['api_hash'])
            c.connect()
            expired = not c.is_user_authorized()
            c.disconnect()
            results.append((phone, expired))
        except Exception:
            results.append((phone, True))
    return results


# ─── Main menu ────────────────────────────────────────────────────────────────
def main():
    check_blacklisted_accounts()

    # ── Session check at startup
    session_status = None
    if config.get('accounts'):
        print(f"{Fore.YELLOW}  Checking sessions...{Style.RESET_ALL}", end='\r')
        session_status = _startup_session_check()
        expired_list = [(p, e) for p, e in session_status if e]
        if expired_list:
            _clear_screen()
            print(f"\n{Fore.RED}⚠️  Expired sessions:{Style.RESET_ALL}")
            for phone, _ in expired_list:
                print(f"   {Fore.RED}●{Style.RESET_ALL} {phone}")
            print(
                f"\n  → Use option {Style.BRIGHT}8{Style.RESET_ALL}"
                f" to reconnect an account.\n"
            )
            input("  [Enter] to continue...")

    # Helper: get a random active (non-blacklisted) account
    def _get_active():
        active = [a for a in config['accounts'] if not a['blacklisted']]
        return random.choice(active) if active else None

    show_full = False
    last_result = ""

    while True:
        try:
            _clear_screen()
            _status_bar(last_result=last_result, session_status=session_status)
            if show_full:
                _full_menu()
                show_full = False
            else:
                _compact_menu()

            choice = input(f"\n{Style.BRIGHT}▶ {Style.RESET_ALL}").strip()
            result = ""

            if choice == '?':
                show_full = True
                continue

            if choice == '1':
                account = _get_active()
                if not account:
                    result = "❌ No active account. Connect one first (option 8).\n"
                else:
                    print("1 - Via active groups  2 - Enter group name/ID/link")
                    sub_choice = input("Your choice: ").strip()
                    client = None
                    try:
                        client = get_client(account)
                        if sub_choice == '1':
                            source_group, error = choose_group_from_active(client)
                            if error:
                                result = error
                            else:
                                online_only = input("All (1) or online only (2)? ").strip() == '2'
                                result = scrape_members(client, source_group, online_only=online_only)
                        elif sub_choice == '2':
                            ident = input("Group name/ID/link: ").strip()
                            source_group, error = get_group_by_name(client, ident)
                            if error:
                                result = error
                            else:
                                online_only = input("All (1) or online only (2)? ").strip() == '2'
                                result = scrape_members(client, source_group, online_only=online_only)
                        else:
                            result = "❌ Invalid choice.\n"
                    except Exception as e:
                        result = f"❌ Error: {e}\n"
                    finally:
                        if client:
                            try: client.disconnect()
                            except Exception: pass

            elif choice == '2':
                account = _get_active()
                if not account:
                    result = "❌ No active account. Connect one first (option 8).\n"
                else:
                    print("1 - Active group  2 - Enter group name/ID/link")
                    sub_choice = input("Your choice: ").strip()
                    client = None
                    try:
                        client = get_client(account)
                        if sub_choice in ('1', '2'):
                            if sub_choice == '1':
                                target_group, error = choose_group_from_active(client)
                            else:
                                ident = input("Group name/ID/link: ").strip()
                                target_group, error = get_group_by_name(client, ident)
                            if error:
                                result = error
                            else:
                                restr = check_group_restrictions(client, target_group)
                                print(restr)
                                input_file = input("CSV file (Enter = members.csv): ").strip() or 'members.csv'
                                if not os.path.exists(input_file):
                                    result = f"❌ File {input_file} not found.\n"
                                else:
                                    with open(input_file, encoding='UTF-8') as f:
                                        n_avail = len(list(csv.reader(f))) - 1
                                    num_to_add = input(f"Number to add (available: {n_avail}, Enter = all): ").strip()
                                    mode = input("Mode: normal/turbo: ").strip().lower()
                                    result = add_members(client, target_group,
                                                         input_file=input_file,
                                                         num_members=num_to_add or None,
                                                         mode=mode)
                        else:
                            result = "❌ Invalid choice.\n"
                    except Exception as e:
                        result = f"❌ Error: {e}\n"
                    finally:
                        if client:
                            try: client.disconnect()
                            except Exception: pass

            elif choice == '3':
                if _confirm("Delete members.csv permanently?"):
                    result = delete_saved_users()
                else:
                    result = "↩ Cancelled.\n"

            elif choice == '4':  result = display_saved_users()
            elif choice == '5':  result = filter_and_remove_inactive_or_fake()
            elif choice == '6':  result = save_scrapped_members_as()
            elif choice == '7':  result = manage_backup_files()
            elif choice == '8':  result = connect_new_account()
            elif choice == '9':  result = list_connected_accounts()
            elif choice == '10':
                if _confirm("Delete this account permanently?"):
                    result = delete_connected_account()
                else:
                    result = "↩ Cancelled.\n"

            elif choice == '11':
                if not config['accounts']:
                    result = list_connected_accounts()
                else:
                    print(list_connected_accounts())
                    try:
                        index = int(input('Account number to quarantine (48h): ')) - 1
                        result = blacklist_account(index)
                    except ValueError:
                        result = "❌ Invalid number.\n"

            elif choice == '12': result = add_proxy()
            elif choice == '13': result = delete_proxy()
            elif choice == '14': result = list_proxies()
            elif choice == '15':
                _clear_screen()
                print(f"\n{Fore.GREEN}  🚪 Goodbye!{Style.RESET_ALL}\n")
                break

            elif choice == '16': result = save_scrapped_members_append_or_overwrite()
            elif choice == '17': result = select_account_and_check_restrictions()
            elif choice == '18': result = test_proxy()
            elif choice == '19':
                check_all_accounts_restrictions()
                result = ""

            elif choice == '20':
                account = _get_active()
                if not account:
                    result = "❌ No active account. Connect one first (option 8).\n"
                else:
                    client = None
                    try:
                        client = get_client(account)
                        group_identifier = input("Name, ID or link of the competitor group: ")
                        source_group, error = get_group_by_name(client, group_identifier)
                        if error:
                            result = error
                        else:
                            result = clone_group_messages(client, source_group)
                    except Exception as e:
                        result = f"❌ Error: {e}\n"
                    finally:
                        if client:
                            try: client.disconnect()
                            except Exception: pass

            elif choice == '21':
                account = _get_active()
                if not account:
                    result = "❌ No active account. Connect one first (option 8).\n"
                else:
                    client = None
                    try:
                        client = get_client(account)
                        group_identifier = input("Name, ID or link of the target group: ")
                        target_group, error = get_group_by_name(client, group_identifier)
                        if error:
                            result = error
                        else:
                            input_file = input("CSV file (Enter = cloned_messages.csv): ").strip() or 'cloned_messages.csv'
                            mode = input("Mode: normal (15-25s) or rapid (5-9s): ").lower()
                            result = send_cloned_messages(client, target_group, input_file=input_file, mode=mode)
                    except Exception as e:
                        result = f"❌ Error: {e}\n"
                    finally:
                        if client:
                            try: client.disconnect()
                            except Exception: pass

            elif choice == '22': result = display_cloned_messages()
            elif choice == '23': result = edit_cloned_messages()
            elif choice == '24':
                if _confirm("Delete all cloned messages?"):
                    result = delete_cloned_messages()
                else:
                    result = "↩ Cancelled.\n"

            elif choice == '25':
                if _confirm("Clear orphan session cache?"):
                    result = clear_cache()
                else:
                    result = "↩ Cancelled.\n"

            else:
                result = (
                    f"{Fore.YELLOW}  ❓ Invalid choice."
                    f" Type {Style.BRIGHT}?{Style.RESET_ALL}{Fore.YELLOW}"
                    f" for the full menu.{Style.RESET_ALL}\n"
                )

            if result:
                _pager(result)
                last_result = result

        except KeyboardInterrupt:
            print(
                f"\n{Fore.YELLOW}  ↩  Ctrl+C — type {Style.BRIGHT}15{Style.RESET_ALL}"
                f"{Fore.YELLOW} to quit cleanly.{Style.RESET_ALL}"
            )
            time.sleep(1)
            continue


if __name__ == "__main__":
    main()

