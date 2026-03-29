"""
Telegram Manager — Automation Engine
Campaign-based system for automated member addition with multi-account rotation.

Usage:
    python automation.py                  # Interactive menu
    python automation.py run <campaign>   # Run a specific campaign
    python automation.py status           # Show all campaign statuses
    python automation.py list             # List campaigns
"""
import os, sys, json, csv, time, random, asyncio, sqlite3, signal
from datetime import datetime, timedelta
from pathlib import Path

# ── Asyncio fix for Python 3.10+ ─────────────────────────────────────────────
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

from telethon.sync import TelegramClient
from telethon.tl.functions.channels import InviteToChannelRequest, GetFullChannelRequest
from telethon.tl.types import (InputPeerChannel, InputPeerUser, InputPeerEmpty,
                                MessageService, MessageActionChatAddUser)
from telethon.errors.rpcerrorlist import (PeerFloodError, UserPrivacyRestrictedError,
                                           FloodWaitError, UserAlreadyParticipantError)
from colorama import Fore, Style, init as colorama_init

colorama_init()

# ── Paths ─────────────────────────────────────────────────────────────────────
CONFIG_FILE = 'config.json'
CAMPAIGNS_DIR = Path('campaigns')
CAMPAIGNS_DIR.mkdir(exist_ok=True)
AUTOMATION_DB = 'automation.db'
LOG_FILE = 'activity.log'
CSV_HEADER = ['username', 'user_id', 'access_hash', 'name', 'group', 'group_id']

G = Fore.GREEN; R = Fore.RED; Y = Fore.YELLOW; C = Fore.CYAN; B = Style.BRIGHT; RST = Style.RESET_ALL

# ── Config ────────────────────────────────────────────────────────────────────
if os.path.exists(CONFIG_FILE):
    with open(CONFIG_FILE) as f:
        config = json.load(f)
else:
    config = {"accounts": [], "proxies": []}


def save_config():
    with open(CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=4)


def log(msg):
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with open(LOG_FILE, 'a') as f:
        f.write(f"{ts} [AUTOMATION] {msg}\n")


# ═══════════════════════════════════════════════════════════════════════════════
#  DATABASE — campaign state + per-account cooldowns
# ═══════════════════════════════════════════════════════════════════════════════

def init_db():
    conn = sqlite3.connect(AUTOMATION_DB)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS campaigns (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        target_group_id INTEGER,
        target_group_title TEXT,
        target_group_hash INTEGER,
        source_csv TEXT DEFAULT 'members.csv',
        total_target INTEGER DEFAULT 0,
        per_wave INTEGER DEFAULT 5,
        hours_between REAL DEFAULT 5,
        daily_limit_per_account INTEGER DEFAULT 15,
        silent INTEGER DEFAULT 1,
        status TEXT DEFAULT 'paused',
        offset INTEGER DEFAULT 0,
        total_added INTEGER DEFAULT 0,
        total_already INTEGER DEFAULT 0,
        total_privacy INTEGER DEFAULT 0,
        total_errors INTEGER DEFAULT 0,
        created_at TEXT,
        last_run TEXT,
        next_run TEXT
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS account_cooldowns (
        phone TEXT NOT NULL,
        campaign_id TEXT NOT NULL,
        daily_count INTEGER DEFAULT 0,
        last_add TEXT,
        cooldown_until TEXT,
        flood_until TEXT,
        PRIMARY KEY (phone, campaign_id)
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS add_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        campaign_id TEXT,
        user_id INTEGER,
        username TEXT,
        name TEXT,
        phone_used TEXT,
        status TEXT,
        error TEXT,
        timestamp TEXT
    )''')
    conn.commit()
    conn.close()


init_db()


def db():
    return sqlite3.connect(AUTOMATION_DB)


# ═══════════════════════════════════════════════════════════════════════════════
#  ACCOUNT MANAGER — smart rotation with cooldown tracking
# ═══════════════════════════════════════════════════════════════════════════════

class AccountManager:
    """Manages multiple Telegram accounts with smart rotation and cooldown tracking."""

    def __init__(self, campaign_id):
        self.campaign_id = campaign_id
        self.accounts = [a for a in config.get('accounts', []) if not a.get('blacklisted')]
        self.clients = {}  # phone -> TelegramClient
        self._current_idx = 0

    def get_available_accounts(self):
        """Returns accounts that are not on cooldown and haven't hit daily limit."""
        conn = db()
        now = datetime.now().isoformat()
        available = []
        for acct in self.accounts:
            phone = acct['phone']
            row = conn.execute(
                'SELECT daily_count, cooldown_until, flood_until FROM account_cooldowns '
                'WHERE phone=? AND campaign_id=?', (phone, self.campaign_id)
            ).fetchone()

            if row:
                daily, cooldown, flood = row
                # Check flood cooldown (24h)
                if flood and flood > now:
                    continue
                # Check short cooldown
                if cooldown and cooldown > now:
                    continue
                # Daily limit is checked per-campaign
                campaign = conn.execute(
                    'SELECT daily_limit_per_account FROM campaigns WHERE id=?',
                    (self.campaign_id,)
                ).fetchone()
                limit = campaign[0] if campaign else 15
                if daily >= limit:
                    continue
            available.append(acct)
        conn.close()
        return available

    def get_next_account(self):
        """Round-robin through available accounts."""
        available = self.get_available_accounts()
        if not available:
            return None
        acct = available[self._current_idx % len(available)]
        self._current_idx += 1
        return acct

    def connect(self, acct):
        """Connect and cache a client."""
        phone = acct['phone']
        if phone in self.clients:
            return self.clients[phone]
        try:
            client = TelegramClient(phone, int(acct['api_id']), acct['api_hash'])
            client.connect()
            if not client.is_user_authorized():
                print(f"  {R}⚠️ Session expirée: {phone}{RST}")
                return None
            self.clients[phone] = client
            return client
        except Exception as e:
            print(f"  {R}⚠️ Connexion impossible ({phone}): {e}{RST}")
            return None

    def record_add(self, phone, success=True, flood_seconds=0):
        """Record an add attempt and update cooldowns."""
        conn = db()
        now = datetime.now()
        # Upsert cooldown record
        conn.execute('''INSERT INTO account_cooldowns (phone, campaign_id, daily_count, last_add)
            VALUES (?, ?, 1, ?)
            ON CONFLICT(phone, campaign_id) DO UPDATE SET
                daily_count = daily_count + 1,
                last_add = ?''',
            (phone, self.campaign_id, now.isoformat(), now.isoformat()))

        if flood_seconds > 0:
            flood_until = (now + timedelta(hours=24)).isoformat()
            conn.execute(
                'UPDATE account_cooldowns SET flood_until=? WHERE phone=? AND campaign_id=?',
                (flood_until, phone, self.campaign_id))
            # Also blacklist in config
            for a in config['accounts']:
                if a['phone'] == phone:
                    a['blacklisted'] = True
                    a['blacklist_time'] = now.isoformat()
            save_config()
            print(f"  {Y}🚫 {phone} blacklisté 24h (flood){RST}")

        conn.commit()
        conn.close()

    def reset_daily_counts(self):
        """Reset daily counts for all accounts (called at midnight or on new day)."""
        conn = db()
        conn.execute(
            'UPDATE account_cooldowns SET daily_count=0 WHERE campaign_id=?',
            (self.campaign_id,))
        conn.commit()
        conn.close()

    def disconnect_all(self):
        for phone, client in self.clients.items():
            try:
                client.disconnect()
            except Exception:
                pass
        self.clients.clear()

    def status_report(self):
        """Returns a dict of account statuses for this campaign."""
        conn = db()
        now = datetime.now().isoformat()
        report = []
        for acct in self.accounts:
            phone = acct['phone']
            row = conn.execute(
                'SELECT daily_count, cooldown_until, flood_until FROM account_cooldowns '
                'WHERE phone=? AND campaign_id=?', (phone, self.campaign_id)
            ).fetchone()
            status = "ready"
            daily = 0
            if row:
                daily = row[0]
                if row[2] and row[2] > now:
                    status = "flood"
                elif row[1] and row[1] > now:
                    status = "cooldown"
            report.append({"phone": phone, "status": status, "daily": daily})
        conn.close()
        return report


# ═══════════════════════════════════════════════════════════════════════════════
#  CAMPAIGN MANAGEMENT
# ═══════════════════════════════════════════════════════════════════════════════

def create_campaign(name, target_group_id, target_group_title, target_group_hash,
                    source_csv='members.csv', total=0, per_wave=5,
                    hours_between=5, daily_limit=15, silent=True):
    """Create a new campaign."""
    cid = f"camp_{int(time.time())}_{random.randint(100,999)}"
    conn = db()
    conn.execute('''INSERT INTO campaigns
        (id, name, target_group_id, target_group_title, target_group_hash,
         source_csv, total_target, per_wave, hours_between, daily_limit_per_account,
         silent, status, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',
        (cid, name, target_group_id, target_group_title, target_group_hash,
         source_csv, total, per_wave, hours_between, daily_limit,
         1 if silent else 0, 'paused', datetime.now().isoformat()))
    conn.commit()
    conn.close()
    log(f"Campaign created: {name} ({cid}) → {target_group_title}")
    return cid


def list_campaigns():
    conn = db()
    rows = conn.execute(
        'SELECT id, name, target_group_title, status, total_target, offset, '
        'total_added, total_already, total_privacy, total_errors, per_wave, '
        'hours_between, daily_limit_per_account, last_run, source_csv '
        'FROM campaigns ORDER BY created_at DESC'
    ).fetchall()
    conn.close()
    return rows


def get_campaign(cid):
    conn = db()
    row = conn.execute('SELECT * FROM campaigns WHERE id=?', (cid,)).fetchone()
    conn.close()
    if not row:
        return None
    cols = ['id', 'name', 'target_group_id', 'target_group_title', 'target_group_hash',
            'source_csv', 'total_target', 'per_wave', 'hours_between',
            'daily_limit_per_account', 'silent', 'status', 'offset',
            'total_added', 'total_already', 'total_privacy', 'total_errors',
            'created_at', 'last_run', 'next_run']
    return dict(zip(cols, row))


def update_campaign(cid, **kwargs):
    conn = db()
    sets = ', '.join(f'{k}=?' for k in kwargs)
    vals = list(kwargs.values()) + [cid]
    conn.execute(f'UPDATE campaigns SET {sets} WHERE id=?', vals)
    conn.commit()
    conn.close()


def delete_campaign(cid):
    conn = db()
    conn.execute('DELETE FROM campaigns WHERE id=?', (cid,))
    conn.execute('DELETE FROM account_cooldowns WHERE campaign_id=?', (cid,))
    conn.execute('DELETE FROM add_log WHERE campaign_id=?', (cid,))
    conn.commit()
    conn.close()


def campaign_add_log(cid, limit=50):
    conn = db()
    rows = conn.execute(
        'SELECT user_id, username, name, phone_used, status, error, timestamp '
        'FROM add_log WHERE campaign_id=? ORDER BY id DESC LIMIT ?',
        (cid, limit)).fetchall()
    conn.close()
    return rows


# ═══════════════════════════════════════════════════════════════════════════════
#  CAMPAIGN RUNNER — the core automation engine
# ═══════════════════════════════════════════════════════════════════════════════

def _delete_join_messages(client, group_entity, limit=5):
    try:
        msgs = client.get_messages(group_entity, limit=limit)
        to_del = [m.id for m in msgs
                  if isinstance(m, MessageService) and isinstance(m.action, MessageActionChatAddUser)]
        if to_del:
            client.delete_messages(group_entity, to_del)
        return len(to_del)
    except Exception:
        return 0


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


_stop_requested = False

def _handle_signal(sig, frame):
    global _stop_requested
    _stop_requested = True
    print(f"\n{Y}  ⏸ Arrêt demandé — fin de la vague en cours...{RST}")


def run_campaign(cid, callback=None):
    """
    Execute a campaign. This is the main automation loop.

    callback: optional function(event, data) for progress updates
        events: 'wave_start', 'add', 'wave_end', 'pause', 'done', 'error', 'flood_stop'
    """
    global _stop_requested
    _stop_requested = False
    signal.signal(signal.SIGINT, _handle_signal)

    campaign = get_campaign(cid)
    if not campaign:
        return "❌ Campagne introuvable."

    def emit(event, data=None):
        if callback:
            callback(event, data or {})

    # Load members
    users = read_members_csv(campaign['source_csv'])
    if not users:
        update_campaign(cid, status='error')
        return f"❌ {campaign['source_csv']} vide ou introuvable."

    total_target = campaign['total_target'] if campaign['total_target'] > 0 else len(users)
    per_wave = campaign['per_wave']
    hours_between = campaign['hours_between']
    offset = campaign['offset']
    silent = campaign['silent']

    remaining = min(total_target - (campaign['total_added'] + campaign['total_already']
                    + campaign['total_privacy']), len(users) - offset)
    if remaining <= 0:
        update_campaign(cid, status='completed')
        return "✅ Campagne terminée — tous les membres ont été traités."

    num_waves = -(-remaining // per_wave)
    target_entity = InputPeerChannel(campaign['target_group_id'], campaign['target_group_hash'])

    update_campaign(cid, status='running', last_run=datetime.now().isoformat())
    mgr = AccountManager(cid)

    print(f"\n{G}{'═' * 55}{RST}")
    print(f"{G}  🚀 CAMPAGNE: {campaign['name']}{RST}")
    print(f"{G}{'═' * 55}{RST}")
    print(f"  Cible       : {campaign['target_group_title']}")
    print(f"  Restant     : {remaining} membres")
    print(f"  Vagues      : {num_waves} x {per_wave}")
    print(f"  Comptes     : {len(mgr.accounts)} actifs")
    print(f"  Pause       : {hours_between}h entre vagues")
    print(f"  Limite/jour : {campaign['daily_limit_per_account']}/compte")
    if silent:
        print(f"  🔇 Mode silencieux")
    print()

    stats = {'added': 0, 'already': 0, 'privacy': 0, 'error': 0}
    wave_num = campaign['offset'] // per_wave + 1
    flood_stop = False

    # Check if daily counts need reset (new day)
    last_run = campaign.get('last_run')
    if last_run:
        last_date = last_run[:10]
        today = datetime.now().strftime('%Y-%m-%d')
        if last_date != today:
            mgr.reset_daily_counts()
            print(f"  {C}🔄 Compteurs journaliers réinitialisés{RST}\n")

    try:
        while remaining > 0 and not flood_stop and not _stop_requested:
            batch_size = min(per_wave, remaining)
            batch = users[offset:offset + batch_size]
            if not batch:
                break

            emit('wave_start', {'wave': wave_num, 'size': len(batch), 'offset': offset})
            print(f"\n{B}━━━ Vague {wave_num} — {len(batch)} membres ━━━{RST}\n")

            wave_added = 0
            all_flooded = True

            for j, user in enumerate(batch, 1):
                if _stop_requested:
                    break

                name_display = user['name'] or user['username'] or str(user['id'])

                # Get next available account
                acct = mgr.get_next_account()
                if not acct:
                    print(f"  {R}🛑 Aucun compte disponible — tous en cooldown/limite{RST}")
                    # Wait 1h and retry
                    next_check = datetime.now() + timedelta(hours=1)
                    print(f"  💤 Attente 1h (reprise à {next_check.strftime('%H:%M')})...")
                    update_campaign(cid, next_run=next_check.isoformat())
                    time.sleep(3600)
                    mgr.reset_daily_counts()  # In case we crossed midnight
                    acct = mgr.get_next_account()
                    if not acct:
                        print(f"  {R}🛑 Toujours aucun compte — arrêt{RST}")
                        flood_stop = True
                        break

                client = mgr.connect(acct)
                if not client:
                    continue

                phone = acct['phone']
                print(f"  [{j}/{len(batch)}] ➕ {name_display} via {phone[-4:]}...", end=" ", flush=True)

                try:
                    client(InviteToChannelRequest(target_entity,
                           [InputPeerUser(user['id'], user['access_hash'])]))
                    stats['added'] += 1
                    wave_added += 1
                    all_flooded = False
                    mgr.record_add(phone, success=True)

                    # Log to DB
                    conn = db()
                    conn.execute(
                        'INSERT INTO add_log (campaign_id,user_id,username,name,phone_used,status,timestamp) '
                        'VALUES (?,?,?,?,?,?,?)',
                        (cid, user['id'], user['username'], user['name'], phone,
                         'success', datetime.now().isoformat()))
                    conn.commit()
                    conn.close()

                    if silent:
                        time.sleep(1.5)
                        # Resolve group entity for delete
                        try:
                            group_ent = client.get_entity(InputPeerChannel(
                                campaign['target_group_id'], campaign['target_group_hash']))
                            _delete_join_messages(client, group_ent)
                        except Exception:
                            pass
                        print(f"{G}✅ (silencieux){RST}")
                    else:
                        print(f"{G}✅{RST}")

                    emit('add', {'user': name_display, 'phone': phone, 'status': 'success',
                                 'count': stats['added']})

                except UserAlreadyParticipantError:
                    stats['already'] += 1
                    all_flooded = False
                    print(f"ℹ️  déjà membre")
                    emit('add', {'user': name_display, 'status': 'already'})

                except UserPrivacyRestrictedError:
                    stats['privacy'] += 1
                    all_flooded = False
                    print(f"🔒 privacy")
                    emit('add', {'user': name_display, 'status': 'privacy'})

                except FloodWaitError as e:
                    if e.seconds > 300:
                        print(f"\n  {R}🛑 FloodWait {e.seconds}s sur {phone}{RST}")
                        mgr.record_add(phone, flood_seconds=e.seconds)
                        # Try next account
                        continue
                    print(f"{Y}⏳ wait {e.seconds}s{RST}")
                    time.sleep(e.seconds + 10)

                except PeerFloodError:
                    print(f"\n  {R}🛑 PeerFlood: {phone}{RST}")
                    mgr.record_add(phone, flood_seconds=86400)
                    # Try next account instead of stopping
                    continue

                except Exception as e:
                    stats['error'] += 1
                    err_str = str(e)[:60]
                    print(f"{Y}⚠️  {err_str}{RST}")
                    # Log error
                    conn = db()
                    conn.execute(
                        'INSERT INTO add_log (campaign_id,user_id,username,name,phone_used,status,error,timestamp) '
                        'VALUES (?,?,?,?,?,?,?,?)',
                        (cid, user['id'], user['username'], user['name'], phone,
                         'error', err_str, datetime.now().isoformat()))
                    conn.commit()
                    conn.close()
                    emit('add', {'user': name_display, 'status': 'error', 'error': err_str})

                # Timer between adds (45-75s)
                if j < len(batch) and not _stop_requested:
                    wait = random.uniform(45, 75)
                    print(f"      ⏱️  {wait:.0f}s...", flush=True)
                    time.sleep(wait)

            # End of wave
            offset += len(batch)
            remaining -= len(batch)
            wave_num += 1

            # Update campaign state
            update_campaign(cid,
                offset=offset,
                total_added=campaign['total_added'] + stats['added'],
                total_already=campaign['total_already'] + stats['already'],
                total_privacy=campaign['total_privacy'] + stats['privacy'],
                total_errors=campaign['total_errors'] + stats['error'],
                last_run=datetime.now().isoformat())

            print(f"\n  📊 Vague: +{wave_added} ajoutés"
                  f" (total campagne: {campaign['total_added'] + stats['added']})")

            emit('wave_end', {'wave': wave_num - 1, 'added': wave_added, 'stats': stats})

            # Reset stats for next wave reporting
            stats = {'added': 0, 'already': 0, 'privacy': 0, 'error': 0}

            # Pause between waves
            if remaining > 0 and not flood_stop and not _stop_requested:
                next_time = datetime.now() + timedelta(hours=hours_between)
                update_campaign(cid, next_run=next_time.isoformat())
                print(f"\n  {C}💤 Pause {hours_between}h — prochaine vague à ~{next_time.strftime('%H:%M')}{RST}")
                print(f"     Ctrl+C pour interrompre proprement\n")
                emit('pause', {'hours': hours_between, 'next': next_time.isoformat()})

                # Sleep with interrupt check
                end_time = time.time() + hours_between * 3600
                while time.time() < end_time and not _stop_requested:
                    time.sleep(min(30, end_time - time.time()))

                # Check if we crossed midnight → reset daily counts
                mgr.reset_daily_counts()

    finally:
        mgr.disconnect_all()
        signal.signal(signal.SIGINT, signal.SIG_DFL)

    # Final state
    campaign = get_campaign(cid)
    if _stop_requested:
        update_campaign(cid, status='paused', next_run=None)
        status_msg = "⏸ Campagne mise en pause"
    elif flood_stop:
        update_campaign(cid, status='paused')
        status_msg = "🛑 Arrêt — comptes en cooldown"
    elif remaining <= 0:
        update_campaign(cid, status='completed', next_run=None)
        status_msg = "✅ Campagne terminée"
    else:
        update_campaign(cid, status='paused')
        status_msg = "⏸ Campagne en pause"

    total_a = campaign['total_added']
    total_al = campaign['total_already']
    total_p = campaign['total_privacy']
    total_e = campaign['total_errors']

    report = (
        f"\n{G}{'═' * 55}{RST}\n"
        f"  {status_msg}\n"
        f"  📊 Bilan total campagne:\n"
        f"     ✅ Ajoutés     : {total_a}\n"
        f"     ℹ️  Déjà membres: {total_al}\n"
        f"     🔒 Privacy     : {total_p}\n"
        f"     ❌ Erreurs     : {total_e}\n"
        f"     📍 Position    : {campaign['offset']}\n"
        f"{G}{'═' * 55}{RST}\n"
    )
    print(report)
    log(f"Campaign {campaign['name']}: {status_msg} — added={total_a}")
    emit('done', {'status': status_msg, 'total_added': total_a})
    return report


# ═══════════════════════════════════════════════════════════════════════════════
#  INTERACTIVE MENU
# ═══════════════════════════════════════════════════════════════════════════════

def _resolve_group(group_str):
    """Resolve a group identifier to (id, title, access_hash) using first available account."""
    active = [a for a in config.get('accounts', []) if not a.get('blacklisted')]
    if not active:
        return None, None, None, "❌ Aucun compte actif."
    for acct in active:
        try:
            client = TelegramClient(acct['phone'], int(acct['api_id']), acct['api_hash'])
            client.connect()
            if not client.is_user_authorized():
                client.disconnect()
                continue
            entity = client.get_entity(group_str)
            gid = entity.id
            title = getattr(entity, 'title', str(gid))
            ahash = entity.access_hash
            client.disconnect()
            return gid, title, ahash, None
        except Exception as e:
            try:
                client.disconnect()
            except:
                pass
            continue
    return None, None, None, "❌ Impossible de résoudre le groupe."


def interactive_menu():
    os.system('clear' if os.name != 'nt' else 'cls')
    print(f"\n{G}{'═' * 55}{RST}")
    print(f"{G}  🤖 TELEGRAM MANAGER — AUTOMATION ENGINE{RST}")
    print(f"{G}{'═' * 55}{RST}")
    print(f"  Comptes actifs: {sum(1 for a in config.get('accounts',[]) if not a.get('blacklisted'))}")
    print()

    while True:
        print(f"\n{B}  1{RST} — Créer une campagne")
        print(f"{B}  2{RST} — Lancer une campagne")
        print(f"{B}  3{RST} — Voir le statut des campagnes")
        print(f"{B}  4{RST} — Voir le log d'une campagne")
        print(f"{B}  5{RST} — Supprimer une campagne")
        print(f"{B}  6{RST} — Statut des comptes")
        print(f"{B}  7{RST} — Lancer toutes les campagnes actives")
        print(f"{B}  q{RST} — Quitter")

        choice = input(f"\n{B}▶ {RST}").strip()

        if choice == '1':
            # Create campaign
            name = input("  Nom de la campagne: ").strip()
            if not name:
                continue
            group_str = input("  Groupe cible (@username ou lien): ").strip()
            print("  Résolution du groupe...")
            gid, title, ahash, err = _resolve_group(group_str)
            if err:
                print(f"  {err}")
                continue
            print(f"  ✅ Trouvé: {title}")

            csv_file = input("  Fichier CSV (Entrée=members.csv): ").strip() or 'members.csv'
            if not os.path.exists(csv_file):
                print(f"  ❌ {csv_file} introuvable.")
                continue

            members = read_members_csv(csv_file)
            total = input(f"  Nombre total ({len(members)} dispo, Entrée=tous): ").strip()
            total = int(total) if total else len(members)
            per_wave = input("  Par vague (Entrée=5): ").strip()
            per_wave = int(per_wave) if per_wave else 5
            hours = input("  Heures entre vagues (Entrée=5): ").strip()
            hours = float(hours) if hours else 5
            daily = input("  Limite /jour /compte (Entrée=15): ").strip()
            daily = int(daily) if daily else 15

            cid = create_campaign(name, gid, title, ahash,
                                  source_csv=csv_file, total=total,
                                  per_wave=per_wave, hours_between=hours,
                                  daily_limit=daily)
            print(f"\n  {G}✅ Campagne créée: {cid}{RST}")
            print(f"  Lancez-la avec l'option 2.")

        elif choice == '2':
            campaigns = list_campaigns()
            if not campaigns:
                print("  Aucune campagne.")
                continue
            print("\n  Campagnes disponibles:")
            for i, c in enumerate(campaigns, 1):
                status_color = G if c[3] == 'completed' else (Y if c[3] == 'paused' else C)
                print(f"  {i}. [{status_color}{c[3]}{RST}] {c[1]} → {c[2]} "
                      f"({c[6]}/{c[4]} ajoutés)")
            idx = input("  Numéro à lancer: ").strip()
            try:
                camp = campaigns[int(idx) - 1]
                print(f"\n  🚀 Lancement: {camp[1]}...")
                run_campaign(camp[0])
            except (ValueError, IndexError):
                print("  ❌ Invalide.")

        elif choice == '3':
            campaigns = list_campaigns()
            if not campaigns:
                print("  Aucune campagne.")
                continue
            print(f"\n  {'─' * 55}")
            for c in campaigns:
                cid, name, target, status = c[0], c[1], c[2], c[3]
                total, offset, added = c[4], c[5], c[6]
                already, privacy, errors = c[7], c[8], c[9]
                per_wave, hours, daily = c[10], c[11], c[12]
                last_run = c[13] or 'jamais'

                status_color = G if status == 'completed' else (
                    C if status == 'running' else (Y if status == 'paused' else R))
                pct = (added / total * 100) if total > 0 else 0
                bar_len = 20
                filled = int(pct / 100 * bar_len)
                bar = f"{'█' * filled}{'░' * (bar_len - filled)}"

                print(f"\n  {B}{name}{RST} [{status_color}{status}{RST}]")
                print(f"  → {target}")
                print(f"  {bar} {pct:.0f}%  ({added}/{total})")
                print(f"  ✅{added} ℹ️{already} 🔒{privacy} ❌{errors}")
                print(f"  Config: {per_wave}/vague, {hours}h pause, {daily}/jour/compte")
                print(f"  Dernier: {last_run[:16] if last_run != 'jamais' else 'jamais'}")
            print(f"\n  {'─' * 55}")

        elif choice == '4':
            campaigns = list_campaigns()
            if not campaigns:
                print("  Aucune campagne.")
                continue
            for i, c in enumerate(campaigns, 1):
                print(f"  {i}. {c[1]}")
            idx = input("  Numéro: ").strip()
            try:
                camp = campaigns[int(idx) - 1]
                logs = campaign_add_log(camp[0], limit=30)
                if not logs:
                    print("  Aucun log.")
                    continue
                print(f"\n  Derniers ajouts ({camp[1]}):")
                for row in logs:
                    uid, uname, name, phone, status, err, ts = row
                    ts_short = ts[11:19] if ts else '?'
                    s = f"{G}✅{RST}" if status == 'success' else (
                        f"{Y}⚠️{RST}" if status == 'error' else f"ℹ️")
                    print(f"  {ts_short} {s} {name or uname or uid} via ...{phone[-4:]}"
                          f"{f' ({err})' if err else ''}")
            except (ValueError, IndexError):
                print("  ❌ Invalide.")

        elif choice == '5':
            campaigns = list_campaigns()
            if not campaigns:
                print("  Aucune campagne.")
                continue
            for i, c in enumerate(campaigns, 1):
                print(f"  {i}. [{c[3]}] {c[1]}")
            idx = input("  Numéro à supprimer: ").strip()
            try:
                camp = campaigns[int(idx) - 1]
                confirm = input(f"  Supprimer '{camp[1]}' ? (o/N): ").strip().lower()
                if confirm in ('o', 'oui'):
                    delete_campaign(camp[0])
                    print(f"  {G}✅ Supprimée.{RST}")
            except (ValueError, IndexError):
                print("  ❌ Invalide.")

        elif choice == '6':
            active = [a for a in config.get('accounts', []) if not a.get('blacklisted')]
            blacklisted = [a for a in config.get('accounts', []) if a.get('blacklisted')]
            print(f"\n  {G}● Actifs ({len(active)}):{RST}")
            for a in active:
                has_session = os.path.exists(f"{a['phone']}.session")
                s = f"{G}●{RST}" if has_session else f"{R}✕{RST}"
                print(f"    {s} {a['phone']}")
            if blacklisted:
                print(f"\n  {Y}● Blacklistés ({len(blacklisted)}):{RST}")
                for a in blacklisted:
                    bt = a.get('blacklist_time', '')
                    print(f"    {Y}⏸{RST} {a['phone']} (depuis {bt[:16] if bt else '?'})")

        elif choice == '7':
            campaigns = list_campaigns()
            runnable = [c for c in campaigns if c[3] in ('paused', 'created')]
            if not runnable:
                print("  Aucune campagne à lancer.")
                continue
            print(f"\n  🚀 Lancement de {len(runnable)} campagne(s) en séquence...")
            for c in runnable:
                print(f"\n  → {c[1]}")
                run_campaign(c[0])
                time.sleep(5)

        elif choice in ('q', 'Q', '15'):
            print(f"\n  {G}🚪 Au revoir.{RST}\n")
            break


# ═══════════════════════════════════════════════════════════════════════════════
#  CLI ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        if cmd == 'status':
            campaigns = list_campaigns()
            if not campaigns:
                print("Aucune campagne.")
            else:
                for c in campaigns:
                    pct = (c[6] / c[4] * 100) if c[4] > 0 else 0
                    print(f"[{c[3]}] {c[1]} → {c[2]} ({c[6]}/{c[4]}, {pct:.0f}%)")
        elif cmd == 'list':
            campaigns = list_campaigns()
            for c in campaigns:
                print(f"{c[0]}  [{c[3]}]  {c[1]}")
        elif cmd == 'run' and len(sys.argv) > 2:
            cid = sys.argv[2]
            run_campaign(cid)
        else:
            print("Usage: python automation.py [run <id> | status | list]")
    else:
        interactive_menu()
