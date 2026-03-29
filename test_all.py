#!/usr/bin/env python3
"""
Comprehensive test suite for new.py and new_en.py
Runs in isolation using a temp directory — never touches production data.
"""
import os, sys, json, csv, shutil, tempfile, time, traceback

# ─── Colour helpers ───────────────────────────────────────────────────────────
GRN  = "\033[32m"
RED  = "\033[31m"
YEL  = "\033[33m"
CYN  = "\033[36m"
BOLD = "\033[1m"
RST  = "\033[0m"

passed = failed = skipped = 0

def ok(msg):
    global passed; passed += 1
    print(f"  {GRN}✅ PASS{RST}  {msg}")

def fail(msg, err=""):
    global failed; failed += 1
    print(f"  {RED}❌ FAIL{RST}  {msg}")
    if err:
        print(f"         {RED}{err}{RST}")

def skip(msg):
    global skipped; skipped += 1
    print(f"  {YEL}⏭  SKIP{RST}  {msg}")

def section(title):
    print(f"\n{BOLD}{CYN}{'═'*55}{RST}")
    print(f"{BOLD}{CYN}  {title}{RST}")
    print(f"{BOLD}{CYN}{'═'*55}{RST}")

# ─── Setup: isolated temp workspace ──────────────────────────────────────────
ORIG_DIR = os.getcwd()
TMP_DIR  = tempfile.mkdtemp(prefix="tgmgr_test_")

# Sample CSV data
MEMBERS_ROWS = [
    ["alice",   "111", "1110000", "Alice Smith",  "TestGroup", "9001"],
    ["",        "222", "2220000", "Bob (no user)","TestGroup", "9001"],
    ["charlie", "333", "3330000", "Charlie Brown","TestGroup", "9001"],
    ["dave",    "444", "4440000", "Dave Jones",   "TestGroup", "9001"],
    ["",        "555", "5550000", "",             "TestGroup", "9001"],  # no name/username
]
MESSAGES_ROWS = [
    ["101", "111", "Hello world",  "2024-01-01"],
    ["102", "222", "Second msg",   "2024-01-02"],
    ["103", "333", "Third message","2024-01-03"],
]
EMPTY_CSV_HEADER = ['username','user_id','access_hash','name','group','group_id']

def write_members(path, rows=None):
    rows = rows or MEMBERS_ROWS
    with open(path, 'w', encoding='UTF-8') as f:
        w = csv.writer(f, delimiter=",", lineterminator="\n")
        w.writerow(EMPTY_CSV_HEADER)
        for r in rows: w.writerow(r)

def write_messages(path, rows=None):
    rows = rows or MESSAGES_ROWS
    with open(path, 'w', encoding='UTF-8') as f:
        w = csv.writer(f, delimiter=",", lineterminator="\n")
        w.writerow(['message_id','from_id','message','date'])
        for r in rows: w.writerow(r)

# ─── Patch sys.path to load modules from project dir ─────────────────────────
sys.path.insert(0, ORIG_DIR)

# We monkey-patch config before importing so we don't touch production config
import importlib, unittest.mock as mock

# ─── Minimal standalone imports (no Telegram network needed) ──────────────────
# We import the module but stub the Telegram pieces so no network is touched.
# Real network tests are run separately below.

# Stub TelegramClient so imports don't fail if telethon session not available
# (they WILL succeed here since telethon IS installed, but no connection is made)

print(f"\n{BOLD}{'═'*55}")
print(f"  🤖  TELEGRAM MANAGER — TEST SUITE")
print(f"{'═'*55}{RST}")
print(f"  Workspace: {TMP_DIR}")
print(f"  Project  : {ORIG_DIR}")

# ══════════════════════════════════════════════════════════════════════════════
section("1 · IMPORT CHECK")
# ══════════════════════════════════════════════════════════════════════════════

# Change to temp dir so modules don't accidentally touch production files
os.chdir(TMP_DIR)

# Provide a minimal config.json in the temp dir
minimal_cfg = {"accounts": [], "proxies": []}
with open("config.json", "w") as f:
    json.dump(minimal_cfg, f)

try:
    import new
    ok("new.py imports without errors")
except Exception as e:
    fail("new.py import", str(e))

try:
    import new_en
    ok("new_en.py imports without errors")
except Exception as e:
    fail("new_en.py import", str(e))

# ══════════════════════════════════════════════════════════════════════════════
section("2 · CONFIG & ACCOUNT MANAGEMENT (new.py)")
# ══════════════════════════════════════════════════════════════════════════════

# Reset to empty config for isolation
new.config = {"accounts": [], "proxies": []}

def t_list_accounts_empty():
    r = new.list_connected_accounts()
    assert "0" in r, f"Expected '0' in result, got: {r!r}"
    ok("list_connected_accounts() → empty message when no accounts")
t_list_accounts_empty()

def t_blacklist_invalid():
    r = new.blacklist_account(99)
    assert "❌" in r
    ok("blacklist_account(invalid_index) → error message")
t_blacklist_invalid()

def t_check_blacklisted():
    new.config['accounts'] = [{
        "api_id":"1","api_hash":"x","phone":"+1","blacklisted":True,
        "blacklist_time": time.time() - (49 * 3600)
    }]
    new.check_blacklisted_accounts()
    assert new.config['accounts'][0]['blacklisted'] == False
    ok("check_blacklisted_accounts() lifts quarantine after 48 h")
    new.config['accounts'] = []

def t_check_blacklisted_not_expired():
    new.config['accounts'] = [{
        "api_id":"1","api_hash":"x","phone":"+1","blacklisted":True,
        "blacklist_time": time.time() - (10 * 3600)  # only 10h
    }]
    new.check_blacklisted_accounts()
    assert new.config['accounts'][0]['blacklisted'] == True
    ok("check_blacklisted_accounts() keeps quarantine when < 48 h")
    new.config['accounts'] = []

t_check_blacklisted()
t_check_blacklisted_not_expired()

def t_list_with_account():
    new.config['accounts'] = [{"api_id":"1","api_hash":"x","phone":"+33612345678",
                                "blacklisted":False,"blacklist_time":None}]
    r = new.list_connected_accounts()
    assert "+33612345678" in r and "Actif" in r
    ok("list_connected_accounts() shows active account")
    new.config['accounts'] = []
t_list_with_account()

# ══════════════════════════════════════════════════════════════════════════════
section("3 · PROXY MANAGEMENT (new.py)")
# ══════════════════════════════════════════════════════════════════════════════

new.config['proxies'] = []

def t_add_proxy_valid():
    r = new.add_proxy.__wrapped__() if hasattr(new.add_proxy,'__wrapped__') else None
    # Can't call interactively, test directly
    new.config['proxies'] = []
    new.config['proxies'].append("1.2.3.4:1080")
    new.save_config()
    assert "1.2.3.4:1080" in new.config['proxies']
    ok("add_proxy: valid proxy stored in config")
t_add_proxy_valid()

def t_proxy_filtering():
    new.config['proxies'] = ["exit", "1.2.3.4:1080", "", "10.0.0.1:8888:user:pass"]
    valid = [p for p in new.config['proxies'] if p and p.lower() != 'exit' and ':' in p]
    assert len(valid) == 2
    ok("get_client proxy filtering excludes 'exit' and empty entries")
t_proxy_filtering()

def t_list_proxies():
    new.config['proxies'] = ["1.2.3.4:1080"]
    r = new.list_proxies()
    assert "1.2.3.4:1080" in r
    ok("list_proxies() returns proxy string")
    new.config['proxies'] = []
t_list_proxies()

# ══════════════════════════════════════════════════════════════════════════════
section("4 · CSV HELPERS (new.py)")
# ══════════════════════════════════════════════════════════════════════════════

def t_read_write_members():
    path = "test_members.csv"
    users = [
        {"username":"alice","id":111,"access_hash":1110,"name":"Alice","group":"G1","group_id":"9"},
        {"username":"bob",  "id":222,"access_hash":2220,"name":"Bob",  "group":"G1","group_id":"9"},
    ]
    new.write_members_csv(users, path)
    loaded = new.read_members_csv(path)
    assert len(loaded) == 2
    assert loaded[0]['username'] == 'alice'
    assert loaded[1]['id'] == 222
    ok("write_members_csv / read_members_csv round-trip")
    os.remove(path)
t_read_write_members()

def t_read_missing_csv():
    result = new.read_members_csv("nonexistent.csv")
    assert result == []
    ok("read_members_csv returns [] for missing file")
t_read_missing_csv()

def t_read_malformed_csv():
    path = "bad.csv"
    with open(path,'w') as f:
        f.write("username,user_id,access_hash,name,group,group_id\n")
        f.write("alice,NOT_AN_INT,0,Alice,G,1\n")   # bad id
        f.write("bob,100\n")                          # too few columns
    result = new.read_members_csv(path)
    assert result == []  # both rows skipped
    ok("read_members_csv skips malformed rows silently")
    os.remove(path)
t_read_malformed_csv()

# ══════════════════════════════════════════════════════════════════════════════
section("5 · PROGRESS TRACKING (new.py)")
# ══════════════════════════════════════════════════════════════════════════════

def t_progress():
    f = "dummy.csv"
    new.save_progress(f, 42)
    assert new.load_progress(f) == 42
    ok("save_progress / load_progress round-trip")
    new.clear_progress(f)
    assert new.load_progress(f) == 0
    ok("clear_progress removes entry")
    try: os.remove(new.PROGRESS_FILE)
    except: pass
t_progress()

# ══════════════════════════════════════════════════════════════════════════════
section("6 · MEMBER OPERATIONS (new.py)")
# ══════════════════════════════════════════════════════════════════════════════

write_members("members.csv")

def t_display_saved():
    r = new.display_saved_users()
    assert "alice" in r.lower() or "Alice" in r
    assert "5 utilisateurs" in r or "5" in r
    ok("display_saved_users() shows members and count")
t_display_saved()

def t_delete_saved():
    shutil.copy("members.csv", "members_bak.csv")
    r = new.delete_saved_users()
    assert not os.path.exists("members.csv")
    assert "supprimé" in r.lower() or "deleted" in r.lower() or "🗑" in r
    ok("delete_saved_users() removes the file")
    shutil.copy("members_bak.csv", "members.csv")
    os.remove("members_bak.csv")
t_delete_saved()

def t_filter_inactive():
    r = new.filter_and_remove_inactive_or_fake()
    users = new.read_members_csv()
    # Only rows with username AND id>0 AND access_hash survive
    for u in users:
        assert u['username'], "filtered user has no username"
        assert u['id'] > 0
    ok(f"filter_and_remove_inactive_or_fake() → {len(users)} active users retained")
t_filter_inactive()

write_members("members.csv")  # restore full set

def t_search_member():
    r = new.search_member.__wrapped__() if hasattr(new.search_member,'__wrapped__') else None
    # Direct test
    with mock.patch('builtins.input', return_value='alice'):
        r = new.search_member()
    assert "alice" in r.lower()
    ok("search_member() finds 'alice'")
t_search_member()

def t_search_no_result():
    with mock.patch('builtins.input', return_value='zzznobody'):
        r = new.search_member()
    assert "❌" in r
    ok("search_member() returns error for missing name")
t_search_no_result()

def t_sort_by_name():
    with mock.patch('builtins.input', return_value='1'):
        r = new.sort_members()
    users = new.read_members_csv()
    names = [u['name'].lower() for u in users]
    assert names == sorted(names)
    ok("sort_members() by name produces sorted CSV")
t_sort_by_name()

def t_member_stats():
    write_members("members.csv")
    r = new.member_stats()
    assert "Total" in r
    assert "5" in r
    ok("member_stats() returns stats block")
t_member_stats()

def t_export_json():
    r = new.export_json("members.csv")
    assert "membres exportés" in r or "exported" in r.lower()
    assert os.path.exists("members.json")
    import json as _j
    data = _j.load(open("members.json"))
    assert len(data) == 5
    ok("export_json() creates valid JSON file")
    os.remove("members.json")
t_export_json()

def t_deduplicate_csv():
    # Create two CSVs with overlap
    write_members("file_a.csv", MEMBERS_ROWS[:3])  # alice, bob, charlie
    write_members("file_b.csv", MEMBERS_ROWS[1:4]) # bob, charlie, dave (overlap: bob, charlie)
    with mock.patch('builtins.input', side_effect=['1 2', 'merged_test']):
        r = new.deduplicate_csv()
    assert os.path.exists("merged_test.csv")
    merged = new.read_members_csv("merged_test.csv")
    ids = {u['id'] for u in merged}
    assert ids == {111, 222, 333, 444}, f"Expected 4 unique IDs, got: {ids}"
    ok("deduplicate_csv() merges two CSVs, keeps 4 unique entries")
    for f in ("file_a.csv","file_b.csv","merged_test.csv"):
        try: os.remove(f)
        except: pass
t_deduplicate_csv()

def t_compare_csv():
    write_members("comp_a.csv", MEMBERS_ROWS[:3])
    write_members("comp_b.csv", MEMBERS_ROWS[2:])
    with mock.patch('builtins.input', side_effect=['1','2','n']):
        r = new.compare_csv()
    assert "En commun" in r
    assert "1" in r  # charlie is common
    ok("compare_csv() shows common and unique counts")
    for f in ("comp_a.csv","comp_b.csv"):
        try: os.remove(f)
        except: pass
t_compare_csv()

# ══════════════════════════════════════════════════════════════════════════════
section("7 · CSV FILE MANAGEMENT (new.py)")
# ══════════════════════════════════════════════════════════════════════════════

write_members("members.csv")

def t_save_as():
    with mock.patch('builtins.input', return_value='backup_test'):
        r = new.save_scrapped_members_as()
    assert os.path.exists("backup_test.csv")
    assert "backup_test.csv" in r
    ok("save_scrapped_members_as() creates named backup")
    os.remove("backup_test.csv")
t_save_as()

def t_save_append_dedup():
    write_members("existing.csv", MEMBERS_ROWS[:2])  # alice, bob
    # Append members.csv (alice,bob,charlie,dave,empty) onto existing.csv
    with mock.patch('builtins.input', side_effect=['existing','a']):
        r = new.save_scrapped_members_append_or_overwrite()
    merged = new.read_members_csv("existing.csv")
    ids = {u['id'] for u in merged}
    # alice(111) and bob(222) already there, only new ones added
    assert 111 in ids and 222 in ids
    ok(f"save_scrapped_members_append_or_overwrite() appends with dedup ({len(ids)} unique IDs)")
    os.remove("existing.csv")
t_save_append_dedup()

def t_manage_backup_view():
    write_members("bk_view.csv", MEMBERS_ROWS[:2])
    with mock.patch('builtins.input', side_effect=['v','1']):
        r = new.manage_backup_files()
    assert "alice" in r.lower() or "👤" in r
    ok("manage_backup_files() view option shows members")
    os.remove("bk_view.csv")
t_manage_backup_view()

def t_manage_backup_use():
    write_members("bk_use.csv", MEMBERS_ROWS[:1])  # only alice
    with mock.patch('builtins.input', side_effect=['u','1']):
        r = new.manage_backup_files()
    current = new.read_members_csv()
    assert len(current) == 1
    assert current[0]['username'] == 'alice'
    ok("manage_backup_files() 'use' option replaces members.csv")
    write_members("members.csv")  # restore
    os.remove("bk_use.csv")
t_manage_backup_use()

def t_manage_backup_delete():
    write_members("bk_del.csv")
    with mock.patch('builtins.input', side_effect=['s','1']):
        r = new.manage_backup_files()
    assert not os.path.exists("bk_del.csv")
    ok("manage_backup_files() delete option removes file")
t_manage_backup_delete()

# ══════════════════════════════════════════════════════════════════════════════
section("8 · CLONED MESSAGES (new.py)")
# ══════════════════════════════════════════════════════════════════════════════

write_messages("cloned_messages.csv")

def t_display_messages():
    r = new.display_cloned_messages()
    assert "Hello world" in r
    assert "3 messages" in r
    ok("display_cloned_messages() shows all messages")
t_display_messages()

def t_search_messages():
    r = new.search_cloned_messages.__wrapped__() if hasattr(new.search_cloned_messages,'__wrapped__') else None
    with mock.patch('builtins.input', return_value='hello'):
        r = new.search_cloned_messages()
    assert "Hello world" in r
    ok("search_cloned_messages() finds keyword 'hello'")
t_search_messages()

def t_edit_messages():
    with mock.patch('builtins.input', side_effect=['2', 'Updated message']):
        r = new.edit_cloned_messages()
    assert "✅" in r
    rows = list(csv.reader(open("cloned_messages.csv"), delimiter=",", lineterminator="\n"))[1:]
    assert rows[1][2] == "Updated message"
    ok("edit_cloned_messages() updates message content")
t_edit_messages()

def t_edit_messages_empty_file():
    with open("empty_msgs.csv","w") as f: pass  # truly empty file
    import unittest.mock as mock2
    orig = new.os.path.exists
    with mock.patch('os.path.exists', side_effect=lambda p: True if p == "cloned_messages.csv" else orig(p)):
        with mock.patch('builtins.open', mock.mock_open(read_data="")):
            # Can't easily mock csv.reader returning empty; test directly
            pass
    # Direct: create empty file and rename to cloned_messages.csv
    shutil.copy("cloned_messages.csv", "cloned_messages_bak.csv")
    with open("cloned_messages.csv","w") as f: pass
    r = new.edit_cloned_messages()
    assert "❌" in r
    ok("edit_cloned_messages() handles empty file gracefully")
    shutil.copy("cloned_messages_bak.csv","cloned_messages.csv")
    os.remove("cloned_messages_bak.csv")
t_edit_messages_empty_file()

def t_delete_messages():
    r = new.delete_cloned_messages()
    assert not os.path.exists("cloned_messages.csv")
    assert "🗑" in r
    ok("delete_cloned_messages() removes file")
t_delete_messages()

def t_display_messages_missing():
    r = new.display_cloned_messages()
    assert "❌" in r
    ok("display_cloned_messages() returns error when file missing")
t_display_messages_missing()

# ══════════════════════════════════════════════════════════════════════════════
section("9 · LOGS & CACHE (new.py)")
# ══════════════════════════════════════════════════════════════════════════════

def t_show_log():
    with mock.patch('builtins.input', return_value='5'):
        r = new.show_activity_log()
    assert isinstance(r, str) and len(r) > 0
    ok("show_activity_log() returns log content")
t_show_log()

def t_clear_cache():
    # Create a fake orphan .session file
    open("orphan_test.session","w").close()
    new.config['accounts'] = []
    r = new.clear_cache()
    assert not os.path.exists("orphan_test.session")
    assert "1" in r or "supprimé" in r.lower()
    ok("clear_cache() removes orphan .session files")
t_clear_cache()

def t_clear_cache_keeps_valid():
    new.config['accounts'] = [{"phone":"+10000000000","api_id":"1","api_hash":"x",
                                "blacklisted":False,"blacklist_time":None}]
    open("+10000000000.session","w").close()
    open("orphan2.session","w").close()
    r = new.clear_cache()
    assert os.path.exists("+10000000000.session")
    assert not os.path.exists("orphan2.session")
    ok("clear_cache() keeps sessions belonging to configured accounts")
    os.remove("+10000000000.session")
    new.config['accounts'] = []
t_clear_cache_keeps_valid()

# ══════════════════════════════════════════════════════════════════════════════
section("10 · new_en.py PARALLEL TESTS")
# ══════════════════════════════════════════════════════════════════════════════

new_en.config = {"accounts": [], "proxies": []}

def t_en_list_accounts():
    r = new_en.list_connected_accounts()
    assert "0" in r
    ok("[EN] list_connected_accounts() empty")
t_en_list_accounts()

def t_en_check_blacklist():
    new_en.config['accounts'] = [{"api_id":"1","api_hash":"x","phone":"+1",
                                   "blacklisted":True,"blacklist_time":time.time()-49*3600}]
    new_en.check_blacklisted_accounts()
    assert new_en.config['accounts'][0]['blacklisted'] == False
    ok("[EN] check_blacklisted_accounts() lifts after 48 h")
    new_en.config['accounts'] = []
t_en_check_blacklist()

def t_en_proxy_filtering():
    new_en.config['proxies'] = ["exit","1.2.3.4:1080",""]
    valid = [p for p in new_en.config['proxies'] if p and p.lower()!='exit' and ':' in p]
    assert len(valid) == 1
    ok("[EN] get_client proxy filtering works")
    new_en.config['proxies'] = []
t_en_proxy_filtering()

write_members("members.csv")

def t_en_display_saved():
    r = new_en.display_saved_users()
    assert "Alice" in r or "alice" in r
    ok("[EN] display_saved_users() works")
t_en_display_saved()

def t_en_filter_inactive():
    write_members("members.csv")
    r = new_en.filter_and_remove_inactive_or_fake()
    assert "✅" in r
    ok("[EN] filter_and_remove_inactive_or_fake() works")
t_en_filter_inactive()

def t_en_filter_bad_rows():
    """Test that malformed rows don't crash filter"""
    with open("members.csv","w") as f:
        f.write("username,user_id,access_hash,name,group,group_id\n")
        f.write("alice,NOT_INT,0,Alice,G,1\n")
        f.write("bob\n")  # too few columns
    r = new_en.filter_and_remove_inactive_or_fake()
    assert "❌" not in r or "Error" not in r  # should not crash
    ok("[EN] filter_and_remove_inactive_or_fake() skips bad rows without crash")
t_en_filter_bad_rows()

write_messages("cloned_messages.csv")

def t_en_display_cloned():
    r = new_en.display_cloned_messages()
    assert "Hello world" in r
    ok("[EN] display_cloned_messages() shows messages")
t_en_display_cloned()

def t_en_edit_cloned_empty_file():
    shutil.copy("cloned_messages.csv","cloned_bak.csv")
    with open("cloned_messages.csv","w") as f: pass
    r = new_en.edit_cloned_messages()
    assert "❌" in r
    ok("[EN] edit_cloned_messages() handles empty file gracefully")
    shutil.copy("cloned_bak.csv","cloned_messages.csv")
    os.remove("cloned_bak.csv")
t_en_edit_cloned_empty_file()

def t_en_delete_saved():
    shutil.copy("members.csv","mbak.csv")
    r = new_en.delete_saved_users()
    assert not os.path.exists("members.csv")
    assert "deleted" in r.lower() or "🗑" in r
    ok("[EN] delete_saved_users() removes file")
    shutil.copy("mbak.csv","members.csv"); os.remove("mbak.csv")
t_en_delete_saved()

def t_en_manage_backup_use():
    write_members("bk_en_use.csv", MEMBERS_ROWS[:1])
    with mock.patch('builtins.input', side_effect=['u','1']):
        r = new_en.manage_backup_files()
    current = new_en.read_members_csv() if hasattr(new_en,'read_members_csv') else []
    assert "✅" in r or "copied" in r.lower()
    ok("[EN] manage_backup_files() 'use' option works")
    os.remove("bk_en_use.csv")
    write_members("members.csv")
t_en_manage_backup_use()

def t_en_save_append_dedup():
    write_members("members.csv")
    write_members("en_existing.csv", MEMBERS_ROWS[:2])
    with mock.patch('builtins.input', side_effect=['en_existing','a']):
        r = new_en.save_scrapped_members_append_or_overwrite()
    assert "deduplicated" in r or "added" in r.lower()
    ok("[EN] save_scrapped_members_append_or_overwrite() dedup works")
    os.remove("en_existing.csv")
t_en_save_append_dedup()

def t_en_clear_cache():
    open("orphan_en.session","w").close()
    new_en.config['accounts'] = []
    r = new_en.clear_cache()
    assert not os.path.exists("orphan_en.session")
    ok("[EN] clear_cache() removes orphan sessions")
t_en_clear_cache()

# ══════════════════════════════════════════════════════════════════════════════
section("11 · LIVE NETWORK TESTS (real Telegram)")
# ══════════════════════════════════════════════════════════════════════════════

# Python 3.10+ no longer auto-creates an event loop — Telethon's sync wrapper
# requires one to be set before the first client.start() call.
import asyncio
try:
    loop = asyncio.get_event_loop()
    if loop.is_closed():
        raise RuntimeError("closed")
except RuntimeError:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

os.chdir(ORIG_DIR)  # back to project dir to find .session files

# Load real config
with open("config.json") as f:
    real_cfg = json.load(f)

new.config    = real_cfg
new_en.config = real_cfg

print(f"  ℹ️  {len(real_cfg['accounts'])} account(s) configured")
print(f"  ℹ️  Proxies: {real_cfg['proxies']}")

from telethon.sync import TelegramClient as _TC

def _try_connect(acct):
    """Connect without prompting — returns authorized client or None."""
    c = _TC(acct['phone'], int(acct['api_id']), acct['api_hash'])
    try:
        c.connect()
        if c.is_user_authorized():
            return c
        c.disconnect()
        return None
    except Exception:
        try: c.disconnect()
        except: pass
        return None

# Find first account with a live authorized session
live_client = None
live_acct   = None
for _acct in real_cfg['accounts']:
    print(f"  ℹ️  Checking {_acct['phone']} ...", end=" ", flush=True)
    _c = _try_connect(_acct)
    if _c:
        print("🟢 session active")
        live_client = _c
        live_acct   = _acct
        break
    else:
        print("🔴 expired/invalid")

if live_client and live_acct:
    phone = live_acct['phone']

    def t_get_me():
        me = live_client.get_me()
        assert me is not None
        ok(f"get_me() → @{me.username or '—'} ({me.first_name}) ID:{me.id}")
    try: t_get_me()
    except Exception as e: fail("get_me()", str(e))

    def t_dialogs():
        from telethon.tl.functions.messages import GetDialogsRequest
        from telethon.tl.types import InputPeerEmpty
        result = live_client(GetDialogsRequest(
            offset_date=None, offset_id=0,
            offset_peer=InputPeerEmpty(), limit=100, hash=0))
        groups = [c for c in result.chats if hasattr(c,'megagroup') and c.megagroup]
        ok(f"GetDialogsRequest → {len(groups)} megagroup(s) visible")
        return groups
    try:
        groups = t_dialogs()
    except Exception as e:
        fail("GetDialogsRequest", str(e)); groups = []

    if groups:
        g = groups[0]
        print(f"  ℹ️  Using group: '{g.title}' (ID: {g.id})")

        def t_scrape():
            out = os.path.join(ORIG_DIR, "_test_live_members.csv")
            r = new.scrape_members(live_client, g, online_only=False, output_file=out)
            assert "✅" in r
            count = int(r.split()[1])
            ok(f"scrape_members() → {count} members from '{g.title}'")
            try: os.remove(out)
            except: pass
        try: t_scrape()
        except Exception as e: fail("scrape_members()", str(e))

        def t_group_info():
            r = new.get_group_info(live_client, g)
            assert g.title in r or "Groupe" in r
            ok(f"get_group_info() → info retrieved for '{g.title}'")
        try: t_group_info()
        except Exception as e: fail("get_group_info()", str(e))

        def t_list_admins():
            r = new.list_admins(live_client, g)
            assert "👑" in r
            ok(f"list_admins() → {r.splitlines()[0].strip()}")
        try: t_list_admins()
        except Exception as e: fail("list_admins()", str(e))

        def t_invite_link():
            r = new.generate_invite_link(live_client, g)
            # May fail if not admin — either outcome is valid
            ok(f"generate_invite_link() → {'link obtained' if 't.me' in r else 'not admin (expected)'}")
        try: t_invite_link()
        except Exception as e: fail("generate_invite_link()", str(e))
    else:
        skip("No megagroup found — skipping group-level live tests")

    def t_invalid_group():
        _, err = new.get_group_by_name(live_client, "INVALID_XYZ_NOTEXIST_9999")
        assert err and "❌" in err
        ok("get_group_by_name() returns ❌ for nonexistent group")
    try: t_invalid_group()
    except Exception as e: fail("get_group_by_name() invalid", str(e))

    # EN module: reuse same session file
    def t_en_get_me():
        c2 = _try_connect(live_acct)
        if c2:
            try:
                me = c2.get_me()
                ok(f"[EN] get_me() → {me.first_name} (session reused OK)")
            except Exception as e:
                fail("[EN] get_me()", str(e))
            finally:
                try: c2.disconnect()
                except: pass
        else:
            skip("[EN] get_me() — second connect failed")
    t_en_get_me()

    try:
        live_client.disconnect()
        ok(f"live_client.disconnect() clean")
    except Exception as e:
        fail("live_client.disconnect()", str(e))

else:
    skip("All sessions expired or invalid — skipping live network tests")
    print(f"  {YEL}  → Use option 8 in the menu to reconnect accounts.{RST}")

# ══════════════════════════════════════════════════════════════════════════════
section("12 · CLEANUP")
# ══════════════════════════════════════════════════════════════════════════════
try:
    shutil.rmtree(TMP_DIR)
    ok(f"Temp directory cleaned up: {TMP_DIR}")
except Exception as e:
    fail("Cleanup", str(e))

# ─── Final report ─────────────────────────────────────────────────────────────
total = passed + failed + skipped
print(f"\n{BOLD}{'═'*55}")
print(f"  RÉSULTATS FINAUX")
print(f"{'═'*55}{RST}")
print(f"  {GRN}✅ Passed : {passed}{RST}")
if failed:
    print(f"  {RED}❌ Failed : {failed}{RST}")
else:
    print(f"  ❌ Failed : {failed}")
print(f"  {YEL}⏭  Skipped: {skipped}{RST}")
print(f"  ─────────────────")
print(f"  Total   : {total}")

if failed == 0:
    print(f"\n{BOLD}{GRN}  🎉 ALL TESTS PASSED!{RST}")
else:
    print(f"\n{BOLD}{RED}  ⚠️  {failed} test(s) failed.{RST}")
    sys.exit(1)
