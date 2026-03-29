import os
import json
import csv
import time
import random
import threading
import logging
import sqlite3
import sys
import itertools
import shutil
import tempfile
import asyncio
from datetime import datetime

# Python 3.10+ no longer creates an event loop automatically.
# Telethon's sync wrapper requires one to exist before any client.start() call.
try:
    _loop = asyncio.get_event_loop()
    if _loop.is_closed():
        raise RuntimeError("closed")
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

from telethon.sync import TelegramClient
from telethon.tl.functions.messages import (GetDialogsRequest, GetHistoryRequest,
                                             ExportChatInviteRequest, EditChatAboutRequest)
from telethon.tl.types import (InputPeerEmpty, InputPeerChannel, InputPeerUser,
                                UserStatusRecently, UserStatusOnline, PeerChannel,
                                ChatAdminRights, InputChannel, ChannelParticipantsAdmins,
                                InputChatUploadedPhoto, InputMessagesFilterPhotos,
                                MessageService, MessageActionChatAddUser)
from telethon.errors.rpcerrorlist import (PeerFloodError, UserPrivacyRestrictedError,
                                           FloodWaitError, SessionPasswordNeededError,
                                           UserAlreadyParticipantError)
from telethon.tl.functions.channels import (InviteToChannelRequest, GetFullChannelRequest,
                                             EditTitleRequest, EditAdminRequest,
                                             GetParticipantsRequest,
                                             EditPhotoRequest)
from colorama import Fore, Style, init as colorama_init

try:
    from tqdm import tqdm
    TQDM_AVAILABLE = True
except ImportError:
    TQDM_AVAILABLE = False

colorama_init()

# ─── Constants ────────────────────────────────────────────────────────────────
CONFIG_FILE   = 'config.json'
PROGRESS_FILE = 'add_progress.json'
HISTORY_DB    = 'history.db'
LOG_FILE      = 'activity.log'
CSV_HEADER    = ['username', 'user_id', 'access_hash', 'name', 'group', 'group_id']

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
def log(msg, level='info'):
    getattr(logging, level)(msg)

# ─── Config ───────────────────────────────────────────────────────────────────
if os.path.exists(CONFIG_FILE):
    with open(CONFIG_FILE, 'r') as _f:
        config = json.load(_f)
else:
    config = {"accounts": [], "proxies": []}

def save_config():
    with open(CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=4)

# ─── SQLite history ───────────────────────────────────────────────────────────
def init_history_db():
    conn = sqlite3.connect(HISTORY_DB)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS add_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER, username TEXT, name TEXT,
        target_group TEXT, account_phone TEXT, status TEXT, timestamp TEXT
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS scrape_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source_group TEXT, member_count INTEGER, account_phone TEXT, timestamp TEXT
    )''')
    conn.commit()
    conn.close()

init_history_db()

def record_add(user_id, username, name, target_group, account_phone, status):
    try:
        conn = sqlite3.connect(HISTORY_DB)
        conn.execute(
            'INSERT INTO add_history (user_id,username,name,target_group,account_phone,status,timestamp) VALUES (?,?,?,?,?,?,?)',
            (user_id, username, name, target_group, account_phone, status, datetime.now().isoformat()))
        conn.commit(); conn.close()
    except Exception:
        pass

def record_scrape(source_group, member_count, account_phone):
    try:
        conn = sqlite3.connect(HISTORY_DB)
        conn.execute(
            'INSERT INTO scrape_history (source_group,member_count,account_phone,timestamp) VALUES (?,?,?,?)',
            (source_group, member_count, account_phone, datetime.now().isoformat()))
        conn.commit(); conn.close()
    except Exception:
        pass

# ─── Utilities ────────────────────────────────────────────────────────────────
def loading_animation(message):
    stop = threading.Event()
    def animate():
        for c in itertools.cycle(['|', '/', '-', '\\']):
            if stop.is_set(): break
            sys.stdout.write(f'\r{message} {c}')
            sys.stdout.flush()
            time.sleep(0.1)
        sys.stdout.write('\r' + ' ' * (len(message) + 2) + '\r')
    threading.Thread(target=animate, daemon=True).start()
    return stop

def safe_int_input(prompt, min_val=0, max_val=None):
    try:
        v = int(input(prompt))
        if v < min_val or (max_val is not None and v > max_val):
            return None
        return v
    except ValueError:
        return None

def progress_iter(iterable, desc=""):
    if TQDM_AVAILABLE:
        return tqdm(iterable, desc=desc, unit="item")
    return iterable

# ─── Progress tracking ────────────────────────────────────────────────────────
def load_progress(csv_file):
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE, 'r') as f:
            return json.load(f).get(csv_file, 0)
    return 0

def save_progress(csv_file, count):
    data = {}
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE, 'r') as f:
            data = json.load(f)
    data[csv_file] = count
    with open(PROGRESS_FILE, 'w') as f:
        json.dump(data, f)

def clear_progress(csv_file):
    if not os.path.exists(PROGRESS_FILE):
        return
    with open(PROGRESS_FILE, 'r') as f:
        data = json.load(f)
    data.pop(csv_file, None)
    with open(PROGRESS_FILE, 'w') as f:
        json.dump(data, f)

# ─── CSV helpers ──────────────────────────────────────────────────────────────
def read_members_csv(filepath='members.csv'):
    if not os.path.exists(filepath):
        return []
    users = []
    with open(filepath, encoding='UTF-8') as f:
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
    return users

def write_members_csv(users, filepath='members.csv'):
    with open(filepath, 'w', encoding='UTF-8') as f:
        writer = csv.writer(f, delimiter=",", lineterminator="\n")
        writer.writerow(CSV_HEADER)
        for u in users:
            writer.writerow([u['username'], u['id'], u['access_hash'],
                             u['name'], u['group'], u['group_id']])

# ─── Account management ───────────────────────────────────────────────────────
def get_active_account():
    active = [a for a in config['accounts'] if not a['blacklisted']]
    return random.choice(active) if active else None

def connect_new_account():
    api_id   = input('API ID: ').strip()
    api_hash = input('API Hash: ').strip()
    phone    = input('Numéro de téléphone: ').strip()
    try:
        api_id_int = int(api_id)
    except ValueError:
        return '❌ API ID invalide (doit être un entier).\n'
    if any(a['phone'] == phone for a in config['accounts']):
        return f'⚠️ Compte {phone} déjà enregistré.\n'
    client = TelegramClient(phone, api_id_int, api_hash)
    try:
        client.connect()
        if not client.is_user_authorized():
            client.send_code_request(phone)
            try:
                client.sign_in(phone, input('📱 Code reçu: '))
            except SessionPasswordNeededError:
                client.sign_in(password=input('🛡️ Mot de passe 2FA: '))
        config['accounts'].append({"api_id": api_id, "api_hash": api_hash,
                                    "phone": phone, "blacklisted": False, "blacklist_time": None})
        save_config()
        log(f"Compte connecté: {phone}")
        return f'✅ Compte {phone} connecté.\n'
    except Exception as e:
        return f'❌ Erreur: {e}\n'
    finally:
        client.disconnect()

def list_connected_accounts():
    if not config['accounts']:
        return "📋 0 compte connecté.\n"
    resp = ""
    for idx, a in enumerate(config['accounts']):
        if a['blacklisted'] and a['blacklist_time']:
            remaining = max(0, 48 * 3600 - (time.time() - a['blacklist_time']))
            h, m = divmod(int(remaining), 3600)
            m //= 60
            status = f"🔴 Blacklisté (encore {h}h{m:02d}m)"
        else:
            status = "🟢 Actif"
        resp += f"  {idx + 1}. {a['phone']} — {status}\n"
    return resp

def delete_connected_account():
    if not config['accounts']:
        return list_connected_accounts()
    print(list_connected_accounts())
    idx = safe_int_input('Numéro à supprimer: ', min_val=1, max_val=len(config['accounts']))
    if idx is None:
        return '❌ Numéro invalide.\n'
    phone = config['accounts'][idx - 1]['phone']
    del config['accounts'][idx - 1]
    save_config()
    log(f"Compte supprimé: {phone}")
    return f'✅ Compte {phone} supprimé.\n'

def blacklist_account(index):
    if 0 <= index < len(config['accounts']):
        config['accounts'][index]['blacklisted'] = True
        config['accounts'][index]['blacklist_time'] = time.time()
        save_config()
        log(f"Compte blacklisté: {config['accounts'][index]['phone']}")
        return '✅ Compte mis en quarantaine 48h.\n'
    return '❌ Numéro invalide.\n'

def check_blacklisted_accounts():
    changed = False
    for a in config['accounts']:
        if a['blacklisted'] and a['blacklist_time'] and time.time() - a['blacklist_time'] > 48 * 3600:
            a['blacklisted'] = False
            a['blacklist_time'] = None
            changed = True
            log(f"Quarantaine levée: {a['phone']}")
    if changed:
        save_config()

def reconnect_account(index):
    account = config['accounts'][index]
    client = None
    try:
        client = get_client(account)
        if not client.is_user_authorized():
            client.send_code_request(account['phone'])
            try:
                client.sign_in(account['phone'], input('📱 Code: '))
            except SessionPasswordNeededError:
                client.sign_in(password=input('🛡️ 2FA: '))
        return f'✅ {account["phone"]} reconnecté.\n'
    except Exception as e:
        return f'❌ Erreur ({account["phone"]}): {e}\n'
    finally:
        if client:
            try: client.disconnect()
            except Exception: pass

def reconnect_all_accounts():
    return "".join(reconnect_account(i) for i in range(len(config['accounts'])))

def show_account_profile(index):
    if not (0 <= index < len(config['accounts'])):
        return '❌ Numéro invalide.\n'
    account = config['accounts'][index]
    client = None
    try:
        client = get_client(account)
        me = client.get_me()
        resp  = f"👤 Profil — {account['phone']}:\n"
        resp += f"  Nom      : {me.first_name or ''} {me.last_name or ''}\n"
        resp += f"  Username : @{me.username}\n" if me.username else "  Username : (aucun)\n"
        resp += f"  ID       : {me.id}\n"
        resp += f"  Bot      : {'Oui' if me.bot else 'Non'}\n"
        return resp
    except Exception as e:
        return f'❌ Erreur: {e}\n'
    finally:
        if client:
            try: client.disconnect()
            except Exception: pass

def show_account_stats():
    try:
        conn = sqlite3.connect(HISTORY_DB)
        c = conn.cursor()
        c.execute('SELECT account_phone, status, COUNT(*) FROM add_history GROUP BY account_phone, status ORDER BY account_phone')
        rows = c.fetchall()
        conn.close()
        if not rows:
            return "📊 Aucune statistique disponible.\n"
        resp = "📊 Statistiques par compte:\n"
        current = None
        for phone, status, count in rows:
            if phone != current:
                resp += f"\n  📱 {phone}:\n"
                current = phone
            emoji = {"success": "✅", "already": "ℹ️", "privacy": "🔒"}.get(status, "❌")
            resp += f"    {emoji} {status}: {count}\n"
        return resp
    except Exception as e:
        return f'❌ Erreur: {e}\n'

def select_account_and_check_restrictions():
    if not config['accounts']:
        return list_connected_accounts()
    print(list_connected_accounts())
    idx = safe_int_input('Numéro à vérifier: ', min_val=1, max_val=len(config['accounts']))
    if idx is None:
        return '❌ Numéro invalide.\n'
    account = config['accounts'][idx - 1]
    if account['blacklisted']:
        return f"🔴 {account['phone']} est en quarantaine.\n"
    client = None
    try:
        client = get_client(account)
        client.get_me()
        return f"🟢 {account['phone']} actif et sans restrictions.\n"
    except Exception as e:
        return f"🔴 {account['phone']}: {get_restriction_details(e)}\n"
    finally:
        if client:
            try: client.disconnect()
            except Exception: pass

def check_all_accounts_restrictions():
    results = []
    for idx, account in enumerate(config['accounts']):
        if account['blacklisted']:
            results.append((idx, f"🔴 {account['phone']} en quarantaine.\n"))
            continue
        client = None
        try:
            client = get_client(account)
            client.get_me()
            results.append((idx, f"🟢 {account['phone']} actif.\n"))
        except Exception as e:
            results.append((idx, f"🔴 {account['phone']}: {get_restriction_details(e)}\n"))
        finally:
            if client:
                try: client.disconnect()
                except Exception: pass
    for _, r in results:
        print(r)
    if input("Mettre les comptes restreints en quarantaine 48h? (o/n): ").lower() == 'o':
        for idx, r in results:
            if "🔴" in r and "quarantaine" not in r:
                print(blacklist_account(idx))

def get_restriction_details(exception):
    s = str(exception).lower()
    if "disconnected" in s: return "Compte déconnecté."
    if "flood" in s:        return "Restreint temporairement (flood)."
    if "privacy" in s:      return "Paramètres de confidentialité restrictifs."
    return str(exception)

# ─── Proxy management ─────────────────────────────────────────────────────────
def add_proxy():
    proxy = input('Proxy (ip:port ou ip:port:user:pass): ').strip()
    parts = proxy.split(':')
    if len(parts) not in (2, 4):
        return "❌ Format invalide. Utilisez ip:port ou ip:port:user:pass\n"
    try:
        int(parts[1])
    except ValueError:
        return "❌ Le port doit être un entier.\n"
    if proxy in config['proxies']:
        return "⚠️ Proxy déjà présent.\n"
    config['proxies'].append(proxy)
    save_config()
    return f'✅ Proxy {proxy} ajouté.\n'

def delete_proxy():
    if not config['proxies']:
        return "🌐 Aucun proxy configuré.\n"
    print(list_proxies())
    idx = safe_int_input('Numéro à supprimer: ', min_val=1, max_val=len(config['proxies']))
    if idx is None:
        return '❌ Numéro invalide.\n'
    proxy = config['proxies'].pop(idx - 1)
    save_config()
    return f'✅ Proxy {proxy} supprimé.\n'

def list_proxies():
    if not config['proxies']:
        return "🌐 0 proxy configuré.\n"
    return "".join(f"  {i+1}. {p}\n" for i, p in enumerate(config['proxies']))

_PROXY_TEST_SESSION = os.path.join(tempfile.gettempdir(), 'tg_proxy_test')

def _make_test_client(proxy, account):
    parts = proxy.split(':')
    if len(parts) == 2:
        return TelegramClient(_PROXY_TEST_SESSION, int(account['api_id']), account['api_hash'],
                              proxy=('socks5', parts[0], int(parts[1])))
    elif len(parts) == 4:
        return TelegramClient(_PROXY_TEST_SESSION, int(account['api_id']), account['api_hash'],
                              proxy=('socks5', parts[0], int(parts[1]), True, parts[2], parts[3]))
    return None

def _cleanup_proxy_test_session():
    for ext in ('', '.session', '.session-journal'):
        try:
            os.remove(_PROXY_TEST_SESSION + ext)
        except OSError:
            pass

def test_proxy(proxy=None):
    if not config['accounts']:
        return '❌ Aucun compte pour tester.\n'
    if proxy is None:
        proxy = input('Proxy à tester: ').strip()
    account = config['accounts'][0]
    tc = None
    try:
        tc = _make_test_client(proxy, account)
        if not tc:
            return "❌ Format invalide.\n"
        tc.connect()
        return f"🟢 Proxy {proxy} OK.\n"
    except Exception as e:
        return f"🔴 Proxy {proxy} KO: {e}\n"
    finally:
        if tc:
            try: tc.disconnect()
            except Exception: pass
        _cleanup_proxy_test_session()

def test_all_proxies():
    valid = [p for p in config['proxies'] if p.lower() != 'exit' and ':' in p]
    if not valid:
        return "🌐 Aucun proxy valide à tester.\n"
    return "".join(test_proxy(p) for p in valid)

def import_proxies_from_file():
    path = input('Fichier de proxies (.txt, un par ligne): ').strip()
    if not os.path.exists(path):
        return f'❌ Fichier {path} introuvable.\n'
    added = 0
    with open(path, encoding='UTF-8') as f:
        for line in f:
            proxy = line.strip()
            if not proxy or proxy in config['proxies']:
                continue
            parts = proxy.split(':')
            if len(parts) in (2, 4):
                try:
                    int(parts[1])
                    config['proxies'].append(proxy)
                    added += 1
                except ValueError:
                    pass
    save_config()
    return f'✅ {added} proxy(s) importé(s).\n'

# ─── Client factory ───────────────────────────────────────────────────────────
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

def reset_database_connection(client):
    try:
        client.disconnect()
        client.connect()
    except Exception as e:
        print(f"⚠️ Reset connexion: {e}")

# ─── Group resolution ─────────────────────────────────────────────────────────
def get_group_by_name(client, group_identifier):
    for _ in range(3):
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
                print("🔒 DB verrouillée, nouvelle tentative...")
                reset_database_connection(client)
                time.sleep(2)
            else:
                return None, f"❌ Erreur DB: {e}\n"
        except Exception as e:
            return None, f"❌ Groupe '{group_identifier}' introuvable: {e}\n"
    return None, "❌ DB verrouillée après plusieurs tentatives.\n"

def choose_group_from_active(client):
    if not client.is_connected():
        client.connect()
    result = client(GetDialogsRequest(offset_date=None, offset_id=0,
                                      offset_peer=InputPeerEmpty(), limit=200, hash=0))
    groups = [c for c in result.chats if hasattr(c, 'megagroup') and c.megagroup]
    if not groups:
        return None, "❌ Aucun groupe disponible.\n"
    print('Choisissez un groupe:')
    for i, g in enumerate(groups):
        print(f"  {i} - {g.title}")
    idx = safe_int_input("📋 Numéro: ", min_val=0, max_val=len(groups) - 1)
    if idx is None:
        return None, "❌ Numéro invalide.\n"
    return groups[idx], ""

# ─── Scraping ─────────────────────────────────────────────────────────────────
def is_user_online(status):
    return isinstance(status, (UserStatusOnline, UserStatusRecently))

def scrape_members(client, target_group, online_only=False, max_users=100000, output_file='members.csv'):
    stop = loading_animation("🔍 Récupération des membres...")
    try:
        if not client.is_connected():
            client.connect()
        all_participants = list(client.get_participants(target_group, aggressive=True))
        if len(all_participants) > max_users:
            all_participants = all_participants[:max_users]
        if online_only:
            all_participants = [u for u in all_participants if u.status and is_user_online(u.status)]
        users = [{'username': u.username or "", 'id': u.id, 'access_hash': u.access_hash,
                  'name': ((u.first_name or "") + " " + (u.last_name or "")).strip(),
                  'group': target_group.title, 'group_id': str(target_group.id)}
                 for u in all_participants]
        write_members_csv(users, output_file)
        try:
            me = client.get_me()
            record_scrape(target_group.title, len(users), me.phone or "?")
        except Exception:
            record_scrape(target_group.title, len(users), "?")
        log(f"Scraping: {len(users)} membres depuis '{target_group.title}'")
        return f'✅ {len(users)} membres enregistrés dans {output_file}.\n'
    except Exception as e:
        log(f"Erreur scraping: {e}", 'error')
        return f"❌ Erreur: {e}\n"
    finally:
        stop.set()

def scrape_admins(client, target_group):
    stop = loading_animation("👑 Récupération des admins...")
    try:
        result = client(GetParticipantsRequest(
            InputChannel(target_group.id, target_group.access_hash),
            ChannelParticipantsAdmins(), offset=0, limit=200, hash=0))
        resp = f"👑 {len(result.users)} admin(s) dans '{target_group.title}':\n"
        for a in result.users:
            resp += f"  @{a.username or '—'} — {a.first_name or ''} {a.last_name or ''} (ID: {a.id})\n"
        return resp
    except Exception as e:
        return f"❌ Erreur: {e}\n"
    finally:
        stop.set()

def scrape_bots(client, target_group):
    stop = loading_animation("🤖 Récupération des bots...")
    try:
        participants = list(client.get_participants(target_group, aggressive=True))
        bots = [u for u in participants if u.bot]
        resp = f"🤖 {len(bots)} bot(s) dans '{target_group.title}':\n"
        for b in bots:
            resp += f"  @{b.username or '—'} (ID: {b.id})\n"
        return resp
    except Exception as e:
        return f"❌ Erreur: {e}\n"
    finally:
        stop.set()

def scrape_multiple_groups(client):
    print("Entrez les groupes à scraper (ligne vide pour terminer):")
    identifiers = []
    while True:
        line = input("  Groupe: ").strip()
        if not line:
            break
        identifiers.append(line)
    if not identifiers:
        return "❌ Aucun groupe saisi.\n"
    all_users = {}
    for ident in identifiers:
        group, error = get_group_by_name(client, ident)
        if error:
            print(f"⚠️ {ident}: {error}")
            continue
        stop = loading_animation(f"🔍 Scraping '{group.title}'...")
        try:
            for u in client.get_participants(group, aggressive=True):
                if u.id not in all_users:
                    all_users[u.id] = {
                        'username': u.username or "", 'id': u.id, 'access_hash': u.access_hash,
                        'name': ((u.first_name or "") + " " + (u.last_name or "")).strip(),
                        'group': group.title, 'group_id': str(group.id)
                    }
        except Exception as e:
            print(f"⚠️ Erreur '{group.title}': {e}")
        finally:
            stop.set()
    write_members_csv(list(all_users.values()))
    log(f"Scraping multi-groupes: {len(all_users)} membres uniques")
    return f'✅ {len(all_users)} membres uniques depuis {len(identifiers)} groupe(s).\n'

def scrape_images(client, target_group):
    stop = loading_animation("🔍 Récupération des images...")
    try:
        all_messages = list(client.iter_messages(target_group, filter=InputMessagesFilterPhotos()))
        with open("images.csv", "w", encoding='UTF-8') as f:
            writer = csv.writer(f, delimiter=",", lineterminator="\n")
            writer.writerow(['message_id', 'from_id', 'file_id', 'date'])
            for msg in all_messages:
                if msg.photo:
                    writer.writerow([msg.id, msg.from_id, msg.photo.id, msg.date])
        return f'✅ {len(all_messages)} images enregistrées dans images.csv.\n'
    except Exception as e:
        return f"❌ Erreur: {e}\n"
    finally:
        stop.set()

# ─── Member management ────────────────────────────────────────────────────────
def deduplicate_csv():
    csv_files = sorted([f for f in os.listdir() if f.endswith('.csv')])
    if not csv_files:
        return "❌ Aucun fichier CSV trouvé.\n"
    print("Fichiers disponibles:")
    for i, f in enumerate(csv_files):
        print(f"  {i+1}. {f}")
    try:
        indices = [int(x) - 1 for x in input("Numéros à fusionner (ex: 1 2 3): ").split()]
        selected = [csv_files[i] for i in indices if 0 <= i < len(csv_files)]
    except (ValueError, IndexError):
        return "❌ Sélection invalide.\n"
    if not selected:
        return "❌ Aucun fichier sélectionné.\n"
    all_users = {}
    for filepath in selected:
        for u in read_members_csv(filepath):
            if u['id'] not in all_users:
                all_users[u['id']] = u
    out = input("Nom du fichier de sortie (sans .csv): ").strip() + '.csv'
    write_members_csv(list(all_users.values()), out)
    return f'✅ {len(all_users)} membres uniques enregistrés dans {out}.\n'

def search_member():
    if not os.path.exists('members.csv'):
        return "❌ members.csv introuvable.\n"
    query = input("🔍 Rechercher (username, nom ou ID): ").strip().lower()
    users = read_members_csv()
    results = [u for u in users if query in u['username'].lower()
               or query in u['name'].lower() or query == str(u['id'])]
    if not results:
        return f"❌ Aucun résultat pour '{query}'.\n"
    resp = f"🔍 {len(results)} résultat(s):\n"
    for u in results:
        resp += f"  👤 {u['name'] or '(sans nom)'} (@{u['username'] or '—'}) ID:{u['id']}\n"
    return resp

def sort_members():
    users = read_members_csv()
    if not users:
        return "❌ Aucun membre dans members.csv.\n"
    print("Trier par:  1-Nom  2-ID  3-Username (avec username en premier)  4-Groupe")
    choice = input("Choix: ").strip()
    if choice == '1':
        users.sort(key=lambda u: u['name'].lower())
    elif choice == '2':
        users.sort(key=lambda u: u['id'])
    elif choice == '3':
        users.sort(key=lambda u: (u['username'] == '', u['username'].lower()))
    elif choice == '4':
        users.sort(key=lambda u: u['group'].lower())
    else:
        return "❌ Choix invalide.\n"
    write_members_csv(users)
    return f"✅ {len(users)} membres triés.\n"

def member_stats():
    users = read_members_csv()
    if not users:
        return "❌ Aucun membre dans members.csv.\n"
    total = len(users)
    with_username = sum(1 for u in users if u['username'])
    with_name     = sum(1 for u in users if u['name'])
    groups        = len(set(u['group'] for u in users))
    resp  = f"📊 Statistiques members.csv:\n"
    resp += f"  Total          : {total}\n"
    resp += f"  Avec username  : {with_username} ({with_username * 100 // total}%)\n"
    resp += f"  Avec nom       : {with_name} ({with_name * 100 // total}%)\n"
    resp += f"  Sans username  : {total - with_username}\n"
    resp += f"  Groupes sources: {groups}\n"
    try:
        conn = sqlite3.connect(HISTORY_DB)
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM add_history WHERE status='success'")
        added = c.fetchone()[0]
        conn.close()
        resp += f"  Ajoutés (hist) : {added}\n"
    except Exception as e:
        log(f"Erreur lecture stats historique: {e}", 'warning')
    return resp

def compare_csv():
    csv_files = sorted([f for f in os.listdir() if f.endswith('.csv')])
    if len(csv_files) < 2:
        return "❌ Besoin d'au moins 2 fichiers CSV.\n"
    print("Fichiers disponibles:")
    for i, f in enumerate(csv_files):
        print(f"  {i+1}. {f}")
    idx1 = safe_int_input("Premier fichier: ", min_val=1, max_val=len(csv_files))
    idx2 = safe_int_input("Deuxième fichier: ", min_val=1, max_val=len(csv_files))
    if idx1 is None or idx2 is None:
        return "❌ Sélection invalide.\n"
    users1 = {u['id']: u for u in read_members_csv(csv_files[idx1 - 1])}
    users2 = {u['id']: u for u in read_members_csv(csv_files[idx2 - 1])}
    only1  = {k: v for k, v in users1.items() if k not in users2}
    only2  = {k: v for k, v in users2.items() if k not in users1}
    both   = {k for k in users1 if k in users2}
    resp  = f"📊 Comparaison:\n"
    resp += f"  {csv_files[idx1-1]}: {len(users1)} membres\n"
    resp += f"  {csv_files[idx2-1]}: {len(users2)} membres\n"
    resp += f"  En commun               : {len(both)}\n"
    resp += f"  Uniques dans fichier 1  : {len(only1)}\n"
    resp += f"  Uniques dans fichier 2  : {len(only2)}\n"
    if only1 and input(f"Exporter les {len(only1)} uniques de fichier 1? (o/n): ").lower() == 'o':
        out = csv_files[idx1 - 1].replace('.csv', '_unique.csv')
        write_members_csv(list(only1.values()), out)
        resp += f"  ✅ Exportés dans {out}\n"
    return resp

def export_json(filepath='members.csv'):
    users = read_members_csv(filepath)
    if not users:
        return "❌ Aucun membre à exporter.\n"
    out = filepath.replace('.csv', '.json')
    with open(out, 'w', encoding='UTF-8') as f:
        json.dump(users, f, indent=2, ensure_ascii=False)
    return f"✅ {len(users)} membres exportés dans {out}.\n"

def verify_members_present(client, target_group):
    stop = loading_animation("🔍 Vérification des membres...")
    try:
        current_ids = {u.id for u in client.get_participants(target_group, aggressive=True)}
        csv_users   = read_members_csv()
        if not csv_users:
            return "❌ members.csv vide.\n"
        still_in = [u for u in csv_users if u['id'] in current_ids]
        not_in   = [u for u in csv_users if u['id'] not in current_ids]
        resp  = f"📊 Vérification dans '{target_group.title}':\n"
        resp += f"  ✅ Présents : {len(still_in)}\n"
        resp += f"  ❌ Absents  : {len(not_in)}\n"
        if not_in and input("Sauvegarder les absents dans 'absent.csv'? (o/n): ").lower() == 'o':
            write_members_csv(not_in, 'absent.csv')
            resp += "  💾 Sauvegardé dans absent.csv\n"
        return resp
    except Exception as e:
        return f"❌ Erreur: {e}\n"
    finally:
        stop.set()

def _is_bot_username(username):
    """Détecte les bots par leur username (finit par 'bot')."""
    if not username:
        return False
    return username.lower().endswith('bot')

def _is_spam_or_promo(username, name):
    """Détecte les comptes spammeurs/promo par mots-clés."""
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

def filter_and_remove_inactive_or_fake(input_file='members.csv'):
    users = read_members_csv(input_file)
    if not users:
        return "❌ members.csv vide.\n"
    before = len(users)
    seen_ids = set()
    active = []
    removed = {
        'no_id': 0, 'no_identity': 0, 'no_hash': 0,
        'duplicate': 0, 'bot': 0, 'spam': 0,
    }
    for u in users:
        # ID invalide
        if u['id'] <= 0:
            removed['no_id'] += 1
            continue
        # Ni username ni nom → compte supprimé/fantôme
        if not u['username'] and not u['name'].strip():
            removed['no_identity'] += 1
            continue
        # access_hash == 0 → inutilisable pour InviteToChannelRequest
        if u['access_hash'] == 0:
            removed['no_hash'] += 1
            continue
        # Doublon par user_id
        if u['id'] in seen_ids:
            removed['duplicate'] += 1
            continue
        # Bots
        if _is_bot_username(u['username']):
            removed['bot'] += 1
            continue
        # Spammeurs / promo
        if _is_spam_or_promo(u['username'], u['name']):
            removed['spam'] += 1
            continue
        seen_ids.add(u['id'])
        active.append(u)
    write_members_csv(active, input_file)
    total_removed = before - len(active)
    details = ", ".join(f"{v} {k}" for k, v in removed.items() if v > 0)
    return (
        f"✅ Filtrés: {before} → {len(active)} membres actifs"
        f" ({total_removed} supprimés{': ' + details if details else ''})\n"
    )

def kick_members(client, target_group, input_file='members.csv'):
    users = read_members_csv(input_file)
    if not users:
        return "❌ Aucun membre à retirer.\n"
    print(f"⚠️  Retirer {len(users)} membres de '{target_group.title}'.")
    if input("Confirmer? (oui/non): ").strip().lower() != 'oui':
        return "❌ Opération annulée.\n"
    kicked = errors = 0
    for user in progress_iter(users, "🚮 Retrait"):
        try:
            client.kick_participant(target_group, InputPeerUser(user['id'], user['access_hash']))
            kicked += 1
            time.sleep(random.uniform(1, 3))
        except Exception as e:
            print(f"  ⚠️ {e}")
            errors += 1
    log(f"Kick: {kicked} retirés, {errors} erreurs de '{target_group.title}'")
    return f"✅ {kicked} retirés, {errors} erreurs.\n"

# ─── Adding members ───────────────────────────────────────────────────────────

def _delete_join_messages(client, group, limit=5):
    """Supprime les messages 'X a rejoint le groupe' pour garder le chat propre."""
    try:
        msgs = client.get_messages(group, limit=limit)
        to_del = [m.id for m in msgs
                  if isinstance(m, MessageService) and isinstance(m.action, MessageActionChatAddUser)]
        if to_del:
            client.delete_messages(group, to_del)
        return len(to_del)
    except Exception:
        return 0

def add_members(target_group, input_file='members.csv', num_members=None,
                mode="normal", silent=True):
    """
    Ajoute des membres dans un groupe.
    Modes: 'safe' (60-90s), 'normal' (30-60s), 'turbo' (5-15s)
    silent: supprime les messages 'X a rejoint le groupe' (défaut: True)
    """
    if not config['accounts']:
        return "❌ Aucun compte configuré.\n"
    users = read_members_csv(input_file)
    if not users:
        return f"❌ Fichier {input_file} vide ou introuvable.\n"
    try:
        num_members = len(users) if (num_members is None or num_members == '') else int(num_members)
    except (ValueError, TypeError):
        return "❌ Nombre de membres invalide.\n"
    start_index = load_progress(input_file)
    if start_index > 0:
        print(f"⏩ Reprise depuis #{start_index + 1}...")
    target_entity   = InputPeerChannel(target_group.id, target_group.access_hash)
    active_accounts = [a for a in config['accounts'] if not a['blacklisted']]
    if not active_accounts:
        return "❌ Tous les comptes sont blacklistés.\n"

    # Timers par mode
    timers = {
        'safe':   (60, 90),
        'normal': (30, 60),
        'turbo':  (5, 15),
    }
    delay_min, delay_max = timers.get(mode, timers['normal'])

    # Pre-connect all accounts once
    connected = []
    for a in active_accounts:
        try:
            connected.append({'phone': a['phone'], 'client': get_client(a)})
        except Exception as e:
            print(f"⚠️ Connexion impossible ({a['phone']}): {e}")
            log(f"Connexion impossible ({a['phone']}): {e}", 'warning')
    if not connected:
        return "❌ Impossible de connecter un compte actif.\n"

    account_index = 0
    stats = {'added': 0, 'already': 0, 'privacy': 0, 'error': 0}
    batch = users[start_index:start_index + num_members]
    est_minutes = len(batch) * (delay_min + delay_max) / 2 / 60
    print(f'➕ Ajout de {len(batch)} membres dans "{target_group.title}" (mode {mode}, ~{est_minutes:.0f}min)...')
    if silent:
        print("🔇 Mode silencieux: messages de join supprimés automatiquement")
    try:
        for i, user in enumerate(batch, 1):
            entry = connected[account_index]
            current_phone = entry['phone']
            client        = entry['client']
            account_index = (account_index + 1) % len(connected)
            name_display = user['name'] or user['username'] or str(user['id'])
            try:
                print(f"  [{i}/{len(batch)}] ➕ {name_display} via {current_phone}...", end=" ", flush=True)
                client(InviteToChannelRequest(target_entity, [InputPeerUser(user['id'], user['access_hash'])]))
                stats['added'] += 1
                record_add(user['id'], user['username'], user['name'], target_group.title, current_phone, 'success')
                save_progress(input_file, start_index + sum(stats.values()))
                # Suppression silencieuse du message join
                if silent:
                    time.sleep(1)
                    deleted = _delete_join_messages(client, target_group)
                    print(f"✅ ajouté{' (msg join supprimé)' if deleted else ''}")
                else:
                    print("✅ ajouté")
                # Timer anti-ban
                if i < len(batch):
                    wait = random.uniform(delay_min, delay_max)
                    print(f"      ⏱️  Pause {wait:.0f}s...")
                    time.sleep(wait)
            except UserAlreadyParticipantError:
                stats['already'] += 1
                record_add(user['id'], user['username'], user['name'], target_group.title, current_phone, 'already')
                print("ℹ️  déjà membre")
            except UserPrivacyRestrictedError:
                stats['privacy'] += 1
                record_add(user['id'], user['username'], user['name'], target_group.title, current_phone, 'privacy')
                print("🔒 privacy restreint")
            except FloodWaitError as e:
                if e.seconds > 300:
                    save_progress(input_file, start_index + sum(stats.values()))
                    log(f"FloodWait {e.seconds}s sur {current_phone} — arrêt", 'warning')
                    print(f"\n🛑 FloodWait {e.seconds}s — arrêt sécurité. Progression sauvée.")
                    break
                print(f"⏳ FloodWait {e.seconds}s — attente...")
                time.sleep(e.seconds + 5)
            except PeerFloodError:
                save_progress(input_file, start_index + sum(stats.values()))
                log(f"PeerFloodError sur {current_phone}", 'warning')
                print("\n🛑 PeerFlood — arrêt sécurité. Progression sauvée.")
                return (f"🚫 Flood détecté. Progression sauvegardée. Réessayez dans 24h.\n"
                        f"📊 ✅{stats['added']} ℹ️{stats['already']} 🔒{stats['privacy']} ❌{stats['error']}\n")
            except Exception as e:
                stats['error'] += 1
                record_add(user['id'], user['username'], user['name'], target_group.title, current_phone, 'error')
                log(f"Erreur ajout user {user['id']} via {current_phone}: {e}", 'warning')
                print(f"⚠️  {str(e)[:80]}")
    finally:
        for entry in connected:
            try: entry['client'].disconnect()
            except Exception: pass

    clear_progress(input_file)
    log(f"Ajout terminé dans '{target_group.title}': {stats}")
    return (f"✅ Terminé.\n"
            f"📊 Rapport:\n"
            f"  ✅ Ajoutés     : {stats['added']}\n"
            f"  ℹ️  Déjà membres: {stats['already']}\n"
            f"  🔒 Privacy     : {stats['privacy']}\n"
            f"  ❌ Erreurs     : {stats['error']}\n")

def schedule_daily_add(target_group, input_file='members.csv', per_day=50, mode='normal'):
    users = read_members_csv(input_file)
    if not users:
        return f"❌ {input_file} vide.\n"
    start = load_progress(input_file)
    batch = users[start:start + per_day]
    if not batch:
        clear_progress(input_file)
        return "✅ Tous les membres ont déjà été traités.\n"
    print(f"📅 Session du jour: {len(batch)} membres (position {start}/{len(users)})...")
    tmp = f"_daily_batch_{int(time.time())}.csv"
    write_members_csv(batch, tmp)
    result = add_members(target_group, input_file=tmp, mode=mode)
    # If PeerFloodError stopped add_members mid-batch, it saved partial progress to tmp.
    # Use that count so we don't skip un-processed members on next run.
    flood_pos = load_progress(tmp)
    if flood_pos > 0:
        save_progress(input_file, start + flood_pos)
        clear_progress(tmp)
    else:
        save_progress(input_file, start + len(batch))
    try:
        os.remove(tmp)
    except Exception:
        pass
    return result

# ─── Wave-based safe add ──────────────────────────────────────────────────────
WAVE_STATE_FILE = 'wave_progress.json'

def _save_wave_state(state):
    with open(WAVE_STATE_FILE, 'w') as f:
        json.dump(state, f, indent=2)

def _load_wave_state():
    if os.path.exists(WAVE_STATE_FILE):
        with open(WAVE_STATE_FILE) as f:
            return json.load(f)
    return None

def _clear_wave_state():
    if os.path.exists(WAVE_STATE_FILE):
        os.remove(WAVE_STATE_FILE)

def wave_add(target_group, input_file='members.csv', total=20,
             per_wave=5, hours_between=5, silent=True):
    """
    Ajout par vagues automatiques avec longues pauses entre chaque.
    Chaque vague ajoute `per_wave` membres avec timer 45-75s entre chaque.
    Attend `hours_between` heures entre les vagues.
    Sauvegarde la progression — reprend automatiquement si interrompu.
    """
    users = read_members_csv(input_file)
    if not users:
        return f"❌ {input_file} vide ou introuvable.\n"
    if not config['accounts']:
        return "❌ Aucun compte configuré.\n"
    active_accounts = [a for a in config['accounts'] if not a['blacklisted']]
    if not active_accounts:
        return "❌ Tous les comptes sont blacklistés.\n"

    # Resume or start fresh
    state = _load_wave_state()
    if state and state.get('group_id') == target_group.id and state.get('input_file') == input_file:
        offset = state['offset']
        wave_num = state['wave']
        print(f"⏩ Reprise: vague {wave_num}, position {offset}")
    else:
        offset = 0
        wave_num = 1
        state = {
            'group_id': target_group.id,
            'group_title': target_group.title,
            'input_file': input_file,
            'offset': 0,
            'wave': 1,
            'total_target': total,
            'per_wave': per_wave,
        }

    total = min(total, len(users) - offset)
    num_waves = -(-total // per_wave)  # ceil division
    est_hours = (num_waves - 1) * hours_between + (num_waves * per_wave * 60 / 3600)

    target_entity = InputPeerChannel(target_group.id, target_group.access_hash)
    grand_stats = {'added': 0, 'already': 0, 'privacy': 0, 'error': 0, 'flood_stop': False}

    print(f"\n🌊 AJOUT PAR VAGUES")
    print(f"   Cible     : {target_group.title}")
    print(f"   Total     : {total} membres")
    print(f"   Vagues    : {num_waves} x {per_wave} membres")
    print(f"   Pause     : {hours_between}h entre chaque vague")
    print(f"   Durée est.: ~{est_hours:.1f}h")
    if silent:
        print(f"   🔇 Mode silencieux activé")
    print()

    remaining = total
    while remaining > 0 and not grand_stats['flood_stop']:
        batch_size = min(per_wave, remaining)
        batch = users[offset:offset + batch_size]
        if not batch:
            break

        print(f"━━━ Vague {wave_num} — {len(batch)} membres (position {offset+1}-{offset+len(batch)}) ━━━\n")

        # Connect
        client = None
        account = None
        for a in active_accounts:
            try:
                client = get_client(a)
                account = a
                break
            except Exception as e:
                print(f"  ⚠️ Connexion impossible ({a['phone']}): {e}")

        if not client:
            print("❌ Impossible de connecter un compte.")
            break

        wave_added = 0
        try:
            for j, user in enumerate(batch, 1):
                name_display = user['name'] or user['username'] or str(user['id'])
                print(f"  [{j}/{len(batch)}] ➕ {name_display}...", end=" ", flush=True)

                try:
                    client(InviteToChannelRequest(target_entity,
                           [InputPeerUser(user['id'], user['access_hash'])]))
                    grand_stats['added'] += 1
                    wave_added += 1
                    record_add(user['id'], user['username'], user['name'],
                               target_group.title, account['phone'], 'success')
                    if silent:
                        time.sleep(1.5)
                        _delete_join_messages(client, target_group)
                        print("✅ (silencieux)")
                    else:
                        print("✅")

                except UserAlreadyParticipantError:
                    grand_stats['already'] += 1
                    print("ℹ️  déjà membre")
                except UserPrivacyRestrictedError:
                    grand_stats['privacy'] += 1
                    print("🔒 privacy")
                except FloodWaitError as e:
                    if e.seconds > 600:
                        print(f"\n  🛑 FloodWait {e.seconds}s — arrêt sécurité")
                        grand_stats['flood_stop'] = True
                        break
                    print(f"⏳ wait {e.seconds}s...")
                    time.sleep(e.seconds + 10)
                except PeerFloodError:
                    print(f"\n  🛑 PeerFlood — arrêt sécurité")
                    grand_stats['flood_stop'] = True
                    break
                except Exception as e:
                    grand_stats['error'] += 1
                    print(f"⚠️  {str(e)[:60]}")

                # Timer intra-vague: 45-75s
                if j < len(batch) and not grand_stats['flood_stop']:
                    wait = random.uniform(45, 75)
                    print(f"      ⏱️  {wait:.0f}s...", flush=True)
                    time.sleep(wait)

        finally:
            try:
                client.disconnect()
            except Exception:
                pass

        offset += len(batch)
        remaining -= len(batch)
        wave_num += 1

        # Save progress
        state['offset'] = offset
        state['wave'] = wave_num
        _save_wave_state(state)

        print(f"\n  📊 Vague terminée: +{wave_added} ajoutés"
              f" (total: {grand_stats['added']})")

        # Pause between waves (unless last wave or flood stop)
        if remaining > 0 and not grand_stats['flood_stop']:
            resume_time = time.strftime('%H:%M', time.localtime(time.time() + hours_between * 3600))
            print(f"\n  💤 Pause de {hours_between}h — prochaine vague à ~{resume_time}")
            print(f"     (Ctrl+C pour interrompre — la progression est sauvée)\n")
            try:
                time.sleep(hours_between * 3600)
            except KeyboardInterrupt:
                print(f"\n  ↩ Interrompu. Progression sauvée (reprise auto au prochain lancement).")
                log(f"Wave add interrompu à la vague {wave_num}, offset {offset}")
                return (f"⏸ Interrompu.\n"
                        f"📊 ✅{grand_stats['added']} ℹ️{grand_stats['already']}"
                        f" 🔒{grand_stats['privacy']} ❌{grand_stats['error']}\n"
                        f"   Relancez l'option pour reprendre automatiquement.\n")

    # Done
    _clear_wave_state()
    log(f"Wave add terminé dans '{target_group.title}': {grand_stats}")

    status = "🛑 Stoppé (flood)" if grand_stats['flood_stop'] else "✅ Terminé"
    return (f"{status}\n"
            f"📊 Rapport final:\n"
            f"  ✅ Ajoutés     : {grand_stats['added']}\n"
            f"  ℹ️  Déjà membres: {grand_stats['already']}\n"
            f"  🔒 Privacy     : {grand_stats['privacy']}\n"
            f"  ❌ Erreurs     : {grand_stats['error']}\n")


# ─── Group management ─────────────────────────────────────────────────────────
def get_group_info(client, group):
    try:
        full = client(GetFullChannelRequest(group))
        fc   = full.full_chat
        resp  = f"📋 Groupe: '{group.title}'\n"
        resp += f"  ID          : {group.id}\n"
        resp += f"  Username    : @{group.username}\n" if getattr(group, 'username', None) else "  Username    : (privé)\n"
        resp += f"  Membres     : {fc.participants_count or 'N/A'}\n"
        resp += f"  Admins      : {fc.admins_count or 'N/A'}\n"
        resp += f"  Bannis      : {getattr(fc, 'kicked_count', None) or 0}\n"
        resp += f"  Restreints  : {getattr(fc, 'restricted_count', None) or 0}\n"
        resp += f"  Description : {fc.about or '(aucune)'}\n"
        return resp
    except Exception as e:
        return f"❌ Erreur: {e}\n"

def generate_invite_link(client, group):
    try:
        result = client(ExportChatInviteRequest(group))
        link = result.link
        log(f"Lien généré pour '{group.title}': {link}")
        return f"🔗 Lien d'invitation: {link}\n"
    except Exception as e:
        return f"❌ Erreur: {e}\n"

def list_admins(client, group):
    try:
        result = client(GetParticipantsRequest(
            InputChannel(group.id, group.access_hash),
            ChannelParticipantsAdmins(), offset=0, limit=200, hash=0))
        resp = f"👑 {len(result.users)} admin(s) dans '{group.title}':\n"
        for u in result.users:
            resp += f"  @{u.username or '—'} — {u.first_name or ''} {u.last_name or ''} (ID: {u.id})\n"
        return resp
    except Exception as e:
        return f"❌ Erreur: {e}\n"

def edit_group_title(client, group):
    new_title = input(f"Nouveau titre (actuel: '{group.title}'): ").strip()
    if not new_title:
        return "❌ Titre vide.\n"
    try:
        client(EditTitleRequest(InputPeerChannel(group.id, group.access_hash), new_title))
        log(f"Titre '{group.title}' → '{new_title}'")
        return f"✅ Titre changé en '{new_title}'.\n"
    except Exception as e:
        return f"❌ Erreur: {e}\n"

def edit_group_description(client, group):
    try:
        new_desc = input("Nouvelle description: ").strip()
        client(EditChatAboutRequest(InputPeerChannel(group.id, group.access_hash), new_desc))
        log(f"Description du groupe '{group.title}' modifiée")
        return "✅ Description mise à jour.\n"
    except Exception as e:
        return f"❌ Erreur: {e}\n"

def change_group_photo(client, group):
    path = input("Chemin de la photo (.jpg/.png): ").strip()
    if not os.path.exists(path):
        return f"❌ Fichier '{path}' introuvable.\n"
    try:
        uploaded = client.upload_file(path)
        client(EditPhotoRequest(InputPeerChannel(group.id, group.access_hash),
                                InputChatUploadedPhoto(uploaded)))
        log(f"Photo du groupe '{group.title}' changée")
        return "✅ Photo mise à jour.\n"
    except Exception as e:
        return f"❌ Erreur: {e}\n"

def ban_member(client, group):
    user_id_str = input("ID ou @username du membre à bannir: ").strip()
    try:
        user = client.get_entity(user_id_str)
        client.edit_permissions(group, user, view_messages=False)
        log(f"Membre {user_id_str} banni de '{group.title}'")
        return f"✅ {user_id_str} banni.\n"
    except Exception as e:
        return f"❌ Erreur: {e}\n"

def unban_member(client, group):
    user_id_str = input("ID ou @username à débannir: ").strip()
    try:
        user = client.get_entity(user_id_str)
        client.edit_permissions(group, user, view_messages=True)
        log(f"Membre {user_id_str} débanni de '{group.title}'")
        return f"✅ {user_id_str} débanni.\n"
    except Exception as e:
        return f"❌ Erreur: {e}\n"

def promote_admin(client, group):
    user_id_str = input("ID ou @username à promouvoir: ").strip()
    try:
        user = client.get_entity(user_id_str)
        rights = ChatAdminRights(
            post_messages=True, edit_messages=True, delete_messages=True,
            ban_users=True, invite_users=True, pin_messages=True,
            add_admins=False, anonymous=False, manage_call=True,
            other=True, manage_topics=False
        )
        client(EditAdminRequest(InputPeerChannel(group.id, group.access_hash), user, rights, rank="Admin"))
        log(f"Membre {user_id_str} promu admin dans '{group.title}'")
        return f"✅ {user_id_str} promu admin.\n"
    except Exception as e:
        return f"❌ Erreur: {e}\n"

def check_group_restrictions(client, group):
    try:
        full = client(GetFullChannelRequest(group))
        fc = full.full_chat
        restricted = getattr(fc, 'restricted_count', 0) or 0
        banned = getattr(fc, 'kicked_count', 0) or 0
        slowmode = getattr(fc, 'slowmode_seconds', 0) or 0
        issues = []
        if restricted > 0:
            issues.append(f"{restricted} restreints")
        if banned > 0:
            issues.append(f"{banned} bannis")
        if slowmode > 0:
            issues.append(f"slowmode {slowmode}s")
        if issues:
            return f"🟡 Restrictions: {', '.join(issues)}.\n"
        return "🟢 Groupe sans restrictions.\n"
    except Exception as e:
        return f"❌ Erreur: {e}\n"

# ─── Migration workflow ───────────────────────────────────────────────────────
def migration_workflow():
    print(Fore.CYAN + "\n🚀 Workflow de migration complet\n" + Style.RESET_ALL)
    account = get_active_account()
    if not account:
        return "❌ Aucun compte actif.\n"
    client = None
    try:
        client = get_client(account)
        print("📤 Étape 1/4 — Groupe SOURCE")
        src_id = input("  Nom/ID/lien: ").strip()
        source_group, err = get_group_by_name(client, src_id)
        if err: return err

        print(f"\n🔍 Étape 2/4 — Scraping de '{source_group.title}'...")
        print(scrape_members(client, source_group))

        print("📥 Étape 3/4 — Groupe DESTINATION")
        dst_id = input("  Nom/ID/lien: ").strip()
        dest_group, err = get_group_by_name(client, dst_id)
        if err: return err

        print(f"\n➕ Étape 4/4 — Ajout dans '{dest_group.title}'...")
        mode   = input("Mode: normal/turbo: ").strip().lower()
        result = add_members(dest_group, mode=mode)
        print(result)

        if input("\n🔗 Générer un lien d'invitation? (o/n): ").lower() == 'o':
            print(generate_invite_link(client, dest_group))

        if input("📢 Poster une annonce dans le groupe source? (o/n): ").lower() == 'o':
            msg = input("Message (laissez vide pour un message par défaut): ").strip()
            if not msg:
                msg = "⚠️ Ce groupe migre vers un nouveau groupe pour des raisons de sécurité. Merci de rejoindre le nouveau groupe."
            print(post_announcement(client, source_group, msg))

        if input("📨 Envoyer un DM de migration à tous les membres? (o/n): ").lower() == 'o':
            print(send_migration_dm())

        log(f"Migration complète: '{source_group.title}' → '{dest_group.title}'")
        return "✅ Migration terminée.\n"
    finally:
        if client:
            try: client.disconnect()
            except Exception: pass

def post_announcement(client, group, message=None):
    if message is None:
        message = input("📢 Message à poster: ").strip()
    try:
        client.send_message(group, message)
        log(f"Annonce postée dans '{group.title}'")
        return f"✅ Annonce postée dans '{group.title}'.\n"
    except Exception as e:
        return f"❌ Erreur: {e}\n"

def send_migration_dm(input_file='members.csv'):
    users = read_members_csv(input_file)
    if not users:
        return "❌ Aucun membre dans le fichier.\n"
    message = input("📨 Message DM (incluez le lien du nouveau groupe): ").strip()
    mode    = input("Mode: normal/rapid: ").strip().lower()
    active  = [a for a in config['accounts'] if not a['blacklisted']]
    if not active:
        return "❌ Aucun compte actif.\n"

    connected = []
    for a in active:
        try:
            connected.append({'phone': a['phone'], 'client': get_client(a)})
        except Exception as e:
            log(f"Connexion impossible ({a['phone']}): {e}", 'warning')
    if not connected:
        return "❌ Impossible de connecter un compte actif.\n"

    account_index = 0
    sent = failed = 0
    try:
        for user in progress_iter(users, "📨 Envoi DMs"):
            entry         = connected[account_index]
            current_phone = entry['phone']
            c             = entry['client']
            account_index = (account_index + 1) % len(connected)
            try:
                c.send_message(InputPeerUser(user['id'], user['access_hash']), message)
                sent += 1
                time.sleep(random.uniform(5, 9) if mode == "rapid" else random.uniform(15, 25))
            except PeerFloodError:
                log(f"PeerFloodError DM sur {current_phone}", 'warning')
                return f"🚫 Flood. {sent} DMs envoyés.\n"
            except FloodWaitError as e:
                time.sleep(e.seconds)
            except Exception as e:
                log(f"Erreur DM user {user['id']} via {current_phone}: {e}", 'warning')
                failed += 1
    finally:
        for entry in connected:
            try: entry['client'].disconnect()
            except Exception: pass

    log(f"DM migration: {sent} envoyés, {failed} échoués")
    return f"✅ DMs: {sent} envoyés, {failed} échecs.\n"

def track_migration(client, source_csv='members.csv'):
    dst_id = input("ID/lien du groupe destination: ").strip()
    dest_group, err = get_group_by_name(client, dst_id)
    if err: return err
    stop = loading_animation("🔍 Analyse migration...")
    try:
        dest_ids     = {u.id for u in client.get_participants(dest_group, aggressive=True)}
        source_users = read_members_csv(source_csv)
        if not source_users:
            return "❌ Fichier source vide.\n"
        joined     = [u for u in source_users if u['id'] in dest_ids]
        not_joined = [u for u in source_users if u['id'] not in dest_ids]
        pct = len(joined) * 100 // len(source_users)
        resp  = f"📊 Migration vers '{dest_group.title}':\n"
        resp += f"  ✅ Ont rejoint  : {len(joined)} ({pct}%)\n"
        resp += f"  ⏳ Pas encore   : {len(not_joined)}\n"
        if not_joined and input("Sauvegarder les non-migrés dans 'pending_migration.csv'? (o/n): ").lower() == 'o':
            write_members_csv(not_joined, 'pending_migration.csv')
            resp += "  💾 Sauvegardé dans pending_migration.csv\n"
        return resp
    except Exception as e:
        return f"❌ Erreur: {e}\n"
    finally:
        stop.set()

# ─── Messages ─────────────────────────────────────────────────────────────────
def clone_group_messages(client, source_group):
    stop = loading_animation("🔍 Clonage des messages...")
    try:
        messages  = []
        offset_id = 0
        while True:
            history = client(GetHistoryRequest(peer=source_group, offset_id=offset_id,
                             offset_date=None, add_offset=0, limit=100,
                             max_id=0, min_id=0, hash=0))
            if not history.messages:
                break
            messages.extend(history.messages)
            offset_id = min(m.id for m in history.messages)
        with open("cloned_messages.csv", "w", encoding='UTF-8') as f:
            writer = csv.writer(f, delimiter=",", lineterminator="\n")
            writer.writerow(['message_id', 'from_id', 'message', 'date'])
            for msg in messages:
                writer.writerow([msg.id, msg.from_id, msg.message or '', msg.date])
        log(f"{len(messages)} messages clonés depuis '{source_group.title}'")
        return f'✅ {len(messages)} messages clonés dans cloned_messages.csv.\n'
    except Exception as e:
        return f"❌ Erreur: {e}\n"
    finally:
        stop.set()

def send_cloned_messages(target_group, input_file='cloned_messages.csv', mode="normal"):
    if not os.path.exists(input_file):
        return f"❌ {input_file} introuvable.\n"
    active = [a for a in config['accounts'] if not a['blacklisted']]
    if not active:
        return "❌ Aucun compte actif.\n"
    with open(input_file, encoding='UTF-8') as f:
        rows = list(csv.reader(f, delimiter=",", lineterminator="\n"))[1:]

    connected = []
    for a in active:
        try:
            connected.append({'phone': a['phone'], 'client': get_client(a)})
        except Exception as e:
            log(f"Connexion impossible ({a['phone']}): {e}", 'warning')
    if not connected:
        return "❌ Impossible de connecter un compte actif.\n"

    account_index = sent = 0
    try:
        for row in rows:
            if len(row) < 3 or not row[2]:
                continue
            entry         = connected[account_index]
            current_phone = entry['phone']
            client        = entry['client']
            account_index = (account_index + 1) % len(connected)
            try:
                client.send_message(target_group, row[2])
                sent += 1
                print(f'✅ Message envoyé via {current_phone}')
                time.sleep(random.uniform(5, 9) if mode == "rapid" else random.uniform(15, 25))
            except Exception as e:
                log(f"Erreur envoi message via {current_phone}: {e}", 'warning')
                print(f"❌ {e}")
    finally:
        for entry in connected:
            try: entry['client'].disconnect()
            except Exception: pass

    return f"✅ {sent} messages envoyés.\n"

def display_cloned_messages():
    if not os.path.exists("cloned_messages.csv"):
        return "❌ Aucun message cloné.\n"
    with open("cloned_messages.csv", encoding='UTF-8') as f:
        rows = list(csv.reader(f, delimiter=",", lineterminator="\n"))[1:]
    if not rows:
        return "📋 Aucun message.\n"
    resp = ""
    for r in rows:
        txt  = r[2] if len(r) > 2 else ''
        resp += f"  [{r[3][:10] if len(r) > 3 else '?'}] {txt[:80]}{'...' if len(txt) > 80 else ''}\n"
    resp += f"\n📊 Total: {len(rows)} messages.\n"
    return resp

def edit_cloned_messages():
    if not os.path.exists("cloned_messages.csv"):
        return "❌ Aucun message cloné.\n"
    with open("cloned_messages.csv", encoding='UTF-8') as f:
        rows = list(csv.reader(f, delimiter=",", lineterminator="\n"))
    if not rows:
        return "❌ Aucun message cloné.\n"
    header, messages = rows[0], rows[1:]
    if not messages:
        return "📋 Aucun message.\n"
    for i, r in enumerate(messages):
        print(f"  {i+1}. {r[2][:80] if len(r) > 2 else ''}")
    idx = safe_int_input("Numéro à éditer: ", min_val=1, max_val=len(messages))
    if idx is None:
        return "❌ Numéro invalide.\n"
    if len(messages[idx - 1]) < 3:
        return "❌ Ligne malformée, impossible d'éditer.\n"
    messages[idx - 1][2] = input("Nouveau contenu: ")
    with open("cloned_messages.csv", "w", encoding='UTF-8') as f:
        writer = csv.writer(f, delimiter=",", lineterminator="\n")
        writer.writerow(header)
        writer.writerows(messages)
    return "✅ Message édité.\n"

def delete_cloned_messages():
    if os.path.exists("cloned_messages.csv"):
        os.remove("cloned_messages.csv")
        return "🗑️ Messages clonés supprimés.\n"
    return "❌ Aucun message à supprimer.\n"

def search_cloned_messages():
    if not os.path.exists("cloned_messages.csv"):
        return "❌ Aucun message cloné.\n"
    query = input("🔍 Mot-clé: ").strip().lower()
    with open("cloned_messages.csv", encoding='UTF-8') as f:
        rows = list(csv.reader(f, delimiter=",", lineterminator="\n"))[1:]
    results = [r for r in rows if len(r) > 2 and query in r[2].lower()]
    if not results:
        return f"❌ Aucun résultat pour '{query}'.\n"
    resp = f"🔍 {len(results)} résultat(s):\n"
    for r in results:
        resp += f"  [{r[3][:10] if len(r) > 3 else '?'}] {r[2][:100]}\n"
    return resp

def forward_messages(client, source_group, target_group):
    stop = loading_animation("↩️ Chargement des messages...")
    try:
        messages = list(client.iter_messages(source_group))
        stop.set()
        num = safe_int_input(f"Nombre de messages à forwarder (max {len(messages)}): ",
                             min_val=1, max_val=len(messages))
        if num is None:
            return "❌ Nombre invalide.\n"
        forwarded = 0
        for msg in progress_iter(messages[:num], "↩️ Forward"):
            try:
                client.forward_messages(target_group, msg.id, source_group)
                forwarded += 1
                time.sleep(random.uniform(2, 5))
            except Exception as e:
                print(f"  ⚠️ {e}")
        log(f"Forward: {forwarded} messages de '{source_group.title}' vers '{target_group.title}'")
        return f"✅ {forwarded} messages forwardés.\n"
    except Exception as e:
        stop.set()
        return f"❌ Erreur: {e}\n"

def download_media(client, group):
    output_dir = input("Dossier de téléchargement (défaut: ./media): ").strip() or "media"
    os.makedirs(output_dir, exist_ok=True)
    stop = loading_animation("📥 Téléchargement des médias...")
    try:
        downloaded = 0
        for msg in client.iter_messages(group):
            if msg.media:
                try:
                    path = client.download_media(msg, file=output_dir)
                    if path:
                        downloaded += 1
                except Exception as e:
                    log(f"Erreur téléchargement média msg {msg.id}: {e}", 'warning')
        log(f"{downloaded} médias téléchargés depuis '{group.title}'")
        return f"✅ {downloaded} médias dans '{output_dir}'.\n"
    except Exception as e:
        return f"❌ Erreur: {e}\n"
    finally:
        stop.set()

def schedule_mass_message(input_file='members.csv', message="Hello!", mode="normal"):
    users = read_members_csv(input_file)
    if not users:
        return f"❌ {input_file} vide.\n"
    active = [a for a in config['accounts'] if not a['blacklisted']]
    if not active:
        return "❌ Aucun compte actif.\n"

    connected = []
    for a in active:
        try:
            connected.append({'phone': a['phone'], 'client': get_client(a)})
        except Exception as e:
            log(f"Connexion impossible ({a['phone']}): {e}", 'warning')
    if not connected:
        return "❌ Impossible de connecter un compte actif.\n"

    account_index = sent = failed = 0
    try:
        for user in progress_iter(users, "📨 Envoi"):
            entry         = connected[account_index]
            current_phone = entry['phone']
            client        = entry['client']
            account_index = (account_index + 1) % len(connected)
            try:
                client.send_message(InputPeerUser(user['id'], user['access_hash']), message)
                sent += 1
                time.sleep(random.uniform(5, 9) if mode == "rapid" else random.uniform(15, 25))
            except PeerFloodError:
                log(f"PeerFloodError envoi masse via {current_phone}", 'warning')
                return f"🚫 Flood. {sent} envoyés.\n"
            except FloodWaitError as e:
                time.sleep(e.seconds)
            except Exception as e:
                log(f"Erreur envoi masse user {user['id']} via {current_phone}: {e}", 'warning')
                failed += 1
    finally:
        for entry in connected:
            try: entry['client'].disconnect()
            except Exception: pass

    log(f"Envoi masse: {sent} envoyés, {failed} échecs")
    return f"✅ {sent} messages envoyés, {failed} échecs.\n"

def send_notification_to_self(client, message=None):
    if message is None:
        message = input("Message à s'envoyer (Saved Messages): ").strip()
    try:
        client.send_message('me', f"🤖 Notification:\n{message}")
        return "✅ Notification envoyée dans Saved Messages.\n"
    except Exception as e:
        return f"❌ Erreur: {e}\n"

# ─── CSV file management ──────────────────────────────────────────────────────
def delete_saved_users():
    if os.path.exists("members.csv"):
        os.remove("members.csv")
    return '🗑️ members.csv supprimé.\n'

def display_saved_users():
    users = read_members_csv()
    if not users:
        return "📋 Aucun utilisateur enregistré.\n"
    resp = ""
    for u in users:
        resp += f"  👤 {u['name'] or '(sans nom)'} (@{u['username'] or '—'}) ID:{u['id']}\n"
    resp += f"\n📊 Total: {len(users)} utilisateurs.\n"
    return resp

def save_scrapped_members_as():
    name = input("Nom du fichier (sans .csv): ").strip()
    if not name:
        return "❌ Nom vide.\n"
    if not os.path.exists("members.csv"):
        return "❌ Aucune donnée à sauvegarder.\n"
    out = name + '.csv'
    shutil.copy("members.csv", out)
    return f"✅ Sauvegardé dans {out}.\n"

def save_scrapped_members_append_or_overwrite():
    name = input("Nom du fichier (sans .csv): ").strip() + '.csv'
    if os.path.exists(name):
        choice = input(f"'{name}' existe. (a)jouter / (é)craser: ").strip().lower()
        if choice in ('a', 'ajouter'):
            if not os.path.exists("members.csv"):
                return "❌ Aucune donnée.\n"
            existing = read_members_csv(name)
            new      = read_members_csv()
            seen     = {u['id'] for u in existing}
            merged   = existing + [u for u in new if u['id'] not in seen]
            write_members_csv(merged, name)
            return f"✅ {len(merged) - len(existing)} membres ajoutés à {name}.\n"
        elif choice in ('e', 'é', 'ecraser', 'écraser'):
            shutil.copy("members.csv", name)
            return f"✅ {name} écrasé.\n"
        return "❌ Choix invalide.\n"
    shutil.copy("members.csv", name)
    return f"✅ Sauvegardé dans {name}.\n"

def manage_backup_files():
    backup_files = sorted([f for f in os.listdir() if f.endswith('.csv') and f != 'members.csv'])
    if not backup_files:
        return "❌ Aucun fichier de sauvegarde.\n"
    for i, f in enumerate(backup_files):
        count = len(read_members_csv(f))
        size  = os.path.getsize(f)
        print(f"  {i+1}. {f} ({count} membres, {size} octets)")
    choice = input("(v)oir  (s)upprimer  (u)tiliser comme members.csv  (q)uitter: ").strip().lower()
    if choice == 'v':
        idx = safe_int_input("Numéro: ", min_val=1, max_val=len(backup_files))
        if idx is None: return "❌ Invalide.\n"
        users = read_members_csv(backup_files[idx - 1])
        return "".join(f"  👤 {u['name']} (@{u['username'] or '—'})\n" for u in users) + f"📊 {len(users)} membres.\n"
    elif choice == 's':
        idx = safe_int_input("Numéro: ", min_val=1, max_val=len(backup_files))
        if idx is None: return "❌ Invalide.\n"
        fname = backup_files[idx - 1]
        os.remove(fname)
        return f"🗑️ {fname} supprimé.\n"
    elif choice == 'u':
        idx = safe_int_input("Numéro: ", min_val=1, max_val=len(backup_files))
        if idx is None: return "❌ Invalide.\n"
        shutil.copy(backup_files[idx - 1], 'members.csv')
        return f"✅ {backup_files[idx - 1]} copié vers members.csv.\n"
    return ""

# ─── Logs and reports ─────────────────────────────────────────────────────────
def show_activity_log():
    if not os.path.exists(LOG_FILE):
        return "📋 Aucun log disponible.\n"
    with open(LOG_FILE, encoding='UTF-8') as f:
        lines = f.readlines()
    if not lines:
        return "📋 Log vide.\n"
    num = safe_int_input(f"Afficher les N dernières lignes (max {len(lines)}, défaut 30): ",
                         min_val=1, max_val=len(lines)) or 30
    return "".join(lines[-num:])

def show_add_history():
    try:
        conn = sqlite3.connect(HISTORY_DB)
        c    = conn.cursor()
        c.execute("SELECT timestamp, username, name, target_group, account_phone, status FROM add_history ORDER BY id DESC LIMIT 50")
        rows = c.fetchall()
        conn.close()
        if not rows:
            return "📋 Aucun historique.\n"
        resp = "📊 50 derniers ajouts:\n"
        for ts, username, name, group, phone, status in rows:
            emoji = {"success": "✅", "already": "ℹ️", "privacy": "🔒"}.get(status, "❌")
            label = name or username or "?"
            resp += f"  {emoji} {ts[:16]} | {label:<20} → {group} via {phone}\n"
        return resp
    except Exception as e:
        return f"❌ Erreur: {e}\n"

def generate_html_report():
    try:
        conn = sqlite3.connect(HISTORY_DB)
        c    = conn.cursor()
        c.execute("SELECT status, COUNT(*) FROM add_history GROUP BY status")
        stats  = dict(c.fetchall())
        c.execute("SELECT source_group, member_count, timestamp FROM scrape_history ORDER BY id DESC LIMIT 10")
        scrapes = c.fetchall()
        conn.close()
        total = sum(stats.values())
        rows_html = "".join(
            f"<tr><td>{s}</td><td>{n}</td><td>{n*100//total if total else 0}%</td></tr>"
            for s, n in stats.items()
        )
        scrapes_html = "".join(
            f"<tr><td>{g}</td><td>{n}</td><td>{t[:16]}</td></tr>"
            for g, n, t in scrapes
        )
        html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Rapport Telegram Manager</title>
<style>
  body{{font-family:sans-serif;padding:20px;background:#f9f9f9;}}
  h1{{color:#2c3e50;}} h2{{color:#34495e;border-bottom:1px solid #ddd;padding-bottom:5px;}}
  table{{border-collapse:collapse;width:100%;margin-bottom:20px;background:white;}}
  td,th{{border:1px solid #ddd;padding:10px;text-align:left;}}
  th{{background:#3498db;color:white;}}
  tr:nth-child(even){{background:#f2f2f2;}}
</style></head><body>
<h1>📊 Rapport Telegram Manager</h1>
<p>Généré le {datetime.now().strftime('%d/%m/%Y à %H:%M')}</p>
<h2>Statistiques des ajouts</h2>
<table><tr><th>Statut</th><th>Nombre</th><th>Pourcentage</th></tr>
{rows_html}
<tr><td><b>Total</b></td><td><b>{total}</b></td><td>100%</td></tr></table>
<h2>Derniers scrapings</h2>
<table><tr><th>Groupe</th><th>Membres</th><th>Date</th></tr>
{scrapes_html}
</table></body></html>"""
        out = f"rapport_{datetime.now().strftime('%Y%m%d_%H%M')}.html"
        with open(out, 'w', encoding='UTF-8') as f:
            f.write(html)
        log(f"Rapport HTML généré: {out}")
        return f"✅ Rapport généré: {out}\n"
    except Exception as e:
        return f"❌ Erreur: {e}\n"

def clear_cache():
    configured_phones = {a['phone'] for a in config['accounts']}
    cache_files = [f for f in os.listdir()
                   if f.endswith('.session') and not any(p in f for p in configured_phones)]
    if not cache_files:
        return "🗑️ Aucun cache orphelin à supprimer.\n"
    for f in cache_files:
        os.remove(f)
    return f"🗑️ {len(cache_files)} fichier(s) de cache supprimé(s).\n"

# ─── Helper: get active client, ensure disconnect after use ───────────────────
def _active_client():
    """Return (account, client) for a random active account, or (None, None)."""
    account = get_active_account()
    if not account:
        return None, None
    try:
        return account, get_client(account)
    except Exception as e:
        log(f"Impossible de connecter {account['phone']}: {e}", 'warning')
        print(f"❌ Impossible de connecter {account['phone']}: {e}")
        return None, None

def _disconnect(client):
    if client:
        try: client.disconnect()
        except Exception: pass

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

    acct_str = f"  👤 {active}/{total} actifs"
    if blacked:
        acct_str += f"  {Y}⏸ {blacked} blacklist{rst}"
    if expired:
        acct_str += f"  {R}⚠ {expired} expirés{rst}"
    print(G + "║" + rst + acct_str)

    mem_c = Fore.GREEN if members > 0 else Fore.YELLOW
    prx_c = Fore.GREEN if proxies > 0 else Fore.WHITE
    print(G + "║" + rst +
          f"  👥 membres: {mem_c}{members}{rst}"
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
    print(f"  {C}🚀 MIGRATION    {rst}30-34")
    print(f"  {G}👤 COMPTES      {rst}8-11, 17, 19, 26, 29, 35, 36")
    print(f"  {B}🌐 PROXIES      {rst}12-14, 18, 37, 38")
    print(f"  {Y}👥 MEMBRES      {rst}1-5, 39-48, 63")
    print(f"  {M}🏠 GROUPES      {rst}49-56")
    print(f"  {M}📁 CSV          {rst}6, 7, 16")
    print(f"  {LG}💬 MESSAGES     {rst}20-24, 27, 28, 57-60")
    print(f"  {R}📊 LOGS         {rst}61, 62, 25")
    print()
    print(f"  {W}[?]{rst} menu détaillé   {W}[15]{rst} quitter")


def _full_menu():
    G, C, B, Y, M, LG, R, rst = (
        Fore.GREEN, Fore.CYAN, Fore.BLUE, Fore.YELLOW,
        Fore.MAGENTA, Fore.LIGHTGREEN_EX, Fore.RED, Style.RESET_ALL)
    print(C + "\n🚀 MIGRATION" + rst)
    print("  30 - 🚀 Workflow migration complet (tout-en-un)")
    print("  31 - 📢 Poster une annonce dans un groupe")
    print("  32 - 📨 DM de migration à tous les membres")
    print("  33 - 📊 Suivi de la migration")
    print("  34 - 🔗 Générer un lien d'invitation")

    print(G + "\n👤 COMPTES" + rst)
    print("   8 - 🔌 Connecter un nouveau compte")
    print("   9 - 📋 Liste des comptes")
    print("  10 - 🗑️  Supprimer un compte")
    print("  11 - ⏸️  Blacklister un compte (48h)")
    print("  17 - 🔍 Vérifier un compte")
    print("  19 - 🔍 Vérifier tous les comptes")
    print("  26 - 🔄 Reconnecter un compte")
    print("  29 - 🔄 Reconnecter tous les comptes")
    print("  35 - 👤 Profil d'un compte")
    print("  36 - 📊 Stats par compte")

    print(B + "\n🌐 PROXIES" + rst)
    print("  12 - ➕ Ajouter un proxy")
    print("  13 - 🗑️  Supprimer un proxy")
    print("  14 - 📋 Liste des proxies")
    print("  18 - 🧪 Tester un proxy")
    print("  37 - 🧪 Tester tous les proxies")
    print("  38 - 📂 Importer depuis fichier .txt")

    print(Y + "\n👥 MEMBRES" + rst)
    print("   1 - 🕵️  Scraper des membres")
    print("   2 - ➕ Ajouter des membres dans un groupe")
    print("   3 - 🗑️  Supprimer members.csv")
    print("   4 - 👁️  Afficher les membres")
    print("   5 - 🚮 Filtrer inactifs / faux comptes")
    print("  39 - 🔁 Dédupliquer / Fusionner des CSVs")
    print("  40 - 🔍 Rechercher un membre")
    print("  41 - 🔃 Trier les membres")
    print("  42 - 📊 Statistiques des membres")
    print("  43 - ⚖️  Comparer deux CSVs")
    print("  44 - 📤 Exporter en JSON")
    print("  45 - ✅ Vérifier présence dans le groupe")
    print("  46 - 🚮 Retirer (kick) des membres")
    print("  47 - 📅 Ajout planifié (N membres/jour)")
    print("  63 - 🌊 Ajout par vagues (safe, anti-ban)")
    print("  48 - 📊 Historique des ajouts")

    print(M + "\n🏠 GROUPES" + rst)
    print("  49 - 📋 Infos d'un groupe")
    print("  50 - ✏️  Modifier le titre")
    print("  51 - 📝 Modifier la description")
    print("  52 - 🖼️  Changer la photo")
    print("  53 - 👑 Lister les admins")
    print("  54 - 👑 Promouvoir un admin")
    print("  55 - 🚫 Bannir un membre")
    print("  56 - ✅ Débannir un membre")

    print(M + "\n📁 FICHIERS CSV" + rst)
    print("   6 - 💾 Sauvegarder sous un nouveau nom")
    print("   7 - 📂 Gérer les fichiers CSV")
    print("  16 - 💾 Ajouter / Écraser un CSV existant")

    print(LG + "\n💬 MESSAGES" + rst)
    print("  20 - 📋 Cloner les messages d'un groupe")
    print("  21 - 🚀 Envoyer les messages clonés")
    print("  22 - 👁️  Afficher les messages clonés")
    print("  23 - ✏️  Éditer les messages clonés")
    print("  24 - 🗑️  Supprimer les messages clonés")
    print("  27 - 📷 Scraper les images d'un groupe")
    print("  28 - 📨 Envoyer un message en masse")
    print("  57 - 🔍 Rechercher dans les messages clonés")
    print("  58 - ↩️  Forwarder des messages")
    print("  59 - 📥 Télécharger les médias d'un groupe")
    print("  60 - 🔔 S'envoyer une notification")

    print(R + "\n📊 LOGS & RAPPORTS" + rst)
    print("  61 - 📋 Voir les logs d'activité")
    print("  62 - 📊 Générer rapport HTML")
    print("  25 - 🗑️  Vider le cache")
    print("  15 - 🚪 Quitter")


def _pager(text, page_size=22):
    """Affiche un texte long page par page."""
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
                    f"\n  {Style.BRIGHT}── {remaining} lignes restantes ──"
                    f"  [Entrée] suite  [q] quitter{Style.RESET_ALL}: "
                ).strip().lower()
                if key == 'q':
                    break
    except (KeyboardInterrupt, EOFError):
        pass


def _confirm(msg):
    """Demande une confirmation pour une action destructive. Retourne True si confirmé."""
    try:
        ans = input(
            f"{Fore.RED}⚠️  {msg} (o/N): {Style.RESET_ALL}"
        ).strip().lower()
        return ans in ('o', 'oui', 'y', 'yes')
    except (KeyboardInterrupt, EOFError):
        return False


def _startup_session_check():
    """Vérifie toutes les sessions au démarrage. Retourne [(phone, expired), ...]."""
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

    # ── Vérification des sessions au démarrage
    session_status = None
    if config.get('accounts'):
        print(f"{Fore.YELLOW}  Vérification des sessions...{Style.RESET_ALL}", end='\r')
        session_status = _startup_session_check()
        expired_list = [(p, e) for p, e in session_status if e]
        if expired_list:
            _clear_screen()
            print(f"\n{Fore.RED}⚠️  Sessions expirées :{Style.RESET_ALL}")
            for phone, _ in expired_list:
                print(f"   {Fore.RED}●{Style.RESET_ALL} {phone}")
            print(
                f"\n  → Utilisez {Style.BRIGHT}26{Style.RESET_ALL} pour reconnecter un compte"
                f" ou {Style.BRIGHT}29{Style.RESET_ALL} pour tout reconnecter.\n"
            )
            input("  [Entrée] pour continuer...")

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

            # ── Migration
            if choice == '30':
                result = migration_workflow()
            elif choice in ('31', '32', '33', '34'):
                account, client = _active_client()
                if not account: result = "❌ Aucun compte actif.\n"
                else:
                    try:
                        if choice == '31':
                            g, err = get_group_by_name(client, input("Groupe cible: "))
                            result = err if err else post_announcement(client, g)
                        elif choice == '32':
                            result = send_migration_dm()
                        elif choice == '33':
                            result = track_migration(client)
                        elif choice == '34':
                            g, err = get_group_by_name(client, input("Groupe: "))
                            result = err if err else generate_invite_link(client, g)
                    finally:
                        _disconnect(client)

            # ── Comptes
            elif choice == '8':  result = connect_new_account()
            elif choice == '9':  result = list_connected_accounts()
            elif choice == '10':
                if _confirm("Supprimer ce compte définitivement ?"):
                    result = delete_connected_account()
                else:
                    result = "↩ Annulé.\n"
            elif choice == '11':
                if not config['accounts']: result = list_connected_accounts()
                else:
                    print(list_connected_accounts())
                    idx = safe_int_input('Numéro à blacklister: ', min_val=1, max_val=len(config['accounts']))
                    result = blacklist_account(idx - 1) if idx else '❌ Invalide.\n'
            elif choice == '17': result = select_account_and_check_restrictions()
            elif choice == '19': check_all_accounts_restrictions()
            elif choice == '26':
                if not config['accounts']: result = list_connected_accounts()
                else:
                    print(list_connected_accounts())
                    idx = safe_int_input('Numéro à reconnecter: ', min_val=1, max_val=len(config['accounts']))
                    result = reconnect_account(idx - 1) if idx else '❌ Invalide.\n'
            elif choice == '29': result = reconnect_all_accounts()
            elif choice == '35':
                if not config['accounts']: result = "❌ Aucun compte.\n"
                else:
                    print(list_connected_accounts())
                    idx = safe_int_input('Numéro: ', min_val=1, max_val=len(config['accounts']))
                    result = show_account_profile(idx - 1) if idx else '❌ Invalide.\n'
            elif choice == '36': result = show_account_stats()

            # ── Proxies
            elif choice == '12': result = add_proxy()
            elif choice == '13': result = delete_proxy()
            elif choice == '14': result = list_proxies()
            elif choice == '18': result = test_proxy()
            elif choice == '37': result = test_all_proxies()
            elif choice == '38': result = import_proxies_from_file()

            # ── Membres
            elif choice == '1':
                account, client = _active_client()
                if not account: result = "❌ Aucun compte actif.\n"
                else:
                    try:
                        print("1-Groupe actif  2-Par nom/ID/lien  3-Plusieurs groupes  4-Admins  5-Bots")
                        sub = input("Choix: ").strip()
                        if sub == '1':
                            g, err = choose_group_from_active(client)
                            if err: result = err
                            else:
                                online = input("Tous (1) ou en ligne seulement (2)? ").strip() == '2'
                                result = scrape_members(client, g, online_only=online)
                        elif sub == '2':
                            g, err = get_group_by_name(client, input("Nom/ID/lien: "))
                            if err: result = err
                            else:
                                online = input("Tous (1) ou en ligne seulement (2)? ").strip() == '2'
                                result = scrape_members(client, g, online_only=online)
                        elif sub == '3':
                            result = scrape_multiple_groups(client)
                        elif sub == '4':
                            g, err = get_group_by_name(client, input("Groupe: "))
                            result = err if err else scrape_admins(client, g)
                        elif sub == '5':
                            g, err = get_group_by_name(client, input("Groupe: "))
                            result = err if err else scrape_bots(client, g)
                        else:
                            result = "❌ Choix invalide.\n"
                    finally:
                        _disconnect(client)
            elif choice == '2':
                account, client = _active_client()
                if not account: result = "❌ Aucun compte actif.\n"
                else:
                    try:
                        g, err = get_group_by_name(client, input("Groupe cible: "))
                        if err: result = err
                        else:
                            print(check_group_restrictions(client, g))
                            f = input("Fichier CSV (Entrée=members.csv): ").strip() or 'members.csv'
                            if not os.path.exists(f): result = f"❌ {f} introuvable.\n"
                            else:
                                n    = input("Nombre (Entrée=tous): ").strip()
                                mode = input("Mode (safe/normal/turbo): ").strip().lower() or 'normal'
                                sil  = input("Silencieux — cacher les msgs 'a rejoint' (o/N): ").strip().lower() in ('o', 'oui', 'y')
                                result = add_members(g, input_file=f, num_members=n or None, mode=mode, silent=sil)
                    finally:
                        _disconnect(client)
            elif choice == '3':
                if _confirm("Supprimer members.csv ?"):
                    result = delete_saved_users()
                else:
                    result = "↩ Annulé.\n"
            elif choice == '4':  result = display_saved_users()
            elif choice == '5':  result = filter_and_remove_inactive_or_fake()
            elif choice == '39': result = deduplicate_csv()
            elif choice == '40': result = search_member()
            elif choice == '41': result = sort_members()
            elif choice == '42': result = member_stats()
            elif choice == '43': result = compare_csv()
            elif choice == '44':
                f = input("Fichier CSV (Entrée=members.csv): ").strip() or 'members.csv'
                result = export_json(f)
            elif choice == '45':
                account, client = _active_client()
                if not account: result = "❌ Aucun compte actif.\n"
                else:
                    try:
                        g, err = get_group_by_name(client, input("Groupe à vérifier: "))
                        result = err if err else verify_members_present(client, g)
                    finally:
                        _disconnect(client)
            elif choice == '46':
                if not _confirm("Retirer (kick) des membres du groupe ?"):
                    result = "↩ Annulé.\n"
                else:
                    account, client = _active_client()
                    if not account: result = "❌ Aucun compte actif.\n"
                    else:
                        try:
                            g, err = get_group_by_name(client, input("Groupe cible: "))
                            if err: result = err
                            else:
                                f = input("Fichier CSV (Entrée=members.csv): ").strip() or 'members.csv'
                                result = kick_members(client, g, f)
                        finally:
                            _disconnect(client)
            elif choice == '47':
                account, client = _active_client()
                if not account: result = "❌ Aucun compte actif.\n"
                else:
                    try:
                        g, err = get_group_by_name(client, input("Groupe cible: "))
                        if err: result = err
                        else:
                            per_day = safe_int_input("Membres par jour: ", min_val=1) or 50
                            f       = input("Fichier CSV (Entrée=members.csv): ").strip() or 'members.csv'
                            mode    = input("Mode: normal/turbo: ").strip().lower()
                            result  = schedule_daily_add(g, f, per_day, mode)
                    finally:
                        _disconnect(client)
            elif choice == '48': result = show_add_history()
            elif choice == '63':
                account, client = _active_client()
                if not account: result = "❌ Aucun compte actif.\n"
                else:
                    try:
                        g, err = get_group_by_name(client, input("Groupe cible: "))
                        if err: result = err
                        else:
                            f = input("Fichier CSV (Entrée=members.csv): ").strip() or 'members.csv'
                            if not os.path.exists(f): result = f"❌ {f} introuvable.\n"
                            else:
                                total = safe_int_input("Total à ajouter (défaut=20): ", min_val=1) or 20
                                pw = safe_int_input("Par vague (défaut=5): ", min_val=1) or 5
                                hrs = safe_int_input("Heures entre vagues (défaut=5): ", min_val=1) or 5
                                _disconnect(client)  # libère la session pour wave_add
                                client = None
                                result = wave_add(g, input_file=f, total=total,
                                                  per_wave=pw, hours_between=hrs, silent=True)
                    finally:
                        _disconnect(client)

            # ── Groupes
            elif choice in ('49', '50', '51', '52', '53', '54', '55', '56'):
                account, client = _active_client()
                if not account: result = "❌ Aucun compte actif.\n"
                else:
                    try:
                        g, err = get_group_by_name(client, input("Groupe: "))
                        if err: result = err
                        elif choice == '49': result = get_group_info(client, g)
                        elif choice == '50': result = edit_group_title(client, g)
                        elif choice == '51': result = edit_group_description(client, g)
                        elif choice == '52': result = change_group_photo(client, g)
                        elif choice == '53': result = list_admins(client, g)
                        elif choice == '54': result = promote_admin(client, g)
                        elif choice == '55':
                            if _confirm("Bannir ce membre ?"):
                                result = ban_member(client, g)
                            else:
                                result = "↩ Annulé.\n"
                        elif choice == '56': result = unban_member(client, g)
                    finally:
                        _disconnect(client)

            # ── CSV files
            elif choice == '6':  result = save_scrapped_members_as()
            elif choice == '7':  result = manage_backup_files()
            elif choice == '16': result = save_scrapped_members_append_or_overwrite()

            # ── Messages
            elif choice == '20':
                account, client = _active_client()
                if not account: result = "❌ Aucun compte actif.\n"
                else:
                    try:
                        g, err = get_group_by_name(client, input("Groupe source: "))
                        result = err if err else clone_group_messages(client, g)
                    finally:
                        _disconnect(client)
            elif choice == '21':
                account, client = _active_client()
                if not account: result = "❌ Aucun compte actif.\n"
                else:
                    try:
                        g, err = get_group_by_name(client, input("Groupe cible: "))
                        if err: result = err
                        else:
                            f    = input("Fichier (Entrée=cloned_messages.csv): ").strip() or 'cloned_messages.csv'
                            mode = input("Mode: normal/rapid: ").strip().lower()
                            result = send_cloned_messages(g, f, mode)
                    finally:
                        _disconnect(client)
            elif choice == '22': result = display_cloned_messages()
            elif choice == '23': result = edit_cloned_messages()
            elif choice == '24':
                if _confirm("Supprimer tous les messages clonés ?"):
                    result = delete_cloned_messages()
                else:
                    result = "↩ Annulé.\n"
            elif choice == '27':
                account, client = _active_client()
                if not account: result = "❌ Aucun compte actif.\n"
                else:
                    try:
                        g, err = get_group_by_name(client, input("Groupe: "))
                        result = err if err else scrape_images(client, g)
                    finally:
                        _disconnect(client)
            elif choice == '28':
                f      = input("Fichier CSV (Entrée=members.csv): ").strip() or 'members.csv'
                msg    = input("Message: ")
                mode   = input("Mode: normal/rapid: ").strip().lower()
                result = schedule_mass_message(f, msg, mode)
            elif choice == '57': result = search_cloned_messages()
            elif choice == '58':
                account, client = _active_client()
                if not account: result = "❌ Aucun compte actif.\n"
                else:
                    try:
                        src, e1 = get_group_by_name(client, input("Groupe source: "))
                        if e1: result = e1
                        else:
                            dst, e2 = get_group_by_name(client, input("Groupe destination: "))
                            result = e2 if e2 else forward_messages(client, src, dst)
                    finally:
                        _disconnect(client)
            elif choice == '59':
                account, client = _active_client()
                if not account: result = "❌ Aucun compte actif.\n"
                else:
                    try:
                        g, err = get_group_by_name(client, input("Groupe: "))
                        result = err if err else download_media(client, g)
                    finally:
                        _disconnect(client)
            elif choice == '60':
                account, client = _active_client()
                if not account: result = "❌ Aucun compte actif.\n"
                else:
                    try:
                        result = send_notification_to_self(client)
                    finally:
                        _disconnect(client)

            # ── Logs
            elif choice == '61': result = show_activity_log()
            elif choice == '62': result = generate_html_report()

            # ── Misc
            elif choice == '25':
                if _confirm("Vider le cache des sessions orphelines ?"):
                    result = clear_cache()
                else:
                    result = "↩ Annulé.\n"
            elif choice == '15':
                _clear_screen()
                print(f"\n{Fore.GREEN}  🚪 Au revoir !{Style.RESET_ALL}\n")
                break
            else:
                result = (
                    f"{Fore.YELLOW}  ❓ Choix invalide."
                    f" Tapez {Style.BRIGHT}?{Style.RESET_ALL}{Fore.YELLOW}"
                    f" pour le menu complet.{Style.RESET_ALL}\n"
                )

            if result:
                _pager(result)
                last_result = result

        except KeyboardInterrupt:
            print(
                f"\n{Fore.YELLOW}  ↩  Ctrl+C intercepté —"
                f" tapez {Style.BRIGHT}15{Style.RESET_ALL}{Fore.YELLOW}"
                f" pour quitter proprement.{Style.RESET_ALL}"
            )
            time.sleep(1)
            continue


if __name__ == "__main__":
    main()
