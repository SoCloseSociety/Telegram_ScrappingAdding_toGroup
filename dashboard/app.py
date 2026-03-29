"""
Telegram Manager — Dashboard (FastAPI + HTMX + SSE)
Run with: uvicorn dashboard.app:app --reload --port 8000
"""
import os, sys, json, csv, asyncio, queue, threading, uuid, time, io, shutil, hashlib, secrets
from pathlib import Path
from urllib.parse import urlencode
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from functools import wraps

from fastapi import FastAPI, Request, Form, Query, Response, Cookie, Depends
from fastapi.responses import HTMLResponse, StreamingResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware

# ── Point working dir to project root ─────────────────────────────────────────
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

import new as nm  # noqa: E402
import automation as auto  # noqa: E402

# ── App setup ─────────────────────────────────────────────────────────────────
app = FastAPI(title="Telegram Manager Dashboard")
DASH = Path(__file__).parent
templates = Jinja2Templates(directory=str(DASH / "templates"))
app.mount("/static", StaticFiles(directory=str(DASH / "static")), name="static")

executor = ThreadPoolExecutor(max_workers=4)


# ═══════════════════════════════════════════════════════════════════════════════
#  AUTH SYSTEM — session-based login
# ═══════════════════════════════════════════════════════════════════════════════

AUTH_FILE = ROOT / "auth.json"
SESSION_COOKIE = "tgm_session"
SESSION_MAX_AGE = 86400 * 7  # 7 days

# In-memory session store: {token: {user, expires, ip, created}}
_sessions: dict[str, dict] = {}


def _hash_password(password: str, salt: str = "") -> str:
    if not salt:
        salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 100000)
    return f"{salt}:{h.hex()}"


def _verify_password(password: str, stored: str) -> bool:
    try:
        salt, hashed = stored.split(':', 1)
        return _hash_password(password, salt) == stored
    except Exception:
        return False


def _load_auth() -> dict:
    if AUTH_FILE.exists():
        try:
            return json.loads(AUTH_FILE.read_text())
        except Exception:
            pass
    return {"users": []}


def _save_auth(data: dict):
    AUTH_FILE.write_text(json.dumps(data, indent=2))


def _init_auth():
    """Create default admin account if no users exist."""
    auth = _load_auth()
    if not auth.get("users"):
        default_pass = secrets.token_urlsafe(12)
        auth["users"] = [{
            "username": "admin",
            "password": _hash_password(default_pass),
            "role": "admin",
            "created": datetime.now().isoformat(),
        }]
        _save_auth(auth)
        # Write password to a temp file for first login
        cred_file = ROOT / ".default_credentials"
        cred_file.write_text(f"Username: admin\nPassword: {default_pass}\n")
        print(f"\n  🔑 Default credentials saved to .default_credentials")
        print(f"     Username: admin")
        print(f"     Password: {default_pass}")
        print(f"     ⚠️  Change this after first login!\n")


_init_auth()


def _create_session(username: str, ip: str = "") -> str:
    token = secrets.token_urlsafe(32)
    _sessions[token] = {
        "user": username,
        "expires": (datetime.now() + timedelta(seconds=SESSION_MAX_AGE)).isoformat(),
        "ip": ip,
        "created": datetime.now().isoformat(),
    }
    # Cleanup expired sessions
    now = datetime.now().isoformat()
    expired = [k for k, v in _sessions.items() if v["expires"] < now]
    for k in expired:
        del _sessions[k]
    return token


def _get_session(token: str) -> dict | None:
    if not token or token not in _sessions:
        return None
    session = _sessions[token]
    if session["expires"] < datetime.now().isoformat():
        del _sessions[token]
        return None
    return session


def _destroy_session(token: str):
    _sessions.pop(token, None)


# ── Auth middleware — protect all routes except /login ────────────────────────

PUBLIC_PATHS = {"/login", "/static"}


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        # Allow public paths
        if path == "/login" or path.startswith("/static"):
            return await call_next(request)
        # Check session cookie
        token = request.cookies.get(SESSION_COOKIE)
        session = _get_session(token)
        if not session:
            # SSE and API endpoints → 401 JSON
            if path.endswith("/stream") or path.startswith("/api/"):
                from fastapi.responses import JSONResponse
                return JSONResponse({"error": "unauthorized"}, status_code=401)
            # Everything else → redirect to login
            return RedirectResponse("/login", status_code=303)
        # Attach user to request state
        request.state.user = session["user"]
        return await call_next(request)


app.add_middleware(AuthMiddleware)


# ── Login/Logout routes ──────────────────────────────────────────────────────

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, error: str = ""):
    return templates.TemplateResponse(request, "login.html", {"error": error})


@app.post("/login")
async def login_submit(request: Request, username: str = Form(...), password: str = Form(...)):
    auth = _load_auth()
    for user in auth.get("users", []):
        if user["username"] == username and _verify_password(password, user["password"]):
            token = _create_session(username, request.client.host if request.client else "")
            response = RedirectResponse("/?toast=Bienvenue+!&tt=success", status_code=303)
            response.set_cookie(
                SESSION_COOKIE, token,
                max_age=SESSION_MAX_AGE,
                httponly=True,
                samesite="lax",
            )
            return response
    return RedirectResponse("/login?error=Identifiants+incorrects", status_code=303)


@app.get("/logout")
async def logout(request: Request):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        _destroy_session(token)
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE)
    return response


# ── Settings page — change password, manage users ─────────────────────────────

@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    ctx = _ctx()
    auth = _load_auth()
    users = [{"username": u["username"], "role": u.get("role", "user"),
              "created": u.get("created", "?")} for u in auth.get("users", [])]
    return templates.TemplateResponse(request, "settings.html", {
        **ctx, "auth_users": users, "current_user": request.state.user,
    })


@app.post("/settings/change-password")
async def change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
):
    if new_password != confirm_password:
        return _redirect("/settings", "Les mots de passe ne correspondent pas", "error")
    if len(new_password) < 6:
        return _redirect("/settings", "Minimum 6 caractères", "error")

    auth = _load_auth()
    username = request.state.user
    for user in auth["users"]:
        if user["username"] == username:
            if not _verify_password(current_password, user["password"]):
                return _redirect("/settings", "Mot de passe actuel incorrect", "error")
            user["password"] = _hash_password(new_password)
            _save_auth(auth)
            # Remove default credentials file if it exists
            cred_file = ROOT / ".default_credentials"
            if cred_file.exists():
                cred_file.unlink()
            return _redirect("/settings", "Mot de passe changé !")
    return _redirect("/settings", "Utilisateur introuvable", "error")


@app.post("/settings/add-user")
async def add_user(
    request: Request,
    new_username: str = Form(...),
    new_user_password: str = Form(...),
):
    if len(new_user_password) < 6:
        return _redirect("/settings", "Minimum 6 caractères", "error")
    auth = _load_auth()
    if any(u["username"] == new_username for u in auth["users"]):
        return _redirect("/settings", f"L'utilisateur '{new_username}' existe déjà", "error")
    auth["users"].append({
        "username": new_username,
        "password": _hash_password(new_user_password),
        "role": "user",
        "created": datetime.now().isoformat(),
    })
    _save_auth(auth)
    return _redirect("/settings", f"Utilisateur '{new_username}' créé")


@app.post("/settings/delete-user/{username}")
async def delete_user(request: Request, username: str):
    if username == request.state.user:
        return _redirect("/settings", "Impossible de supprimer votre propre compte", "error")
    auth = _load_auth()
    auth["users"] = [u for u in auth["users"] if u["username"] != username]
    _save_auth(auth)
    return _redirect("/settings", f"Utilisateur '{username}' supprimé")

# ── Jobs store ────────────────────────────────────────────────────────────────
_jobs: dict[str, dict] = {}
_MAX_JOBS = 50
GROUPS_CACHE = ROOT / "groups_cache.json"


def _new_job() -> str:
    jid = str(uuid.uuid4())[:8]
    _jobs[jid] = {"status": "running", "lines": [], "q": queue.Queue(),
                  "title": "", "created": time.time()}
    if len(_jobs) > _MAX_JOBS:
        finished = sorted(
            [(k, v) for k, v in _jobs.items() if v["status"] != "running"],
            key=lambda x: x[1].get("created", 0),
        )
        for k, _ in finished[:len(_jobs) - _MAX_JOBS]:
            del _jobs[k]
    return jid


def _run_in_job(jid: str, fn, *args, title: str = "", **kwargs):
    job = _jobs[jid]
    job["title"] = title

    class _Cap(io.StringIO):
        def write(self, text):
            stripped = text.rstrip()
            if stripped:
                job["q"].put(("line", stripped))
                job["lines"].append(stripped)
        def flush(self):
            pass

    def _worker():
        # Each thread needs its own asyncio event loop for Telethon
        import asyncio as _aio
        try:
            _aio.get_event_loop()
        except RuntimeError:
            _aio.set_event_loop(_aio.new_event_loop())

        old = sys.stdout
        sys.stdout = _Cap()
        try:
            result = fn(*args, **kwargs)
            if result:
                for ln in str(result).splitlines():
                    if ln.strip():
                        job["q"].put(("line", ln))
                        job["lines"].append(ln)
            job["status"] = "done"
        except Exception as exc:
            msg = f"❌ Erreur: {exc}"
            job["q"].put(("line", msg))
            job["lines"].append(msg)
            job["status"] = "error"
        finally:
            sys.stdout = old
            job["q"].put(("done", job["status"]))

    threading.Thread(target=_worker, daemon=True).start()


async def _sse_gen(jid: str):
    if jid not in _jobs:
        yield 'data: {"t":"error","msg":"Job introuvable"}\n\n'
        return
    job = _jobs[jid]
    loop = asyncio.get_event_loop()
    while True:
        try:
            kind, payload = await loop.run_in_executor(
                executor, lambda: job["q"].get(timeout=30)
            )
            if kind == "done":
                yield f'data: {{"t":"done","status":"{payload}"}}\n\n'
                return
            safe = json.dumps(payload)
            yield f'data: {{"t":"line","msg":{safe}}}\n\n'
        except Exception:
            if job["status"] in ("done", "error"):
                yield f'data: {{"t":"done","status":"{job["status"]}"}}\n\n'
                return
            yield 'data: {"t":"ping"}\n\n'


# ── Helpers ───────────────────────────────────────────────────────────────────

def _reload_config():
    if os.path.exists(nm.CONFIG_FILE):
        with open(nm.CONFIG_FILE) as f:
            data = json.load(f)
        nm.config.clear()
        nm.config.update(data)
        # Keep automation engine in sync
        auto.config.clear()
        auto.config.update(data)


def _ctx() -> dict:
    _reload_config()
    accts = nm.config.get("accounts", [])
    total = len(accts)
    active = sum(1 for a in accts if not a.get("blacklisted"))
    proxies = len(nm.config.get("proxies", []))
    members = 0
    if os.path.exists("members.csv"):
        try:
            with open("members.csv", encoding="utf-8") as f:
                members = max(0, sum(1 for _ in f) - 1)
        except Exception:
            pass
    return {
        "total_accounts": total, "active_accounts": active,
        "blacklisted_accounts": total - active,
        "proxies": proxies, "members": members,
    }


def _redirect(path: str, toast: str = "", tt: str = "success"):
    if toast:
        sep = "&" if "?" in path else "?"
        path += sep + urlencode({"toast": toast, "tt": tt})
    return RedirectResponse(path, status_code=303)


def _safe_csv_path(fname: str):
    if '/' in fname or '\\' in fname or '..' in fname:
        return None
    if not fname.endswith('.csv'):
        return None
    path = ROOT / fname
    if path.resolve().parent != ROOT.resolve():
        return None
    return path


def _read_csv_safe(path, max_rows=200):
    header, rows = [], []
    try:
        with open(path, encoding="utf-8") as f:
            reader = list(csv.reader(f))
        if reader:
            header = reader[0]
            rows = reader[1:max_rows + 1]
    except Exception:
        pass
    return header, rows


# ═══════════════════════════════════════════════════════════════════════════════
#  ROUTES: Overview
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    ctx = _ctx()
    log_lines = []
    if os.path.exists(nm.LOG_FILE):
        with open(nm.LOG_FILE, encoding="utf-8", errors="replace") as f:
            raw = f.readlines()[-15:]
        log_lines = [ln.strip() for ln in reversed(raw)]
    # Running jobs count
    running = sum(1 for j in _jobs.values() if j["status"] == "running")
    return templates.TemplateResponse(request, "index.html", {
        **ctx, "log_lines": log_lines, "running_jobs": running,
    })


@app.get("/api/stats", response_class=HTMLResponse)
async def api_stats():
    ctx = _ctx()
    running = sum(1 for j in _jobs.values() if j["status"] == "running")
    return HTMLResponse(f"""
    <div class="card p-5"><div class="text-xs font-semibold text-muted mb-2">COMPTES ACTIFS</div>
    <div class="text-3xl text-blue">{ctx['active_accounts']}</div>
    <div class="text-xs mt-2 text-muted">/ {ctx['total_accounts']} total</div></div>
    <div class="card p-5"><div class="text-xs font-semibold text-muted mb-2">MEMBRES CSV</div>
    <div class="text-3xl text-green">{ctx['members']}</div>
    <div class="text-xs mt-2 text-muted">dans members.csv</div></div>
    <div class="card p-5"><div class="text-xs font-semibold text-muted mb-2">PROXIES</div>
    <div class="text-3xl" style="color:#a78bfa;">{ctx['proxies']}</div>
    <div class="text-xs mt-2 text-muted">configurés</div></div>
    <div class="card p-5"><div class="text-xs font-semibold text-muted mb-2">JOBS ACTIFS</div>
    <div class="text-3xl" style="color:{'#4ade80' if running == 0 else '#fbbf24'}">{running}</div>
    <div class="text-xs mt-2 text-muted">en cours</div></div>
    """)


# ═══════════════════════════════════════════════════════════════════════════════
#  ROUTES: Accounts
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/accounts", response_class=HTMLResponse)
async def accounts_page(request: Request):
    ctx = _ctx()
    accts = nm.config.get("accounts", [])
    annotated = []
    for i, a in enumerate(accts):
        phone = a.get("phone", "?")
        has_session = os.path.exists(f"{phone}.session")
        annotated.append({**a, "idx": i, "has_session": has_session})
    return templates.TemplateResponse(request, "accounts.html", {
        **ctx, "accounts": annotated,
    })


@app.post("/accounts/{idx}/blacklist")
async def blacklist_account(idx: int):
    try:
        await asyncio.get_event_loop().run_in_executor(executor, nm.blacklist_account, idx)
    except (IndexError, KeyError):
        pass
    return _redirect("/accounts", "Compte blacklisté 48h")


@app.post("/accounts/{idx}/unblacklist")
async def unblacklist_account(idx: int):
    try:
        nm.config["accounts"][idx]["blacklisted"] = False
        nm.save_config()
    except (IndexError, KeyError):
        pass
    return _redirect("/accounts", "Compte débloqué")


@app.post("/accounts/{idx}/delete")
async def delete_account(idx: int):
    try:
        phone = nm.config["accounts"][idx].get("phone", "")
        nm.config["accounts"].pop(idx)
        nm.save_config()
        # Remove session file
        sess = ROOT / f"{phone}.session"
        if sess.exists():
            sess.unlink()
    except (IndexError, KeyError):
        pass
    return _redirect("/accounts", "Compte supprimé")


@app.post("/accounts/{idx}/reconnect")
async def reconnect_account_route(idx: int):
    jid = _new_job()
    _run_in_job(jid, nm.reconnect_account, idx, title=f"Reconnexion #{idx+1}")
    return _redirect(f"/jobs/{jid}")


@app.post("/accounts/reconnect-all")
async def reconnect_all():
    jid = _new_job()
    _run_in_job(jid, nm.reconnect_all_accounts, title="Reconnexion tous")
    return _redirect(f"/jobs/{jid}")


@app.post("/accounts/check-all")
async def check_all():
    jid = _new_job()
    _run_in_job(jid, nm.check_all_accounts_restrictions, title="Vérification comptes")
    return _redirect(f"/jobs/{jid}")


# ═══════════════════════════════════════════════════════════════════════════════
#  ROUTES: Proxies
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/proxies", response_class=HTMLResponse)
async def proxies_page(request: Request):
    ctx = _ctx()
    proxy_list = nm.config.get("proxies", [])
    return templates.TemplateResponse(request, "proxies.html", {
        **ctx, "proxy_list": proxy_list,
    })


@app.post("/proxies/add")
async def add_proxy(proxy: str = Form(...)):
    proxy = proxy.strip()
    parts = proxy.split(':')
    if len(parts) not in (2, 4):
        return _redirect("/proxies", "Format invalide (ip:port ou ip:port:user:pass)", "error")
    try:
        int(parts[1])
    except ValueError:
        return _redirect("/proxies", "Le port doit être un entier", "error")
    if proxy in nm.config.get("proxies", []):
        return _redirect("/proxies", "Proxy déjà présent", "warning")
    nm.config.setdefault("proxies", []).append(proxy)
    nm.save_config()
    return _redirect("/proxies", f"Proxy {proxy} ajouté")


@app.post("/proxies/{idx}/delete")
async def delete_proxy(idx: int):
    try:
        removed = nm.config["proxies"].pop(idx)
        nm.save_config()
        return _redirect("/proxies", f"Proxy {removed} supprimé")
    except (IndexError, KeyError):
        return _redirect("/proxies", "Index invalide", "error")


@app.post("/proxies/test-all")
async def test_all_proxies():
    jid = _new_job()
    _run_in_job(jid, nm.test_all_proxies, title="Test de tous les proxies")
    return _redirect(f"/jobs/{jid}")


# ═══════════════════════════════════════════════════════════════════════════════
#  ROUTES: Members
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/members", response_class=HTMLResponse)
async def members_page(request: Request, q: str = ""):
    ctx = _ctx()
    header, rows = _read_csv_safe("members.csv", max_rows=200)
    if q and rows:
        ql = q.lower()
        rows = [r for r in rows if any(ql in (c or '').lower() for c in r)]
    csv_files = sorted(
        [f for f in os.listdir(".") if f.endswith(".csv")],
        key=lambda x: os.path.getmtime(x), reverse=True,
    )
    return templates.TemplateResponse(request, "members.html", {
        **ctx, "header": header, "rows": rows[:100],
        "csv_files": csv_files, "q": q, "total_filtered": len(rows),
    })


@app.post("/members/scrape")
async def start_scrape(group: str = Form(...), online_only: str = Form("0")):
    jid = _new_job()
    only = online_only == "1"

    def _scrape():
        account, client = nm._active_client()
        if not account:
            return "❌ Aucun compte actif."
        try:
            g, err = nm.get_group_by_name(client, group)
            if err:
                return err
            return nm.scrape_members(client, g, online_only=only)
        finally:
            nm._disconnect(client)

    _run_in_job(jid, _scrape, title=f"Scrape: {group}")
    return _redirect(f"/jobs/{jid}")


@app.post("/members/add")
async def start_add(
    group: str = Form(...), csv_file: str = Form("members.csv"),
    num_members: str = Form(""), mode: str = Form("normal"),
):
    jid = _new_job()

    def _add():
        account, client = nm._active_client()
        if not account:
            return "❌ Aucun compte actif."
        try:
            g, err = nm.get_group_by_name(client, group)
            if err:
                return err
            nm._disconnect(client)
            return nm.add_members(g, input_file=csv_file,
                                   num_members=num_members or None, mode=mode, silent=True)
        except Exception:
            nm._disconnect(client)
            raise

    _run_in_job(jid, _add, title=f"Ajout → {group} ({mode})")
    return _redirect(f"/jobs/{jid}")


@app.post("/members/wave-add")
async def start_wave_add(
    group: str = Form(...), csv_file: str = Form("members.csv"),
    total: int = Form(20), per_wave: int = Form(5), hours: int = Form(5),
):
    jid = _new_job()

    def _wave():
        account, client = nm._active_client()
        if not account:
            return "❌ Aucun compte actif."
        try:
            g, err = nm.get_group_by_name(client, group)
            if err:
                return err
            nm._disconnect(client)
            return nm.wave_add(g, input_file=csv_file, total=total,
                               per_wave=per_wave, hours_between=hours, silent=True)
        except Exception:
            nm._disconnect(client)
            raise

    _run_in_job(jid, _wave, title=f"Vagues → {group} ({total} membres)")
    return _redirect(f"/jobs/{jid}")


@app.post("/members/filter")
async def filter_members():
    jid = _new_job()
    _run_in_job(jid, nm.filter_and_remove_inactive_or_fake, title="Filtrage bots/spam")
    return _redirect(f"/jobs/{jid}")


@app.post("/members/delete-csv")
async def delete_members_csv():
    await asyncio.get_event_loop().run_in_executor(executor, nm.delete_saved_users)
    return _redirect("/members", "members.csv supprimé")


@app.post("/members/stats")
async def members_stats():
    jid = _new_job()
    _run_in_job(jid, nm.member_stats, title="Statistiques membres")
    return _redirect(f"/jobs/{jid}")


# ═══════════════════════════════════════════════════════════════════════════════
#  ROUTES: Groups
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/groups", response_class=HTMLResponse)
async def groups_page(request: Request):
    ctx = _ctx()
    groups = []
    if GROUPS_CACHE.exists():
        try:
            groups = json.loads(GROUPS_CACHE.read_text())
        except Exception:
            pass
    return templates.TemplateResponse(request, "groups.html", {
        **ctx, "groups": groups,
    })


@app.post("/groups/list")
async def list_groups():
    jid = _new_job()

    def _list():
        from telethon.tl.types import InputPeerEmpty
        account, client = nm._active_client()
        if not account:
            return "❌ Aucun compte actif."
        try:
            dialogs = client.get_dialogs(limit=200)
            cache = []
            for d in dialogs:
                ent = d.entity
                if not (d.is_group or d.is_channel):
                    continue
                is_broadcast = getattr(ent, 'broadcast', False)
                count = getattr(ent, 'participants_count', 0) or 0
                creator = getattr(ent, 'creator', False)
                admin = getattr(ent, 'admin_rights', None) is not None
                gtype = 'canal' if is_broadcast else ('supergroupe' if getattr(ent, 'megagroup', False) else 'groupe')
                entry = {
                    "id": ent.id, "title": d.title,
                    "username": getattr(ent, 'username', None),
                    "members": count, "type": gtype,
                    "creator": creator, "admin": admin,
                    "can_add": not is_broadcast,
                }
                cache.append(entry)
                role = "👑" if creator else ("⭐" if admin else "👤")
                print(f"  {role} {d.title} — {count} membres ({gtype})")
            cache.sort(key=lambda x: x["members"], reverse=True)
            with open(GROUPS_CACHE, "w") as f:
                json.dump(cache, f)
            return f"✅ {len(cache)} groupes trouvés"
        finally:
            nm._disconnect(client)

    _run_in_job(jid, _list, title="Chargement des groupes")
    return _redirect(f"/jobs/{jid}")


@app.post("/groups/info")
async def group_info(group: str = Form(...)):
    jid = _new_job()

    def _info():
        account, client = nm._active_client()
        if not account:
            return "❌ Aucun compte actif."
        try:
            g, err = nm.get_group_by_name(client, group)
            if err:
                return err
            print(nm.get_group_info(client, g))
            print(nm.check_group_restrictions(client, g))
            print(nm.list_admins(client, g))
            return ""
        finally:
            nm._disconnect(client)

    _run_in_job(jid, _info, title=f"Info: {group}")
    return _redirect(f"/jobs/{jid}")


# ═══════════════════════════════════════════════════════════════════════════════
#  ROUTES: Messages
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/messages", response_class=HTMLResponse)
async def messages_page(request: Request, q: str = ""):
    ctx = _ctx()
    header, rows = [], []
    total = 0
    if os.path.exists("cloned_messages.csv"):
        header, all_rows = _read_csv_safe("cloned_messages.csv", max_rows=500)
        total = len(all_rows)
        if q:
            ql = q.lower()
            all_rows = [r for r in all_rows if any(ql in (c or '').lower() for c in r)]
        rows = all_rows[:100]
    return templates.TemplateResponse(request, "messages.html", {
        **ctx, "header": header, "rows": rows, "q": q,
        "total_messages": total, "filtered": len(rows),
    })


@app.post("/messages/clone")
async def clone_messages(group: str = Form(...)):
    jid = _new_job()

    def _clone():
        account, client = nm._active_client()
        if not account:
            return "❌ Aucun compte actif."
        try:
            g, err = nm.get_group_by_name(client, group)
            if err:
                return err
            return nm.clone_group_messages(client, g)
        finally:
            nm._disconnect(client)

    _run_in_job(jid, _clone, title=f"Clone: {group}")
    return _redirect(f"/jobs/{jid}")


@app.post("/messages/delete")
async def delete_messages():
    nm.delete_cloned_messages()
    return _redirect("/messages", "Messages clonés supprimés")


# ═══════════════════════════════════════════════════════════════════════════════
#  ROUTES: Jobs
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/jobs", response_class=HTMLResponse)
async def jobs_list(request: Request):
    ctx = _ctx()
    jobs_sorted = sorted(
        [{"jid": k, **v} for k, v in _jobs.items()],
        key=lambda x: x.get("created", 0), reverse=True,
    )
    # Don't pass queue objects to template
    for j in jobs_sorted:
        j.pop("q", None)
        j["created_fmt"] = time.strftime("%H:%M:%S", time.localtime(j.get("created", 0)))
        j["lines_count"] = len(j.get("lines", []))
    return templates.TemplateResponse(request, "jobs.html", {
        **ctx, "jobs_list": jobs_sorted,
    })


@app.get("/jobs/{jid}", response_class=HTMLResponse)
async def job_page(request: Request, jid: str):
    ctx = _ctx()
    job = _jobs.get(jid, {})
    return templates.TemplateResponse(request, "job.html", {
        **ctx, "jid": jid, "job": job,
    })


@app.get("/jobs/{jid}/stream")
async def job_stream(jid: str):
    return StreamingResponse(
        _sse_gen(jid), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  ROUTES: CSV files
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/csv", response_class=HTMLResponse)
async def csv_page(request: Request):
    ctx = _ctx()
    files = []
    for fname in sorted(
        [f for f in os.listdir(".") if f.endswith(".csv")],
        key=lambda x: os.path.getmtime(x), reverse=True,
    ):
        stat = os.stat(fname)
        rows = 0
        try:
            with open(fname, encoding="utf-8") as fp:
                rows = max(0, sum(1 for _ in fp) - 1)
        except Exception:
            pass
        files.append({
            "name": fname, "rows": rows,
            "size": f"{stat.st_size / 1024:.1f} KB",
            "mtime": time.strftime("%Y-%m-%d %H:%M", time.localtime(stat.st_mtime)),
        })
    return templates.TemplateResponse(request, "csv.html", {**ctx, "files": files})


@app.get("/csv/{fname}/preview", response_class=HTMLResponse)
async def csv_preview(request: Request, fname: str):
    ctx = _ctx()
    path = _safe_csv_path(fname)
    if not path or not path.exists():
        return _redirect("/csv", "Fichier introuvable", "error")
    header, rows = _read_csv_safe(path, max_rows=200)
    total = 0
    try:
        with open(path, encoding="utf-8") as f:
            total = max(0, sum(1 for _ in f) - 1)
    except Exception:
        pass
    stat = path.stat()
    return templates.TemplateResponse(request, "csv_preview.html", {
        **ctx, "fname": fname, "header": header, "rows": rows, "total": total,
        "size": f"{stat.st_size / 1024:.1f} KB",
        "mtime": time.strftime("%Y-%m-%d %H:%M", time.localtime(stat.st_mtime)),
    })


@app.post("/csv/{fname}/use-as-members")
async def use_as_members(fname: str):
    src = _safe_csv_path(fname)
    if not src or not src.exists():
        return _redirect("/csv", "Fichier introuvable", "error")
    shutil.copy(src, ROOT / "members.csv")
    return _redirect("/csv", f"{fname} → members.csv")


@app.post("/csv/{fname}/delete")
async def delete_csv(fname: str):
    target = _safe_csv_path(fname)
    if target and target.exists() and fname != 'members.csv':
        target.unlink()
        return _redirect("/csv", f"{fname} supprimé")
    return _redirect("/csv", "Impossible de supprimer", "error")


# ═══════════════════════════════════════════════════════════════════════════════
#  ROUTES: Logs
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/logs", response_class=HTMLResponse)
async def logs_page(request: Request, n: int = 200):
    ctx = _ctx()
    lines = []
    if os.path.exists(nm.LOG_FILE):
        with open(nm.LOG_FILE, encoding="utf-8", errors="replace") as f:
            raw = f.readlines()[-n:]
        lines = [ln.strip() for ln in reversed(raw)]
    return templates.TemplateResponse(request, "logs.html", {**ctx, "lines": lines})


# ═══════════════════════════════════════════════════════════════════════════════
#  ROUTES: Campaigns (automation)
# ═══════════════════════════════════════════════════════════════════════════════

def _load_templates():
    tpl_path = ROOT / "campaign_templates.json"
    if tpl_path.exists():
        try:
            data = json.loads(tpl_path.read_text())
            return data.get("templates", [])
        except Exception:
            pass
    return []


@app.get("/campaigns", response_class=HTMLResponse)
async def campaigns_page(request: Request):
    ctx = _ctx()
    campaigns = auto.list_campaigns()
    camp_list = []
    for c in campaigns:
        cid, name, target, status = c[0], c[1], c[2], c[3]
        total, offset, added = c[4], c[5], c[6]
        already, privacy, errors = c[7], c[8], c[9]
        per_wave, hours, daily = c[10], c[11], c[12]
        last_run, source = c[13], c[14]
        pct = (added / total * 100) if total > 0 else 0
        processed = added + already + privacy + errors
        camp_list.append({
            "id": cid, "name": name, "target": target, "status": status,
            "total": total, "offset": offset, "added": added,
            "already": already, "privacy": privacy, "errors": errors,
            "per_wave": per_wave, "hours": hours, "daily": daily,
            "last_run": last_run, "source": source,
            "pct": pct, "processed": processed,
        })
    csv_files = sorted([f for f in os.listdir(".") if f.endswith(".csv")])
    # Running campaign jobs
    running_ids = set()
    for j in _jobs.values():
        if j["status"] == "running" and "Campagne:" in j.get("title", ""):
            running_ids.add(j["title"].split(":")[-1].strip())
    tpls = _load_templates()
    return templates.TemplateResponse(request, "campaigns.html", {
        **ctx, "campaigns": camp_list, "csv_files": csv_files,
        "running_ids": running_ids, "templates": tpls,
    })


@app.post("/campaigns/create")
async def create_campaign_route(
    name: str = Form(...), group: str = Form(...),
    csv_file: str = Form("members.csv"), total: int = Form(0),
    per_wave: int = Form(5), hours: float = Form(5),
    daily_limit: int = Form(15),
):
    jid = _new_job()

    def _create():
        gid, title, ahash, err = auto._resolve_group(group)
        if err:
            return err
        members = auto.read_members_csv(csv_file)
        actual_total = total if total > 0 else len(members)
        cid = auto.create_campaign(
            name, gid, title, ahash,
            source_csv=csv_file, total=actual_total,
            per_wave=per_wave, hours_between=hours,
            daily_limit=daily_limit,
        )
        return f"✅ Campagne créée: {name}\n   ID: {cid}\n   Cible: {title}\n   {actual_total} membres à ajouter"

    _run_in_job(jid, _create, title=f"Création campagne: {name}")
    return _redirect(f"/jobs/{jid}")


@app.post("/campaigns/{cid}/start")
async def start_campaign(cid: str):
    campaign = auto.get_campaign(cid)
    if not campaign:
        return _redirect("/campaigns", "Campagne introuvable", "error")

    jid = _new_job()

    def _run():
        return auto.run_campaign(cid)

    _run_in_job(jid, _run, title=f"Campagne: {campaign['name']}")
    return _redirect(f"/jobs/{jid}")


@app.post("/campaigns/{cid}/pause")
async def pause_campaign(cid: str):
    auto.update_campaign(cid, status='paused', next_run=None)
    return _redirect("/campaigns", "Campagne mise en pause")


@app.post("/campaigns/{cid}/delete")
async def delete_campaign_route(cid: str):
    campaign = auto.get_campaign(cid)
    name = campaign['name'] if campaign else cid
    auto.delete_campaign(cid)
    return _redirect("/campaigns", f"Campagne '{name}' supprimée")


@app.get("/campaigns/{cid}/log", response_class=HTMLResponse)
async def campaign_log_page(request: Request, cid: str):
    ctx = _ctx()
    campaign = auto.get_campaign(cid)
    if not campaign:
        return _redirect("/campaigns", "Campagne introuvable", "error")
    logs = auto.campaign_add_log(cid, limit=200)
    log_list = []
    for row in logs:
        uid, uname, uname2, phone, status, err, ts = row
        log_list.append({
            "user_id": uid, "username": uname, "name": uname2,
            "phone": phone, "status": status, "error": err,
            "time": ts[11:19] if ts else '?',
        })
    # Account status for this campaign
    mgr = auto.AccountManager(cid)
    acct_status = mgr.status_report()
    return templates.TemplateResponse(request, "campaign_log.html", {
        **ctx, "campaign": campaign, "logs": log_list,
        "acct_status": acct_status,
    })
