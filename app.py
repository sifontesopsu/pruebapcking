import os
import faulthandler

try:
    faulthandler.enable()
except Exception:
    pass
import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
import sqlite3
from datetime import datetime
import re
import html
import json
import math
import random
import string
import requests
import time
import uuid
import hashlib
import threading
from contextlib import contextmanager
# =========================
# CONFIG
# =========================
DB_NAME = "aurora_ml_PRUEBAS_SIN_SHEETS.db"  # Aislada de la base operativa
ADMIN_PASSWORD = "aurora123"  # cambia si quieres
NUM_MESAS = 4

# MODO PRUEBAS: Sheets completamente desconectado.
# Bandera fija: no se puede reactivar mediante variables de entorno ni st.secrets.
# Esta copia guarda los datos en SQLite local, sin crear cola de eventos remotos.
PICKING_SHEETS_BACKUP_ENABLED = False
DEFAULT_PICKING_SHEETS_WEBHOOK_URL = ""  # Se omite el endpoint de producción
PICKING_BACKUP_MAX_ATTEMPTS = 8
PICKING_BACKUP_WORKER_LIMIT = 120
PICKING_SNAPSHOT_MAX_JSON_BYTES = 30000
_PICKING_BACKUP_THREAD_LOCK = threading.Lock()
_PICKING_BACKUP_THREAD_RUNNING = False


# =========================
# TABLAS POR MÓDULO (para respaldo parcial)
# =========================
PICKING_TABLES = [
    "orders",
    "order_items",
    "pickers",
    "picking_ots",
    "picking_tasks",
    "picking_incidences",
    "cortes_tasks",
    "ot_orders",
    # Documentos históricos locales: incluirlos en el respaldo manual.
    "despacho_tandas",
    "despacho_ventas",
    "despacho_etiquetas",
    "despacho_etiquetas_pdf",
]

# Maestro SKU/EAN en la misma carpeta que app.py
MASTER_FILE = "maestro_sku_ean.xlsx"



# Maestro de SKUs para CORTES (rollos / corte manual)
CORTES_FILE = "CORTES.xlsx"

# Links de publicaciones (SKU -> item/link/fotos)
# Debe estar en el repo, en la misma carpeta que app.py
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PUBLICATIONS_FILE = os.path.join(BASE_DIR, "links_con_imagenes.xlsx")
STOCK_KAME_FILE = os.path.join(BASE_DIR, "stock_kame.json")
# =========================
# SFX (Sistema A: CLICK + OK/ERR) — estable para Chrome/Android
# =========================
def _sfx_init_state():
    ss = st.session_state
    if "sfx_enabled" not in ss:
        ss["sfx_enabled"] = True
    if "sfx_volume" not in ss:
        ss["sfx_volume"] = 0.55  # 0..1
    if "sfx_unlocked" not in ss:
        ss["sfx_unlocked"] = False
    if "_sfx_kind" not in ss:
        ss["_sfx_kind"] = ""
    if "_sfx_nonce" not in ss:
        ss["_sfx_nonce"] = 0

def sfx_sidebar():
    _sfx_init_state()
    with st.sidebar.expander("🔊 Sonidos", expanded=True):
        st.session_state["sfx_enabled"] = st.toggle("Sonido", value=st.session_state["sfx_enabled"], key="sfx_enabled_toggle")
        vol_pct = st.slider("Volumen", min_value=0, max_value=100, value=int(st.session_state["sfx_volume"]*100), step=5, key="sfx_volume_slider")
        st.session_state["sfx_volume"] = max(0.0, min(1.0, vol_pct/100.0))

        if st.button("Activar sonido", disabled=bool(st.session_state.get("sfx_unlocked", False)), use_container_width=True):
            st.session_state["sfx_unlocked"] = True
            st.rerun()

        if st.session_state.get("sfx_unlocked", False):
            st.success("Audio habilitado ✅")
        else:
            st.info("En Chrome debes tocar “Activar sonido” una vez.")

def _sfx_unlock_render():
    _sfx_init_state()
    if not st.session_state.get("sfx_enabled", True):
        return
    if not st.session_state.get("sfx_unlocked", False):
        return

    components.html(
        '''
        <script>
        (function(){
          try{
            const root = window.parent || window;
            const AC = root.AudioContext || root.webkitAudioContext;
            if(!root.__auroraAudio && AC){
              root.__auroraAudio = new AC();
            }
            if(root.__auroraAudio && root.__auroraAudio.state === "suspended"){
              root.__auroraAudio.resume();
            }
          }catch(e){}
        })();
        </script>
        ''',
        height=0,
    )

def _sfx_global_click_hook():
    _sfx_init_state()
    enabled = bool(st.session_state.get("sfx_enabled", True))
    unlocked = bool(st.session_state.get("sfx_unlocked", False))
    vol = float(st.session_state.get("sfx_volume", 0.55))

    cfg_js = json.dumps({"enabled": enabled, "unlocked": unlocked, "volume": vol})

    components.html(
        '''
        <script>
        (function(){
          try{
            const root = window.parent || window;
            root.__auroraSfxCfg = __CFG__;
            const doc = root.document;
            if(root.__auroraClickHookInstalled) return;
            root.__auroraClickHookInstalled = true;

            function playClick(){
              try{
                const cfg = root.__auroraSfxCfg || {enabled:false, unlocked:false, volume:0.5};
                if(!cfg.enabled || !cfg.unlocked) return;
                const ctx = root.__auroraAudio;
                if(!ctx) return;
                const now = ctx.currentTime;
                const o = ctx.createOscillator();
                const g = ctx.createGain();
                o.type = "square";
                o.frequency.setValueAtTime(1200, now);
                g.gain.setValueAtTime(0.0001, now);
                g.gain.exponentialRampToValueAtTime(Math.max(0.02, cfg.volume*0.10), now+0.005);
                g.gain.exponentialRampToValueAtTime(0.0001, now+0.03);
                o.connect(g); g.connect(ctx.destination);
                o.start(now); o.stop(now+0.04);
              }catch(e){}
            }

            doc.addEventListener("click", function(ev){
              const t = ev.target;
              if(!t) return;
              const btn = t.closest ? t.closest("button") : null;
              if(!btn) return;
              playClick();
            }, true);
          }catch(e){}
        })();
        </script>
        '''.replace("__CFG__", cfg_js),
        height=0,
    )

def sfx_emit(kind: str):
    _sfx_init_state()
    if not st.session_state.get("sfx_enabled", True):
        return
    if not st.session_state.get("sfx_unlocked", False):
        return
    kind = (kind or "").upper().strip()
    if kind not in ("OK", "ERR"):
        kind = "ERR"
    st.session_state["_sfx_kind"] = kind
    st.session_state["_sfx_nonce"] = int(st.session_state.get("_sfx_nonce", 0)) + 1

def sfx_render_pending():
    _sfx_init_state()
    if not st.session_state.get("sfx_enabled", True):
        return
    if not st.session_state.get("sfx_unlocked", False):
        return
    kind = (st.session_state.get("_sfx_kind") or "").upper().strip()
    if not kind:
        return

    st.session_state["_sfx_kind"] = ""
    nonce = int(st.session_state.get("_sfx_nonce", 0))

    kind_js = json.dumps(kind)

    components.html(
        '''
        <script>
        (function(){
          try{
            const root = window.parent || window;
            const cfg = root.__auroraSfxCfg || {enabled:false, unlocked:false, volume:0.5};
            if(!cfg.enabled || !cfg.unlocked) return;
            const ctx = root.__auroraAudio;
            if(!ctx) return;

            const kind = __KIND__;
            const vol = Math.max(0.0, Math.min(1.0, cfg.volume || 0.5));
            const now = ctx.currentTime;

            function tone(freq, t0, dur, type, gain){
              const o = ctx.createOscillator();
              const g = ctx.createGain();
              o.type = type || "square";
              o.frequency.setValueAtTime(freq, t0);
              g.gain.setValueAtTime(0.0001, t0);
              g.gain.exponentialRampToValueAtTime(Math.max(0.02, vol*(gain||0.12)), t0+0.01);
              g.gain.exponentialRampToValueAtTime(0.0001, t0+dur);
              o.connect(g); g.connect(ctx.destination);
              o.start(t0); o.stop(t0+dur+0.02);
            }

            function ok(){
              tone(988,  now+0.00, 0.06, "square", 0.14);
              tone(1319, now+0.07, 0.06, "square", 0.13);
              tone(1760, now+0.14, 0.06, "square", 0.12);
            }
            function err(){
              tone(220, now+0.00, 0.16, "square", 0.12);
              tone(180, now+0.10, 0.18, "square", 0.10);
            }

            if(kind === "OK") ok();
            else err();
          }catch(e){}
        })();
        </script>
        <!-- nonce:__NONCE__ -->
        '''.replace("__KIND__", kind_js).replace("__NONCE__", str(nonce)),
        height=0,
    )

# =========================
# TIMEZONE CHILE
# =========================
try:
    from zoneinfo import ZoneInfo  # py3.9+
    CL_TZ = ZoneInfo("America/Santiago")
    UTC_TZ = ZoneInfo("UTC")
except Exception:
    CL_TZ = None
    UTC_TZ = None


# PDF manifiestos
try:
    import pdfplumber
    HAS_PDF_LIB = True
except ImportError:
    HAS_PDF_LIB = False


# =========================
# UTILIDADES
# =========================
def now_iso():
    """ISO timestamp in Chile time (America/Santiago) with UTC offset."""
    if CL_TZ is not None:
        return datetime.now(CL_TZ).isoformat(timespec="seconds")
    return datetime.now().isoformat(timespec="seconds")



# =========================
# TEXT HELPERS
# =========================
UBC_RE = re.compile(r"\[\s*UBC\s*:\s*([^\]]+)\]", re.IGNORECASE)


def to_chile_display(iso_str: str) -> str:
    """Muestra timestamps en hora Chile.

    - Si el ISO trae zona/offset, se convierte a America/Santiago.
    - Si es naive (sin zona), se muestra tal cual (asumido ya en hora Chile).
    """
    if not iso_str:
        return ""
    try:
        dt = datetime.fromisoformat(str(iso_str))
        if CL_TZ is None:
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        if dt.tzinfo is not None:
            dt = dt.astimezone(CL_TZ)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(iso_str)


_STOCK_KAME_CACHE = {"path": None, "mtime": None, "data": None}


def load_stock_kame(path: str = STOCK_KAME_FILE) -> tuple[dict[str, float], str]:
    """Carga stock_kame.json desde disco con cache por mtime.

    Espera un JSON con:
    - updated_at: fecha/hora última actualización
    - stock: {sku: cantidad}
    """
    if not path or not os.path.exists(path):
        return {}, ""

    try:
        mtime = os.path.getmtime(path)
    except Exception:
        mtime = None

    cached = _STOCK_KAME_CACHE
    if cached.get("path") == path and cached.get("mtime") == mtime and cached.get("data") is not None:
        payload = cached["data"]
    else:
        try:
            with open(path, "r", encoding="utf-8") as f:
                payload = json.load(f)
        except Exception:
            return {}, ""
        cached.update({"path": path, "mtime": mtime, "data": payload})

    stock_raw = payload.get("stock") or {}
    stock_map: dict[str, float] = {}
    for k, v in stock_raw.items():
        sku = normalize_sku(k)
        if not sku:
            continue
        try:
            stock_map[sku] = float(v)
        except Exception:
            continue

    updated_at = str(payload.get("updated_at") or "").strip()
    return stock_map, updated_at


def obtener_stock_kame(sku: str, path: str = STOCK_KAME_FILE):
    stock_map, _ = load_stock_kame(path)
    sku_n = normalize_sku(sku)
    if not sku_n:
        return None
    return stock_map.get(sku_n)


def obtener_fecha_stock_kame(path: str = STOCK_KAME_FILE) -> str:
    _, updated_at = load_stock_kame(path)
    return updated_at


def format_stock_kame(value) -> str:
    if value is None:
        return "N/D"
    try:
        num = float(value)
        if num.is_integer():
            return f"{int(num):,}".replace(",", ".")
        return f"{num:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return str(value)


def normalize_sku(value) -> str:
    s = str(value).strip()
    if not s or s.lower() == "nan":
        return ""
    if re.fullmatch(r"\d+\.0", s):
        s = s[:-2]
    if re.fullmatch(r"\d+(\.\d+)?[eE][+-]?\d+", s):
        try:
            s = str(int(float(s)))
        except Exception:
            pass
    return s


def only_digits(s: str) -> str:
    return re.sub(r"\D", "", str(s or ""))


def split_barcodes(cell_value) -> list[str]:
    if cell_value is None:
        return []
    s = str(cell_value).strip()
    if not s or s.lower() == "nan":
        return []
    parts = re.split(r"[\s,;]+", s)
    out = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        d = only_digits(p)
        if d:
            out.append(d)
    seen = set()
    uniq = []
    for x in out:
        if x not in seen:
            seen.add(x)
            uniq.append(x)
    return uniq


def get_conn():
    """Conexión SQLite estable para Streamlit Cloud y uso concurrente."""
    conn = sqlite3.connect(
        DB_NAME,
        timeout=30,
        check_same_thread=False,
    )
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA busy_timeout=30000;")
        conn.execute("PRAGMA foreign_keys=ON;")
    except Exception:
        pass
    return conn


# =========================
# DB HELPERS (centralizados)
# =========================
@contextmanager
def db_conn(commit: bool = False):
    """Context manager para SQLite.
    - commit=True: hace commit al salir si no hubo excepción; si hubo, rollback.
    """
    conn = get_conn()
    try:
        yield conn
        if commit:
            conn.commit()
    except Exception:
        if commit:
            try:
                conn.rollback()
            except Exception:
                pass
        raise
    finally:
        try:
            conn.close()
        except Exception:
            pass


def db_fetchone(sql: str, params: tuple = ()):
    with db_conn(commit=False) as conn:
        return conn.execute(sql, params).fetchone()


def db_fetchall(sql: str, params: tuple = ()):
    with db_conn(commit=False) as conn:
        return conn.execute(sql, params).fetchall()


def db_exec(sql: str, params: tuple = (), commit: bool = False):
    with db_conn(commit=commit) as conn:
        cur = conn.execute(sql, params)
        return cur


# ============================================================
# PICKING — RESPALDO PERSISTENTE GOOGLE SHEETS (SQLite-first)
# ============================================================
def _picking_clean(value) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    text = str(value).replace("\u00a0", " ").strip()
    if text.lower() in {"nan", "none", "null", "nat"}:
        return ""
    return re.sub(r"\s+", " ", text)


def get_picking_backup_webhook_url() -> str:
    """Retorna URL sólo si Sheets está habilitado. En esta versión siempre es vacía."""
    if not PICKING_SHEETS_BACKUP_ENABLED:
        return ""
    env_url = _picking_clean(os.getenv("PICKING_SHEETS_WEBHOOK_URL", ""))
    if env_url:
        return env_url
    try:
        secret_url = _picking_clean(st.secrets.get("PICKING_SHEETS_WEBHOOK_URL", ""))
        if secret_url:
            return secret_url
    except Exception:
        pass
    return _picking_clean(DEFAULT_PICKING_SHEETS_WEBHOOK_URL)


def _picking_meta_get(key: str, default: str = "") -> str:
    try:
        with db_conn(commit=False) as conn:
            row = conn.execute("SELECT value FROM picking_sync_meta WHERE key=?", (str(key),)).fetchone()
        return _picking_clean(row[0]) if row else default
    except Exception:
        return default


def _picking_meta_set(key: str, value: str):
    try:
        with db_conn(commit=True) as conn:
            conn.execute(
                """INSERT INTO picking_sync_meta(key,value,updated_at) VALUES (?,?,?)
                   ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
                (str(key), str(value), now_iso()),
            )
    except Exception:
        pass


def _make_picking_event_uid(event_type: str, queued_at: str) -> str:
    return f"PK_EVT:{_picking_clean(event_type)}:{_picking_clean(queued_at)}:{uuid.uuid4().hex}"


def _attach_picking_event_identity(event_type: str, payload: dict, queued_at: str) -> dict:
    out = dict(payload or {})
    if not _picking_clean(out.get("event_uid")):
        out["event_uid"] = _make_picking_event_uid(event_type, queued_at)
    out["event_source"] = "streamlit_picking_aurora"
    return out


def enqueue_picking_backup_event(event_type: str, payload: dict) -> int | None:
    ids = enqueue_picking_backup_events_batch([(event_type, payload)])
    return ids[0] if ids else None


def enqueue_picking_backup_events_batch(events: list[tuple[str, dict]]) -> list[int]:
    """Modo pruebas: no se crea cola ni se envía información a Sheets."""
    if not PICKING_SHEETS_BACKUP_ENABLED:
        return []
    if not events:
        return []
    created_at = now_iso()
    rows = []
    for event_type, payload in events:
        safe_payload = _attach_picking_event_identity(event_type, payload or {}, created_at)
        rows.append((event_type, json.dumps(safe_payload, ensure_ascii=False, default=str), created_at))

    ids: list[int] = []
    try:
        with db_conn(commit=True) as conn:
            for event_type, payload_json, ts in rows:
                cur = conn.execute(
                    """INSERT INTO picking_backup_queue
                       (event_type,payload_json,status,attempts,created_at)
                       VALUES (?,?,'pending',0,?)""",
                    (event_type, payload_json, ts),
                )
                ids.append(int(cur.lastrowid))
    except Exception:
        return []

    trigger_picking_backup_sync_async(limit=max(PICKING_BACKUP_WORKER_LIMIT, min(120, len(ids) + 10)))
    return ids


def _is_picking_backup_transient(message: str) -> bool:
    msg = _picking_clean(message).lower()
    transient_tokens = (
        "timeout", "timed out", "lock_busy", "apps script ocupado", "script ocupado",
        "temporarily", "connection reset", "connection aborted", "too many times",
        "429", "502", "503", "504",
    )
    return any(tok in msg for tok in transient_tokens)


def _send_picking_webhook_event(url: str, event: dict, timeout: int = 25) -> tuple[bool, str]:
    if not PICKING_SHEETS_BACKUP_ENABLED:
        return False, "Respaldo Google Sheets deshabilitado en la versión de pruebas."
    try:
        resp = requests.post(url, json=event, timeout=(5, int(timeout)), allow_redirects=True)
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
    text = resp.text or ""
    if not (200 <= int(resp.status_code) < 300):
        return False, f"HTTP {resp.status_code}: {text[:500]}"
    try:
        data = resp.json()
    except Exception:
        return False, f"Respuesta no JSON: {text[:500]}"
    if data.get("ok") is True:
        return True, text[:500]
    if data.get("transient") is True or data.get("retry") is True:
        return False, f"TRANSIENT_APPS_SCRIPT: {text[:500]}"
    return False, f"Apps Script respondió ok=false: {text[:500]}"


def _claim_picking_backup_lease(owner: str, lease_seconds: int = 60) -> bool:
    """Candado persistente para que 8 sesiones/PDA no creen 8 escritores hacia Sheets."""
    now_epoch = time.time()
    try:
        conn = get_conn()
        c = conn.cursor()
        c.execute("BEGIN IMMEDIATE")
        row_owner = c.execute("SELECT value FROM picking_sync_meta WHERE key='worker_owner'").fetchone()
        row_until = c.execute("SELECT value FROM picking_sync_meta WHERE key='worker_until'").fetchone()
        cur_owner = _picking_clean(row_owner[0]) if row_owner else ""
        try:
            cur_until = float(row_until[0]) if row_until and row_until[0] not in (None, "") else 0.0
        except Exception:
            cur_until = 0.0
        if cur_owner and cur_owner != owner and cur_until > now_epoch:
            conn.rollback()
            conn.close()
            return False
        until = str(now_epoch + max(20, int(lease_seconds)))
        c.execute(
            """INSERT INTO picking_sync_meta(key,value,updated_at) VALUES ('worker_owner',?,?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at""",
            (owner, now_iso()),
        )
        c.execute(
            """INSERT INTO picking_sync_meta(key,value,updated_at) VALUES ('worker_until',?,?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at""",
            (until, now_iso()),
        )
        conn.commit()
        conn.close()
        return True
    except Exception:
        try:
            conn.rollback()
            conn.close()
        except Exception:
            pass
        return False


def _renew_picking_backup_lease(owner: str, lease_seconds: int = 60):
    try:
        with db_conn(commit=True) as conn:
            row = conn.execute("SELECT value FROM picking_sync_meta WHERE key='worker_owner'").fetchone()
            if row and _picking_clean(row[0]) == owner:
                until = str(time.time() + max(20, int(lease_seconds)))
                conn.execute("UPDATE picking_sync_meta SET value=?,updated_at=? WHERE key='worker_until'", (until, now_iso()))
    except Exception:
        pass


def _release_picking_backup_lease(owner: str):
    try:
        with db_conn(commit=True) as conn:
            row = conn.execute("SELECT value FROM picking_sync_meta WHERE key='worker_owner'").fetchone()
            if row and _picking_clean(row[0]) == owner:
                conn.execute("UPDATE picking_sync_meta SET value='',updated_at=? WHERE key='worker_owner'", (now_iso(),))
                conn.execute("UPDATE picking_sync_meta SET value='0',updated_at=? WHERE key='worker_until'", (now_iso(),))
    except Exception:
        pass


def flush_picking_backup_queue(limit: int = 40, include_failed: bool = False) -> int:
    """Drena la cola sólo con sincronización habilitada."""
    if not PICKING_SHEETS_BACKUP_ENABLED:
        return 0
    url = get_picking_backup_webhook_url()
    if not url:
        return 0
    owner = uuid.uuid4().hex
    if not _claim_picking_backup_lease(owner, lease_seconds=180):
        return 0
    processed = 0
    try:
        statuses = ("pending", "failed") if include_failed else ("pending",)
        qmarks = ",".join("?" for _ in statuses)
        while processed < max(1, int(limit)):
            with db_conn(commit=False) as conn:
                row = conn.execute(
                    f"""SELECT id,event_type,payload_json,attempts,created_at,last_error,last_attempt_at
                        FROM picking_backup_queue
                        WHERE status IN ({qmarks})
                        ORDER BY id ASC LIMIT 1""",
                    statuses,
                ).fetchone()
            if not row:
                break
            qid, event_type, payload_json, attempts, created_at, last_error, last_attempt_at = row
            try:
                payload = json.loads(payload_json or "{}")
            except Exception:
                payload = {}
            event = {
                "event_type": event_type,
                "queue_id": int(qid),
                "queued_at": created_at,
                **payload,
            }
            attempt_at = now_iso()
            try:
                with db_conn(commit=True) as conn:
                    conn.execute("UPDATE picking_backup_queue SET last_attempt_at=? WHERE id=?", (attempt_at, int(qid)))
            except Exception:
                pass

            ok, detail = _send_picking_webhook_event(url, event)
            processed += 1
            if ok:
                with db_conn(commit=True) as conn:
                    conn.execute(
                        "UPDATE picking_backup_queue SET status='sent',sent_at=?,last_error=NULL WHERE id=?",
                        (now_iso(), int(qid)),
                    )
                if processed % 5 == 0:
                    _renew_picking_backup_lease(owner, 180)
                continue

            detail = _picking_clean(detail)[:700]
            if _is_picking_backup_transient(detail):
                with db_conn(commit=True) as conn:
                    conn.execute(
                        "UPDATE picking_backup_queue SET status='pending',last_error=? WHERE id=?",
                        (detail, int(qid)),
                    )
                break

            attempts_next = int(attempts or 0) + 1
            new_status = "failed" if attempts_next >= PICKING_BACKUP_MAX_ATTEMPTS else "pending"
            with db_conn(commit=True) as conn:
                conn.execute(
                    "UPDATE picking_backup_queue SET attempts=?,status=?,last_error=? WHERE id=?",
                    (attempts_next, new_status, detail, int(qid)),
                )
            if new_status == "pending":
                break
        return processed
    finally:
        _release_picking_backup_lease(owner)


def _picking_backup_worker(limit: int):
    global _PICKING_BACKUP_THREAD_RUNNING
    try:
        flush_picking_backup_queue(limit=int(limit), include_failed=False)
    except Exception:
        pass
    finally:
        with _PICKING_BACKUP_THREAD_LOCK:
            _PICKING_BACKUP_THREAD_RUNNING = False


def trigger_picking_backup_sync_async(limit: int = 40) -> bool:
    """Modo pruebas: no inicia hilos de sincronización."""
    global _PICKING_BACKUP_THREAD_RUNNING
    if not PICKING_SHEETS_BACKUP_ENABLED:
        return False
    if not get_picking_backup_webhook_url():
        return False
    with _PICKING_BACKUP_THREAD_LOCK:
        if _PICKING_BACKUP_THREAD_RUNNING:
            return False
        _PICKING_BACKUP_THREAD_RUNNING = True
    try:
        thread = threading.Thread(target=_picking_backup_worker, args=(int(limit),), daemon=True)
        thread.start()
        return True
    except Exception:
        with _PICKING_BACKUP_THREAD_LOCK:
            _PICKING_BACKUP_THREAD_RUNNING = False
        return False


def get_picking_backup_status() -> dict:
    try:
        with db_conn(commit=False) as conn:
            row = conn.execute(
                """SELECT
                    SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END),
                    SUM(CASE WHEN status='sent' THEN 1 ELSE 0 END),
                    SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END),
                    MAX(sent_at), MAX(last_error)
                   FROM picking_backup_queue"""
            ).fetchone()
        return {
            "pending": int((row[0] if row else 0) or 0),
            "sent": int((row[1] if row else 0) or 0),
            "failed": int((row[2] if row else 0) or 0),
            "last_sent": (row[3] if row else "") or "",
            "last_error": (row[4] if row else "") or "",
        }
    except Exception:
        return {"pending": 0, "sent": 0, "failed": 0, "last_sent": "", "last_error": ""}


def _picking_batch_context(batch_key: str) -> dict:
    batch_key = _picking_clean(batch_key)
    if not batch_key:
        return {}
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            """SELECT po.batch_key,MAX(COALESCE(NULLIF(po.batch_label,''),po.ot_code)) AS batch_label,
                      MIN(po.created_at) AS created_at,MAX(COALESCE(po.model,'SKU')) AS model,
                      COUNT(DISTINCT po.id) AS ot_count,COUNT(DISTINCT pk.name) AS picker_count,
                      GROUP_CONCAT(DISTINCT pk.name) AS picker_names
                 FROM picking_ots po JOIN pickers pk ON pk.id=po.picker_id
                WHERE po.batch_key=? GROUP BY po.batch_key""",
            (batch_key,),
        ).fetchone()
        if not row:
            return {}
        out = dict(row)
        label = _picking_clean(out.get("batch_label")) or batch_key
        out["source_label"] = label.split(" · ", 1)[0] if " · " in label else label
        return out
    finally:
        conn.close()


def _picking_entity_rows_for_batch(batch_key: str) -> list[dict]:
    """Snapshot lógico sin ids SQLite; se restaura por OT/SKU/venta y no recalcula reparto."""
    batch_key = _picking_clean(batch_key)
    if not batch_key:
        return []
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    entities: list[dict] = []
    try:
        ots = conn.execute(
            """SELECT po.ot_code,pk.name AS picker_name,po.status,po.created_at,po.closed_at,
                      COALESCE(po.model,'') AS model,COALESCE(po.batch_key,'') AS batch_key,
                      COALESCE(po.batch_label,'') AS batch_label
                 FROM picking_ots po JOIN pickers pk ON pk.id=po.picker_id
                WHERE po.batch_key=? ORDER BY po.id""",
            (batch_key,),
        ).fetchall()
        ot_codes = [_picking_clean(r["ot_code"]) for r in ots]
        for r in ots:
            entities.append({"entity": "ot", **dict(r)})
        if not ot_codes:
            return entities
        qmarks = ",".join("?" for _ in ot_codes)

        orders = conn.execute(
            f"""SELECT DISTINCT o.ml_order_id,o.buyer,o.created_at
                  FROM ot_orders oo JOIN picking_ots po ON po.id=oo.ot_id JOIN orders o ON o.id=oo.order_id
                 WHERE po.ot_code IN ({qmarks}) ORDER BY o.id""",
            ot_codes,
        ).fetchall()
        for r in orders:
            entities.append({"entity": "order", **dict(r)})

        order_ids = [_picking_clean(r["ml_order_id"]) for r in orders]
        if order_ids:
            oq = ",".join("?" for _ in order_ids)
            rows = conn.execute(
                f"""SELECT o.ml_order_id,oi.sku_ml,oi.title_ml,oi.title_tec,oi.qty
                      FROM order_items oi JOIN orders o ON o.id=oi.order_id
                     WHERE o.ml_order_id IN ({oq}) ORDER BY oi.id""",
                order_ids,
            ).fetchall()
            for r in rows:
                entities.append({"entity": "order_item", **dict(r)})

        rows = conn.execute(
            f"""SELECT po.ot_code,o.ml_order_id
                  FROM ot_orders oo JOIN picking_ots po ON po.id=oo.ot_id JOIN orders o ON o.id=oo.order_id
                 WHERE po.ot_code IN ({qmarks}) ORDER BY oo.id""",
            ot_codes,
        ).fetchall()
        for r in rows:
            entities.append({"entity": "ot_order", **dict(r)})

        rows = conn.execute(
            f"""SELECT po.ot_code,pt.sku_ml,pt.title_ml,pt.title_tec,pt.qty_total,pt.qty_picked,pt.status,
                       pt.decided_at,pt.confirm_mode,COALESCE(pt.defer_rank,0) AS defer_rank,pt.defer_at,
                       COALESCE(pt.family,'') AS family
                  FROM picking_tasks pt JOIN picking_ots po ON po.id=pt.ot_id
                 WHERE po.ot_code IN ({qmarks}) ORDER BY pt.id""",
            ot_codes,
        ).fetchall()
        for r in rows:
            entities.append({"entity": "task", **dict(r)})

        rows = conn.execute(
            f"""SELECT po.ot_code,pi.sku_ml,pi.qty_total,pi.qty_picked,pi.qty_missing,pi.reason,
                       COALESCE(pi.note,'') AS note,pi.created_at
                  FROM picking_incidences pi JOIN picking_ots po ON po.id=pi.ot_id
                 WHERE po.ot_code IN ({qmarks}) ORDER BY pi.id""",
            ot_codes,
        ).fetchall()
        for r in rows:
            entities.append({"entity": "incidence", **dict(r)})

        rows = conn.execute(
            f"""SELECT po.ot_code,ct.sku_ml,ct.title_ml,ct.title_tec,ct.qty_total,ct.created_at
                  FROM cortes_tasks ct JOIN picking_ots po ON po.id=ct.ot_id
                 WHERE po.ot_code IN ({qmarks}) ORDER BY ct.id""",
            ot_codes,
        ).fetchall()
        for r in rows:
            entities.append({"entity": "corte", **dict(r)})
    finally:
        conn.close()
    return entities


def _chunk_picking_entities(entities: list[dict], max_bytes: int = PICKING_SNAPSHOT_MAX_JSON_BYTES) -> list[list[dict]]:
    chunks: list[list[dict]] = []
    current: list[dict] = []
    current_size = 2
    for entity in entities:
        encoded = json.dumps(entity, ensure_ascii=False, default=str, separators=(",", ":")).encode("utf-8")
        item_size = len(encoded) + 1
        if current and current_size + item_size > max(8000, int(max_bytes)):
            chunks.append(current)
            current = []
            current_size = 2
        current.append(entity)
        current_size += item_size
    if current or not chunks:
        chunks.append(current)
    return chunks


def queue_picking_batch_snapshot(batch_key: str, motivo: str = "AUTO", include_lote_created: bool = False) -> list[int]:
    """Guarda estructura y progreso actual del lote en chunks seguros para Sheets."""
    ctx = _picking_batch_context(batch_key)
    if not ctx:
        return []
    entities = _picking_entity_rows_for_batch(batch_key)
    tasks = [x for x in entities if x.get("entity") == "task"]
    cortes = [x for x in entities if x.get("entity") == "corte"]
    total_units = sum(int(x.get("qty_total") or 0) for x in tasks + cortes)
    stable = json.dumps(entities, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
    snapshot_hash = hashlib.sha256(stable.encode("utf-8")).hexdigest()
    chunks = _chunk_picking_entities(entities)
    ts = now_iso()
    batch_label = _picking_clean(ctx.get("batch_label")) or batch_key
    base = {
        "lote_id": batch_key,
        "lote_nombre": batch_label,
        "backup_lote_key": batch_key,
        "lote_created_at": _picking_clean(ctx.get("created_at")),
        "archivo": _picking_clean(ctx.get("source_label")),
        "hoja": "PICKING",
        "model": _picking_clean(ctx.get("model")),
        "picker_count": int(ctx.get("picker_count") or 0),
        "picker_names": _picking_clean(ctx.get("picker_names")),
        "created_at": ts,
    }
    events: list[tuple[str, dict]] = []
    if include_lote_created:
        status = "CERRADO" if ctx and all(
            _picking_clean(x.get("status")).upper() == "PICKED"
            for x in entities if x.get("entity") == "ot"
        ) else "ACTIVO"
        events.append(("lote_creado", {
            **base,
            "total_lineas": len(tasks),
            "total_unidades": total_units,
            "status": status,
            "snapshot_mode": "PICKING_SQLITE_CHUNKS_V1",
        }))
    for idx, items in enumerate(chunks, start=1):
        events.append(("lote_snapshot_chunk", {
            **base,
            "snapshot_kind": "PICKING_SQLITE_V1",
            "motivo_snapshot": _picking_clean(motivo) or "AUTO",
            "chunk_index": idx,
            "chunk_total": len(chunks),
            "productos_total": len(tasks),
            "unidades_total": total_units,
            "snapshot_hash": snapshot_hash,
            "items": items,
        }))
    events.append(("lote_snapshot_completo", {
        **base,
        "snapshot_kind": "PICKING_SQLITE_V1",
        "motivo_snapshot": _picking_clean(motivo) or "AUTO",
        "chunk_index": len(chunks),
        "chunk_total": len(chunks),
        "productos_total": len(tasks),
        "unidades_total": total_units,
        "snapshot_hash": snapshot_hash,
    }))
    return enqueue_picking_backup_events_batch(events)


def _picking_task_payload(task_id: int) -> dict:
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            """SELECT pt.id,po.batch_key,po.batch_label,po.ot_code,pk.name AS picker_name,
                      pt.sku_ml,pt.title_ml,pt.title_tec,pt.qty_total,pt.qty_picked,pt.status,
                      pt.decided_at,pt.confirm_mode,COALESCE(pt.defer_rank,0) AS defer_rank,
                      pt.defer_at,COALESCE(pt.family,'') AS family,po.created_at AS lote_created_at
                 FROM picking_tasks pt JOIN picking_ots po ON po.id=pt.ot_id
                 JOIN pickers pk ON pk.id=po.picker_id WHERE pt.id=?""",
            (int(task_id),),
        ).fetchone()
        if not row or not _picking_clean(row["batch_key"]):
            return {}
        d = dict(row)
        batch_key = _picking_clean(d.get("batch_key"))
        return {
            **d,
            "lote_id": batch_key,
            "lote_nombre": _picking_clean(d.get("batch_label")) or batch_key,
            "backup_lote_key": batch_key,
            "created_at": now_iso(),
        }
    finally:
        conn.close()


def queue_picking_task_state(task_id: int) -> int | None:
    payload = _picking_task_payload(task_id)
    return enqueue_picking_backup_event("picking_task_state", payload) if payload else None


def _picking_incidence_payload(incidence_id: int) -> dict:
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            """SELECT pi.id,po.batch_key,po.batch_label,po.ot_code,pk.name AS picker_name,
                      pi.sku_ml,pi.qty_total,pi.qty_picked,pi.qty_missing,pi.reason,
                      COALESCE(pi.note,'') AS note,pi.created_at AS incidence_created_at,
                      po.created_at AS lote_created_at
                 FROM picking_incidences pi JOIN picking_ots po ON po.id=pi.ot_id
                 JOIN pickers pk ON pk.id=po.picker_id WHERE pi.id=?""",
            (int(incidence_id),),
        ).fetchone()
        if not row or not _picking_clean(row["batch_key"]):
            return {}
        d = dict(row)
        batch_key = _picking_clean(d.get("batch_key"))
        d.update({
            "lote_id": batch_key,
            "lote_nombre": _picking_clean(d.get("batch_label")) or batch_key,
            "backup_lote_key": batch_key,
            "created_at": now_iso(),
        })
        return d
    finally:
        conn.close()


def queue_picking_incidence_state(incidence_id: int) -> int | None:
    payload = _picking_incidence_payload(incidence_id)
    return enqueue_picking_backup_event("picking_incidence_state", payload) if payload else None


def _picking_ot_payload(ot_id: int) -> dict:
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            """SELECT po.id,po.batch_key,po.batch_label,po.ot_code,pk.name AS picker_name,
                      po.status,po.created_at AS ot_created_at,po.closed_at,COALESCE(po.model,'') AS model
                 FROM picking_ots po JOIN pickers pk ON pk.id=po.picker_id WHERE po.id=?""",
            (int(ot_id),),
        ).fetchone()
        if not row or not _picking_clean(row["batch_key"]):
            return {}
        d = dict(row)
        batch_key = _picking_clean(d.get("batch_key"))
        d.update({
            "lote_id": batch_key,
            "lote_nombre": _picking_clean(d.get("batch_label")) or batch_key,
            "backup_lote_key": batch_key,
            "created_at": now_iso(),
        })
        return d
    finally:
        conn.close()


def queue_picking_ot_state(ot_id: int) -> int | None:
    payload = _picking_ot_payload(ot_id)
    return enqueue_picking_backup_event("picking_ot_state", payload) if payload else None


def queue_picking_batch_closed_if_complete(batch_key: str):
    batch_key = _picking_clean(batch_key)
    if not batch_key:
        return
    ctx = _picking_batch_context(batch_key)
    if not ctx:
        return
    try:
        with db_conn(commit=False) as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM picking_ots WHERE batch_key=? AND status<>'PICKED'",
                (batch_key,),
            ).fetchone()
        if int((row[0] if row else 0) or 0) == 0:
            enqueue_picking_backup_event("lote_cerrado", {
                "lote_id": batch_key,
                "lote_nombre": _picking_clean(ctx.get("batch_label")) or batch_key,
                "backup_lote_key": batch_key,
                "lote_created_at": _picking_clean(ctx.get("created_at")),
                "archivo": _picking_clean(ctx.get("source_label")),
                "hoja": "PICKING",
                "status": "CERRADO",
                "usuario": "SISTEMA",
                "created_at": now_iso(),
            })
    except Exception:
        pass


def _read_picking_sheets_json(params: dict, timeout: int = 40, attempts: int = 3) -> tuple[bool, dict, str]:
    if not PICKING_SHEETS_BACKUP_ENABLED:
        return False, {}, "Respaldo Google Sheets deshabilitado en esta versión de pruebas."
    url = get_picking_backup_webhook_url()
    if not url:
        return False, {}, "No hay webhook de respaldo configurado."
    last_error = ""
    for attempt in range(max(1, int(attempts))):
        try:
            resp = requests.get(url, params=params, timeout=(5, int(timeout)), allow_redirects=True)
            text = resp.text or ""
            if not (200 <= int(resp.status_code) < 300):
                raise RuntimeError(f"HTTP {resp.status_code}: {text[:500]}")
            data = resp.json()
            if data.get("ok") is not True:
                return False, data if isinstance(data, dict) else {}, f"Apps Script respondió error: {text[:500]}"
            return True, data, "OK"
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt + 1 < max(1, int(attempts)):
                time.sleep(0.6 + attempt * 0.8)
    return False, {}, last_error


def get_picking_backup_lotes_from_sheets() -> tuple[bool, list[dict], str]:
    ok, data, msg = _read_picking_sheets_json({"action": "lotes"}, timeout=35, attempts=2)
    if not ok:
        return False, [], msg
    rows = []
    for raw in data.get("lotes") or []:
        r = dict(raw or {})
        key = _picking_clean(r.get("backup_lote_key")) or _picking_clean(r.get("lote_id"))
        if not key.startswith("PK-"):
            continue
        r["backup_lote_key"] = key
        rows.append(r)
    rows.sort(key=lambda r: _picking_clean(r.get("ultimo_evento") or r.get("created_at")), reverse=True)
    return True, rows, f"Lotes Picking disponibles: {len(rows)}"


def get_picking_backup_events_from_sheets(batch_key: str, max_pages: int = 120) -> tuple[bool, list[dict], str]:
    batch_key = _picking_clean(batch_key)
    if not batch_key:
        return False, [], "Lote sin batch_key."
    events: list[dict] = []
    cursor = ""
    seen = set()
    for page in range(max(1, int(max_pages))):
        params = {
            "action": "events",
            "backup_lote_key": batch_key,
            "lote_id": batch_key,
            "page_size": 750,
            "scan_rows": 10000,
        }
        if cursor:
            params["cursor"] = cursor
        ok, data, msg = _read_picking_sheets_json(params, timeout=45, attempts=3)
        if not ok:
            return False, events, msg
        events.extend(data.get("events") or [])
        nxt = _picking_clean(data.get("next_cursor"))
        if not nxt:
            return True, events, f"Eventos leídos: {len(events)}"
        if nxt in seen:
            return False, events, "Apps Script repitió cursor; rescate detenido por seguridad."
        seen.add(nxt)
        cursor = nxt
    return False, events, f"Rescate excedió {max_pages} páginas."


def _normalize_picking_sheet_event(raw: dict) -> dict:
    out = dict(raw or {})
    raw_json = out.get("raw_json")
    if raw_json:
        try:
            parsed = json.loads(raw_json) if isinstance(raw_json, str) else raw_json
            if isinstance(parsed, dict):
                out.update(parsed)
        except Exception:
            pass
    return out


def _picking_event_ts(ev: dict) -> str:
    return _picking_clean(ev.get("created_at")) or _picking_clean(ev.get("queued_at")) or _picking_clean(ev.get("received_at"))


def _dedupe_picking_sheet_events(events: list[dict]) -> list[dict]:
    seen = set()
    out = []
    for raw in events or []:
        ev = _normalize_picking_sheet_event(raw)
        key = _picking_clean(ev.get("event_key"))
        uid = _picking_clean(ev.get("event_uid"))
        ident = f"KEY:{key}" if key else (f"UID:{uid}" if uid else "SEM:" + "|".join([
            _picking_clean(ev.get("event_type")), _picking_event_ts(ev),
            _picking_clean(ev.get("backup_lote_key")), _picking_clean(ev.get("snapshot_hash")),
            _picking_clean(ev.get("chunk_index")), _picking_clean(ev.get("ot_code")),
            _picking_clean(ev.get("sku_ml")), _picking_clean(ev.get("status")),
            _picking_clean(ev.get("incidence_created_at")),
        ]))
        if ident in seen:
            continue
        seen.add(ident)
        out.append(ev)
    out.sort(key=lambda x: (_picking_event_ts(x), _picking_clean(x.get("event_uid"))))
    return out


def _select_latest_picking_snapshot(events: list[dict]) -> tuple[list[dict], str, str, int]:
    groups: dict[str, dict] = {}
    for ev in events:
        if _picking_clean(ev.get("event_type")).lower() != "lote_snapshot_chunk":
            continue
        if _picking_clean(ev.get("snapshot_kind")) != "PICKING_SQLITE_V1":
            continue
        snap_hash = _picking_clean(ev.get("snapshot_hash"))
        if not snap_hash:
            continue
        try:
            idx = int(ev.get("chunk_index") or 0)
            total = int(ev.get("chunk_total") or 0)
        except Exception:
            continue
        if idx <= 0 or total <= 0:
            continue
        g = groups.setdefault(snap_hash, {"chunks": {}, "total": total, "ts": "", "order": 0})
        g["chunks"][idx] = ev
        g["total"] = max(int(g.get("total") or 0), total)
        g["ts"] = max(_picking_clean(g.get("ts")), _picking_event_ts(ev))
        try:
            g["order"] = max(int(g.get("order") or 0), int(ev.get("queue_id") or 0))
        except Exception:
            pass
    candidates = []
    for snap_hash, g in groups.items():
        total = int(g.get("total") or 0)
        if total > 0 and all(i in g["chunks"] for i in range(1, total + 1)):
            candidates.append((_picking_clean(g.get("ts")), int(g.get("order") or 0), snap_hash, g))
    if not candidates:
        return [], "", "", 0
    ts, snap_order, snap_hash, g = max(candidates, key=lambda x: (x[0], x[1], x[2]))
    entities: list[dict] = []
    for idx in range(1, int(g["total"]) + 1):
        items = g["chunks"][idx].get("items") or []
        if isinstance(items, list):
            entities.extend([dict(x) for x in items if isinstance(x, dict)])
    return entities, snap_hash, ts, int(snap_order or 0)


def _delete_local_picking_batch(conn, batch_key: str):
    rows = conn.execute("SELECT id FROM picking_ots WHERE batch_key=?", (batch_key,)).fetchall()
    ot_ids = [int(r[0]) for r in rows]
    if not ot_ids:
        return
    q = ",".join("?" for _ in ot_ids)
    order_rows = conn.execute(f"SELECT DISTINCT order_id FROM ot_orders WHERE ot_id IN ({q})", ot_ids).fetchall()
    order_ids = [int(r[0]) for r in order_rows]
    conn.execute(f"DELETE FROM picking_incidences WHERE ot_id IN ({q})", ot_ids)
    conn.execute(f"DELETE FROM cortes_tasks WHERE ot_id IN ({q})", ot_ids)
    conn.execute(f"DELETE FROM picking_tasks WHERE ot_id IN ({q})", ot_ids)
    conn.execute(f"DELETE FROM ot_orders WHERE ot_id IN ({q})", ot_ids)
    picker_rows = conn.execute(f"SELECT DISTINCT picker_id FROM picking_ots WHERE id IN ({q})", ot_ids).fetchall()
    picker_ids = [int(r[0]) for r in picker_rows]
    conn.execute(f"DELETE FROM picking_ots WHERE id IN ({q})", ot_ids)
    for oid in order_ids:
        ref = conn.execute("SELECT 1 FROM ot_orders WHERE order_id=? LIMIT 1", (oid,)).fetchone()
        if not ref:
            conn.execute("DELETE FROM order_items WHERE order_id=?", (oid,))
            conn.execute("DELETE FROM orders WHERE id=?", (oid,))
    for pid in picker_ids:
        ref = conn.execute("SELECT 1 FROM picking_ots WHERE picker_id=? LIMIT 1", (pid,)).fetchone()
        if not ref:
            conn.execute("DELETE FROM pickers WHERE id=?", (pid,))


def restore_picking_batch_from_sheets(batch_key: str, replace_existing: bool = True) -> tuple[bool, str]:
    if not PICKING_SHEETS_BACKUP_ENABLED:
        return False, "Rescate desde Sheets deshabilitado en la versión de pruebas."
    ok, raw_events, msg = get_picking_backup_events_from_sheets(batch_key)
    if not ok:
        return False, msg
    events = _dedupe_picking_sheet_events(raw_events)
    entities, snap_hash, snapshot_ts, snapshot_order = _select_latest_picking_snapshot(events)
    if not entities:
        return False, "No existe un snapshot completo PICKING_SQLITE_V1 para este lote."

    ots = [x for x in entities if x.get("entity") == "ot"]
    if not ots:
        return False, "Snapshot inválido: no contiene OTs."
    batch_label = _picking_clean(ots[0].get("batch_label")) or batch_key

    try:
        conn = get_conn()
        c = conn.cursor()
        c.execute("BEGIN IMMEDIATE")
        if replace_existing:
            _delete_local_picking_batch(conn, batch_key)

        picker_id_by_name = {}
        ot_id_by_code = {}
        for e in ots:
            pname = _picking_clean(e.get("picker_name"))
            if not pname:
                continue
            c.execute("INSERT OR IGNORE INTO pickers(name) VALUES (?)", (pname,))
            pid_row = c.execute("SELECT id FROM pickers WHERE name=?", (pname,)).fetchone()
            if not pid_row:
                continue
            pid = int(pid_row[0])
            picker_id_by_name[pname] = pid
            ot_code = _picking_clean(e.get("ot_code"))
            if not ot_code:
                continue
            c.execute(
                """INSERT OR REPLACE INTO picking_ots
                   (id,ot_code,picker_id,status,created_at,closed_at,model,batch_key,batch_label)
                   VALUES ((SELECT id FROM picking_ots WHERE ot_code=?),?,?,?,?,?,?,?,?)""",
                (
                    ot_code, ot_code, pid, _picking_clean(e.get("status")) or "OPEN",
                    _picking_clean(e.get("created_at")) or now_iso(), e.get("closed_at"),
                    _picking_clean(e.get("model")) or "SKU", batch_key,
                    _picking_clean(e.get("batch_label")) or batch_label,
                ),
            )
            row = c.execute("SELECT id FROM picking_ots WHERE ot_code=?", (ot_code,)).fetchone()
            if row:
                ot_id_by_code[ot_code] = int(row[0])

        order_id_by_ml = {}
        for e in [x for x in entities if x.get("entity") == "order"]:
            mlid = _picking_clean(e.get("ml_order_id"))
            if not mlid:
                continue
            c.execute("INSERT OR IGNORE INTO orders(ml_order_id,buyer,created_at) VALUES (?,?,?)", (mlid, e.get("buyer"), e.get("created_at") or now_iso()))
            c.execute("UPDATE orders SET buyer=?,created_at=? WHERE ml_order_id=?", (e.get("buyer"), e.get("created_at") or now_iso(), mlid))
            row = c.execute("SELECT id FROM orders WHERE ml_order_id=?", (mlid,)).fetchone()
            if row:
                order_id_by_ml[mlid] = int(row[0])
        for oid in order_id_by_ml.values():
            c.execute("DELETE FROM order_items WHERE order_id=?", (oid,))
        for e in [x for x in entities if x.get("entity") == "order_item"]:
            mlid = _picking_clean(e.get("ml_order_id"))
            oid = order_id_by_ml.get(mlid)
            if not oid:
                continue
            c.execute(
                "INSERT INTO order_items(order_id,sku_ml,title_ml,title_tec,qty) VALUES (?,?,?,?,?)",
                (oid, e.get("sku_ml"), e.get("title_ml"), e.get("title_tec"), int(e.get("qty") or 0)),
            )
        for e in [x for x in entities if x.get("entity") == "ot_order"]:
            otid = ot_id_by_code.get(_picking_clean(e.get("ot_code")))
            oid = order_id_by_ml.get(_picking_clean(e.get("ml_order_id")))
            if otid and oid:
                c.execute("INSERT INTO ot_orders(ot_id,order_id) VALUES (?,?)", (otid, oid))
        for e in [x for x in entities if x.get("entity") == "task"]:
            otid = ot_id_by_code.get(_picking_clean(e.get("ot_code")))
            if not otid:
                continue
            c.execute(
                """INSERT INTO picking_tasks
                   (ot_id,sku_ml,title_ml,title_tec,qty_total,qty_picked,status,decided_at,confirm_mode,defer_rank,defer_at,family)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    otid, e.get("sku_ml"), e.get("title_ml"), e.get("title_tec"), int(e.get("qty_total") or 0),
                    int(e.get("qty_picked") or 0), _picking_clean(e.get("status")) or "PENDING",
                    e.get("decided_at"), e.get("confirm_mode"), int(e.get("defer_rank") or 0),
                    e.get("defer_at"), e.get("family"),
                ),
            )
        for e in [x for x in entities if x.get("entity") == "incidence"]:
            otid = ot_id_by_code.get(_picking_clean(e.get("ot_code")))
            if not otid:
                continue
            c.execute(
                """INSERT INTO picking_incidences
                   (ot_id,sku_ml,qty_total,qty_picked,qty_missing,reason,note,created_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (otid, e.get("sku_ml"), int(e.get("qty_total") or 0), int(e.get("qty_picked") or 0),
                 int(e.get("qty_missing") or 0), e.get("reason"), e.get("note"), e.get("created_at") or now_iso()),
            )
        for e in [x for x in entities if x.get("entity") == "corte"]:
            otid = ot_id_by_code.get(_picking_clean(e.get("ot_code")))
            if not otid:
                continue
            c.execute(
                "INSERT INTO cortes_tasks(ot_id,sku_ml,title_ml,title_tec,qty_total,created_at) VALUES (?,?,?,?,?,?)",
                (otid, e.get("sku_ml"), e.get("title_ml"), e.get("title_tec"), int(e.get("qty_total") or 0), e.get("created_at") or now_iso()),
            )

        # Aplicar sólo eventos posteriores al snapshot elegido.
        for ev in events:
            ev_ts = _picking_event_ts(ev)
            try:
                ev_order = int(ev.get("queue_id") or 0)
            except Exception:
                ev_order = 0
            if snapshot_ts and ev_ts:
                if ev_ts < snapshot_ts:
                    continue
                if ev_ts == snapshot_ts and snapshot_order and ev_order and ev_order <= snapshot_order:
                    continue
            et = _picking_clean(ev.get("event_type")).lower()
            if et == "picking_task_state":
                ot_code = _picking_clean(ev.get("ot_code"))
                sku = _picking_clean(ev.get("sku_ml"))
                otid = ot_id_by_code.get(ot_code)
                if otid and sku:
                    c.execute(
                        """UPDATE picking_tasks SET qty_picked=?,status=?,decided_at=?,confirm_mode=?,defer_rank=?,defer_at=?,family=?
                           WHERE ot_id=? AND sku_ml=?""",
                        (int(ev.get("qty_picked") or 0), _picking_clean(ev.get("status")) or "PENDING",
                         ev.get("decided_at"), ev.get("confirm_mode"), int(ev.get("defer_rank") or 0), ev.get("defer_at"),
                         ev.get("family"), otid, sku),
                    )
            elif et == "picking_incidence_state":
                ot_code = _picking_clean(ev.get("ot_code"))
                otid = ot_id_by_code.get(ot_code)
                if otid:
                    exists = c.execute(
                        """SELECT 1 FROM picking_incidences WHERE ot_id=? AND sku_ml=? AND reason=? AND created_at=? LIMIT 1""",
                        (otid, ev.get("sku_ml"), ev.get("reason"), ev.get("incidence_created_at")),
                    ).fetchone()
                    if not exists:
                        c.execute(
                            """INSERT INTO picking_incidences(ot_id,sku_ml,qty_total,qty_picked,qty_missing,reason,note,created_at)
                               VALUES (?,?,?,?,?,?,?,?)""",
                            (otid, ev.get("sku_ml"), int(ev.get("qty_total") or 0), int(ev.get("qty_picked") or 0),
                             int(ev.get("qty_missing") or 0), ev.get("reason"), ev.get("note"), ev.get("incidence_created_at") or _picking_event_ts(ev) or now_iso()),
                        )
            elif et == "picking_ot_state":
                ot_code = _picking_clean(ev.get("ot_code"))
                otid = ot_id_by_code.get(ot_code)
                if otid:
                    c.execute("UPDATE picking_ots SET status=?,closed_at=? WHERE id=?", (_picking_clean(ev.get("status")) or "OPEN", ev.get("closed_at"), otid))
            elif et == "lote_cerrado":
                c.execute("UPDATE picking_ots SET status='PICKED',closed_at=COALESCE(closed_at,?) WHERE batch_key=?", (_picking_event_ts(ev) or now_iso(), batch_key))

        conn.commit()
        conn.close()
        _picking_meta_set("intentional_reset", "")
        _picking_meta_set("last_recovery_at", now_iso())
        return True, f"{batch_label}: restaurado desde Sheets con snapshot {snap_hash[:12]}."
    except Exception as exc:
        try:
            conn.rollback()
            conn.close()
        except Exception:
            pass
        return False, f"No se pudo restaurar {batch_key}: {type(exc).__name__}: {exc}"


def _picking_local_is_empty() -> bool:
    try:
        with db_conn(commit=False) as conn:
            row = conn.execute("SELECT COUNT(*) FROM picking_ots").fetchone()
        return int((row[0] if row else 0) or 0) == 0
    except Exception:
        return True


def _claim_picking_recovery_lease(owner: str, lease_seconds: int = 300) -> bool:
    now_epoch = time.time()
    try:
        conn = get_conn()
        c = conn.cursor()
        c.execute("BEGIN IMMEDIATE")
        row_owner = c.execute("SELECT value FROM picking_sync_meta WHERE key='recovery_owner'").fetchone()
        row_until = c.execute("SELECT value FROM picking_sync_meta WHERE key='recovery_until'").fetchone()
        cur_owner = _picking_clean(row_owner[0]) if row_owner else ""
        try:
            cur_until = float(row_until[0]) if row_until and row_until[0] not in (None, "") else 0.0
        except Exception:
            cur_until = 0.0
        if cur_owner and cur_owner != owner and cur_until > now_epoch:
            conn.rollback(); conn.close(); return False
        until = str(now_epoch + max(60, int(lease_seconds)))
        c.execute("""INSERT INTO picking_sync_meta(key,value,updated_at) VALUES ('recovery_owner',?,?)
                     ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at""", (owner, now_iso()))
        c.execute("""INSERT INTO picking_sync_meta(key,value,updated_at) VALUES ('recovery_until',?,?)
                     ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at""", (until, now_iso()))
        conn.commit(); conn.close(); return True
    except Exception:
        try:
            conn.rollback(); conn.close()
        except Exception:
            pass
        return False


def _release_picking_recovery_lease(owner: str):
    try:
        with db_conn(commit=True) as conn:
            row = conn.execute("SELECT value FROM picking_sync_meta WHERE key='recovery_owner'").fetchone()
            if row and _picking_clean(row[0]) == owner:
                conn.execute("UPDATE picking_sync_meta SET value='',updated_at=? WHERE key='recovery_owner'", (now_iso(),))
                conn.execute("UPDATE picking_sync_meta SET value='0',updated_at=? WHERE key='recovery_until'", (now_iso(),))
    except Exception:
        pass


def _picking_recovery_is_active() -> bool:
    owner = _picking_meta_get("recovery_owner")
    if not owner:
        return False
    try:
        until = float(_picking_meta_get("recovery_until", "0") or 0)
    except Exception:
        until = 0.0
    return until > time.time()


def auto_restore_picking_if_database_was_lost() -> tuple[bool, str]:
    """En esta edición, nunca intenta recuperar datos de Sheets."""
    if not PICKING_SHEETS_BACKUP_ENABLED:
        return False, "rescate_sheets_deshabilitado"
    if not _picking_local_is_empty():
        return False, "local_not_empty"
    if _picking_meta_get("intentional_reset"):
        return False, "intentional_reset"
    owner = uuid.uuid4().hex
    if not _claim_picking_recovery_lease(owner, lease_seconds=300):
        return False, "recovery_in_progress"
    try:
        ok, lotes, msg = get_picking_backup_lotes_from_sheets()
        if not ok:
            return False, msg
        active = [r for r in lotes if _picking_clean(r.get("status") or "ACTIVO").upper() not in {"CERRADO", "CLOSED", "ELIMINADO", "DELETED"}]
        if not active:
            return False, "no_active_lotes"
        restored = 0
        errors = []
        for lote in sorted(active, key=lambda r: _picking_clean(r.get("created_at") or r.get("lote_created_at"))):
            key = _picking_clean(lote.get("backup_lote_key")) or _picking_clean(lote.get("lote_id"))
            rok, rmsg = restore_picking_batch_from_sheets(key, replace_existing=True)
            if rok:
                restored += 1
            else:
                errors.append(rmsg)
        if restored:
            return True, f"Recuperados {restored} lote(s) activos desde Sheets." + ((" " + " | ".join(errors[:2])) if errors else "")
        return False, " | ".join(errors) if errors else "No se pudo restaurar ningún lote."
    finally:
        _release_picking_recovery_lease(owner)


def render_picking_backup_sidebar():
    if not PICKING_SHEETS_BACKUP_ENABLED:
        return
    status = get_picking_backup_status()
    pending = int(status.get("pending") or 0)
    failed = int(status.get("failed") or 0)
    with st.sidebar.expander("☁️ Respaldo Picking", expanded=False):
        if failed:
            st.error(f"{failed} evento(s) con error")
        elif pending:
            st.warning(f"{pending} evento(s) pendientes")
        else:
            st.success("Sincronizado")
        if status.get("last_sent"):
            st.caption(f"Último envío: {to_chile_display(status.get('last_sent'))}")
        if st.button("Sincronizar ahora", key="picking_sync_now_sidebar", use_container_width=True):
            flush_picking_backup_queue(limit=120, include_failed=True)
            st.rerun()


def render_picking_recovery_admin_ui():
    if not PICKING_SHEETS_BACKUP_ENABLED:
        return
    with st.expander("☁️ Respaldo automático / Rescate desde Sheets", expanded=False):
        status = get_picking_backup_status()
        a, b, c = st.columns(3)
        a.metric("Pendientes", int(status.get("pending") or 0))
        b.metric("Enviados", int(status.get("sent") or 0))
        c.metric("Con error", int(status.get("failed") or 0))
        col1, col2 = st.columns(2)
        if col1.button("Sincronizar cola", key="adm_sync_picking_backup"):
            flush_picking_backup_queue(limit=250, include_failed=True)
            st.rerun()
        refresh = col2.button("Buscar lotes en Sheets", key="adm_refresh_picking_lotes")
        if refresh:
            ok, rows, msg = get_picking_backup_lotes_from_sheets()
            st.session_state["_picking_sheet_lotes_admin"] = rows if ok else []
            st.session_state["_picking_sheet_lotes_admin_msg"] = msg
        rows = st.session_state.get("_picking_sheet_lotes_admin", None)
        msg = st.session_state.get("_picking_sheet_lotes_admin_msg", "")
        if msg:
            st.caption(msg)
        if rows is None:
            st.info("Pulsa **Buscar lotes en Sheets** sólo cuando necesites un rescate manual.")
            return
        if not rows:
            st.info("No hay lotes Picking recuperables en Sheets.")
            return
        options = []
        by_label = {}
        for r in rows:
            key = _picking_clean(r.get("backup_lote_key")) or _picking_clean(r.get("lote_id"))
            label = f"{_picking_clean(r.get('lote_nombre')) or key} · {_picking_clean(r.get('status')) or 'ACTIVO'} · {key}"
            options.append(label)
            by_label[label] = key
        selected = st.selectbox("Lote a recuperar", options, key="adm_picking_restore_select")
        if st.button("Restaurar lote seleccionado", type="primary", key="adm_picking_restore_btn"):
            rok, rmsg = restore_picking_batch_from_sheets(by_label[selected], replace_existing=True)
            if rok:
                st.success(rmsg)
                st.session_state.pop("selected_picker", None)
                st.rerun()
            else:
                st.error(rmsg)


# =========================
# BACKUP/RESTORE POR MÓDULO (SQLite parcial)
# =========================
def _db_table_exists(conn, table: str) -> bool:
    try:
        row = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?;", (table,)).fetchone()
        return bool(row)
    except Exception:
        return False

def _export_tables_to_db_bytes(tables: list[str]) -> bytes:
    """Exporta SOLO las tablas indicadas a un .db (bytes). No toca el DB actual."""
    import tempfile
    conn_src = get_conn()
    csrc = conn_src.cursor()
    # Crear DB temporal
    fd, tmp_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn_out = sqlite3.connect(tmp_path, check_same_thread=False)
    cout = conn_out.cursor()
    try:
        for tname in tables:
            if not _db_table_exists(conn_src, tname):
                continue
            row = csrc.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?;", (tname,)).fetchone()
            create_sql = row[0] if row and row[0] else None
            if not create_sql:
                continue
            cout.execute(create_sql)
            rows = csrc.execute(f"SELECT * FROM {tname};").fetchall()
            if rows:
                ncols = len(rows[0])
                ph = ",".join(["?"] * ncols)
                cout.executemany(f"INSERT INTO {tname} VALUES ({ph});", rows)
        conn_out.commit()
        conn_out.close()
        conn_src.close()
        with open(tmp_path, "rb") as f:
            data = f.read()
        return data
    finally:
        try:
            conn_out.close()
        except Exception:
            pass
        try:
            conn_src.close()
        except Exception:
            pass
        try:
            os.remove(tmp_path)
        except Exception:
            pass

def _restore_tables_from_db_bytes(db_bytes: bytes, tables: list[str]) -> tuple[bool, str|None]:
    """Restaura SOLO las tablas indicadas desde un .db (bytes). Mantiene el resto intacto."""
    import tempfile
    # Guardar uploaded db a temp
    fd, up_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    with open(up_path, "wb") as f:
        f.write(db_bytes)

    conn_src = sqlite3.connect(up_path, check_same_thread=False)
    csrc = conn_src.cursor()
    conn_dst = get_conn()
    cdst = conn_dst.cursor()

    try:
        # La restauración puede reemplazar tablas padre e hijas dentro del mismo lote.
        # FK se reactivan al finalizar y se verifica que no haya enlaces huérfanos.
        conn_dst.execute("PRAGMA foreign_keys=OFF;")
        # Validación mínima: que exista al menos 1 de las tablas esperadas
        any_ok = False
        for tname in tables:
            if _db_table_exists(conn_src, tname):
                any_ok = True
                break
        if not any_ok:
            return False, "El respaldo no contiene las tablas esperadas para este módulo."

        # Transacción de reemplazo parcial
        cdst.execute("BEGIN;")
        for tname in tables:
            if not _db_table_exists(conn_src, tname):
                continue

            # Leer schema desde respaldo
            row = csrc.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?;", (tname,)).fetchone()
            create_sql = row[0] if row and row[0] else None
            if not create_sql:
                continue

            # Reemplazar tabla
            cdst.execute(f"DROP TABLE IF EXISTS {tname};")
            cdst.execute(create_sql)

            # Copiar filas
            rows = csrc.execute(f"SELECT * FROM {tname};").fetchall()
            if rows:
                ncols = len(rows[0])
                ph = ",".join(["?"] * ncols)
                cdst.executemany(f"INSERT INTO {tname} VALUES ({ph});", rows)

        violations = conn_dst.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise ValueError(f"Respaldo con referencias incompletas: {violations[:3]}")
        conn_dst.commit()
        return True, None
    except Exception as e:
        try:
            conn_dst.rollback()
        except Exception:
            pass
        return False, str(e)
    finally:
        try:
            conn_src.close()
        except Exception:
            pass
        try:
            conn_dst.execute("PRAGMA foreign_keys=ON;")
            conn_dst.close()
        except Exception:
            pass
        try:
            os.remove(up_path)
        except Exception:
            pass

def _render_module_backup_ui(scope_key: str, scope_label: str, tables: list[str]):
    """UI para respaldar/restaurar SOLO un módulo (tablas específicas)."""
    with st.expander(f"💾 Respaldo / Restauración — {scope_label}", expanded=False):
        st.caption(
            "Este respaldo es SOLO de este módulo (tablas específicas). "
            "No toca datos de otros módulos. "
            "Nota: el mapa común de códigos (sku_barcodes) no se incluye aquí."
        )
        # Password gate sólo para acciones críticas
        pwd2 = st.text_input("Contraseña admin", type="password", key=f"pwd_{scope_key}")
        if pwd2 != ADMIN_PASSWORD:
            st.info("Ingresa la contraseña para habilitar respaldo/restauración.")
            return

        # Backup
        try:
            data = _export_tables_to_db_bytes(tables)
            st.download_button(
                f"⬇️ Descargar respaldo ({scope_key}.db)",
                data=data,
                file_name=f"aurora_{scope_key}.db",
                mime="application/octet-stream",
                use_container_width=True,
                key=f"dl_{scope_key}",
            )
        except Exception as e:
            st.warning(f"No se pudo preparar el respaldo: {e}")

        st.divider()

        up = st.file_uploader(
            f"⬆️ Restaurar respaldo de {scope_label} (.db)",
            type=["db"],
            key=f"up_{scope_key}",
        )
        col1, col2 = st.columns([2, 1])
        with col1:
            confirm = st.text_input("Escribe RESTAURAR para confirmar", value="", key=f"cf_{scope_key}")
        with col2:
            do = st.button(
                "♻️ Restaurar",
                type="primary",
                disabled=not (up and confirm.strip().upper() == "RESTAURAR"),
                key=f"do_{scope_key}",
            )
        if do and up is not None:
            ok, err = _restore_tables_from_db_bytes(up.getvalue(), tables)
            if ok:
                st.success("✅ Restaurado. Recargando…")
                st.rerun()
            else:
                st.error(f"No se pudo restaurar: {err}")





def force_tel_keyboard(label: str):
    """Fuerza teclado numérico tipo 'teléfono' para el input con aria-label=label."""
    safe = label.replace("\\", "\\\\").replace('"', '\\"')
    components.html(
        f"""
        <script>
        (function() {{
          const label = "{safe}";
          let tries = 0;
          function apply() {{
            const inputs = window.parent.document.querySelectorAll('input[aria-label="' + label + '"]');
            if (!inputs || inputs.length === 0) {{
              tries++;
              if (tries < 30) setTimeout(apply, 200);
              return;
            }}
            inputs.forEach((el) => {{
              try {{
                el.setAttribute('type', 'tel');
                el.setAttribute('inputmode', 'numeric');
                el.setAttribute('pattern', '[0-9]*');
                el.setAttribute('autocomplete', 'off');
              }} catch (e) {{}}
            }});
          }}
          apply();
          setTimeout(apply, 500);
          setTimeout(apply, 1200);
        }})();
        </script>
        """,
        height=0,
    )


def autofocus_input(label: str):
    """Pone foco inmediato en un input por aria-label."""
    safe = label.replace("\\", "\\\\").replace('"', '\\"')
    components.html(
        f"""
        <script>
        (function() {{
          const label = "{safe}";
          let tries = 0;
          function focusIt() {{
            const el = window.parent.document.querySelector('input[aria-label="' + label + '"]');
            if (!el) {{
              tries++;
              if (tries < 40) setTimeout(focusIt, 120);
              return;
            }}
            try {{
              el.focus();
              el.select();
            }} catch (e) {{}}
          }}
          focusIt();
          setTimeout(focusIt, 300);
          setTimeout(focusIt, 900);
        }})();
        </script>
        """,
        height=0,
    )


# =========================
# DB INIT
# =========================
def init_db():
    conn = get_conn()
    c = conn.cursor()

    # --- FLEX/COLECTA ---
    c.execute("""
    CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ml_order_id TEXT UNIQUE,
        buyer TEXT,
        created_at TEXT
    );
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS order_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id INTEGER,
        sku_ml TEXT,
        title_ml TEXT,
        title_tec TEXT,
        qty INTEGER
    );
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS pickers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE
    );
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS picking_ots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ot_code TEXT UNIQUE,
        picker_id INTEGER,
        status TEXT,
        created_at TEXT,
        closed_at TEXT
    );
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS picking_tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ot_id INTEGER,
        sku_ml TEXT,
        title_ml TEXT,
        title_tec TEXT,
        qty_total INTEGER,
        qty_picked INTEGER DEFAULT 0,
        status TEXT DEFAULT 'PENDING',
        decided_at TEXT,
        confirm_mode TEXT,
        defer_rank INTEGER DEFAULT 0,
        defer_at TEXT
    );
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS picking_incidences (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ot_id INTEGER,
        sku_ml TEXT,
        qty_total INTEGER,
        qty_picked INTEGER,
        qty_missing INTEGER,
        reason TEXT,
        note TEXT,
        created_at TEXT
    );
    """)

    # --- CORTES (rollos / corte manual) ---
    c.execute("""
    CREATE TABLE IF NOT EXISTS cortes_tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ot_id INTEGER,
        sku_ml TEXT,
        title_ml TEXT,
        title_tec TEXT,
        qty_total INTEGER,
        created_at TEXT
    );
    """)


    c.execute("""
    CREATE TABLE IF NOT EXISTS ot_orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ot_id INTEGER,
        order_id INTEGER
    );
    """)
    # Maestro EAN/SKU (común)
    # Maestro EAN/SKU (común)
    c.execute("""
    CREATE TABLE IF NOT EXISTS sku_barcodes (
        barcode TEXT PRIMARY KEY,
        sku_ml TEXT
    );
    """)

    # --- Reparación robusta de schema para sku_barcodes (BD antiguas en Streamlit Cloud) ---
    # Esta tabla se puede reconstruir desde el maestro, así que es seguro "normalizarla".
    try:
        cols = [r[1] for r in c.execute("PRAGMA table_info(sku_barcodes);").fetchall()]
        cols_set = set(cols or [])
        if "barcode" not in cols_set:
            # Algunos DB viejos tenían 'ean' u otro nombre: recrear
            c.execute("ALTER TABLE sku_barcodes RENAME TO sku_barcodes_old;")
            c.execute("CREATE TABLE IF NOT EXISTS sku_barcodes (barcode TEXT PRIMARY KEY, sku_ml TEXT);")
        else:
            # Asegurar sku_ml
            if "sku_ml" not in cols_set and "sku" in cols_set:
                try:
                    c.execute("ALTER TABLE sku_barcodes ADD COLUMN sku_ml TEXT;")
                    c.execute("UPDATE sku_barcodes SET sku_ml=sku WHERE (sku_ml IS NULL OR sku_ml='');")
                except Exception:
                    pass
            cols_now = [r[1] for r in c.execute("PRAGMA table_info(sku_barcodes);").fetchall()]
            if "sku_ml" not in set(cols_now):
                # no se pudo agregar -> recrear limpia
                c.execute("ALTER TABLE sku_barcodes RENAME TO sku_barcodes_old;")
                c.execute("CREATE TABLE IF NOT EXISTS sku_barcodes (barcode TEXT PRIMARY KEY, sku_ml TEXT);")
        # Si quedó una tabla old, intentar copiar lo que se pueda
        if _db_table_exists(conn, "sku_barcodes_old"):
            old_cols = [r[1] for r in c.execute("PRAGMA table_info(sku_barcodes_old);").fetchall()]
            if "barcode" in old_cols:
                src_sku = "sku_ml" if "sku_ml" in old_cols else ("sku" if "sku" in old_cols else None)
                if src_sku:
                    try:
                        c.execute(f"INSERT OR IGNORE INTO sku_barcodes(barcode, sku_ml) SELECT barcode, {src_sku} FROM sku_barcodes_old;")
                    except Exception:
                        pass
            # no borramos old automáticamente; si quieres limpiar, se puede en un mantenimiento futuro
    except Exception:
        # Si algo falla aquí, no botamos la app; solo dejamos la tabla como esté.
        pass


    # Links / publicaciones (para ver fotos)
    c.execute("""
    CREATE TABLE IF NOT EXISTS sku_publications (
        sku_ml TEXT PRIMARY KEY,
        ml_item_id TEXT,
        title TEXT,
        link TEXT,
        image_url TEXT,
        updated_at TEXT
    );
    """)

    # Único flujo operativo: Picking; documentos se gestionan por tanda.

    # --- MIGRACIONES SUAVES (para BD antiguas) ---
    def _cols(table: str) -> set:
        try:
            c.execute(f"PRAGMA table_info({table});")
            return {r[1] for r in c.fetchall()}
        except Exception:
            return set()

    def _ensure_col(table: str, col: str, ddl: str):
        cols = _cols(table)
        if col in cols:
            return
        try:
            c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl};")
        except Exception:
            # Si falla (por locks o tablas raras), no botar la app.
            pass

        # picking_tasks (nuevas columnas para reordenar por "Surtido en venta")
    _ensure_col("picking_tasks", "defer_rank", "INTEGER DEFAULT 0")
    _ensure_col("picking_tasks", "defer_at", "TEXT")
    _ensure_col("picking_tasks", "family", "TEXT")
    _ensure_col("picking_ots", "model", "TEXT")
    _ensure_col("picking_ots", "batch_key", "TEXT")
    _ensure_col("picking_ots", "batch_label", "TEXT")
    _ensure_col("picking_incidences", "note", "TEXT")


    # sku_publications
    _ensure_col("sku_publications", "sku_ml", "TEXT")
    _ensure_col("sku_publications", "ml_item_id", "TEXT")
    _ensure_col("sku_publications", "title", "TEXT")
    _ensure_col("sku_publications", "link", "TEXT")
    _ensure_col("sku_publications", "image_url", "TEXT")
    _ensure_col("sku_publications", "updated_at", "TEXT")

    # --- Respaldo persistente PICKING (aislado del resto de módulos) ---
    c.execute("""
    CREATE TABLE IF NOT EXISTS picking_backup_queue (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_type TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        attempts INTEGER NOT NULL DEFAULT 0,
        last_error TEXT,
        created_at TEXT NOT NULL,
        last_attempt_at TEXT,
        sent_at TEXT
    );
    """)
    c.execute("""
    CREATE TABLE IF NOT EXISTS picking_sync_meta (
        key TEXT PRIMARY KEY,
        value TEXT,
        updated_at TEXT
    );
    """)
    try:
        c.execute("CREATE INDEX IF NOT EXISTS idx_picking_backup_queue_status_id ON picking_backup_queue(status,id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_picking_ots_batch_key ON picking_ots(batch_key)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_picking_tasks_ot_status ON picking_tasks(ot_id,status)")
    except Exception:
        pass

    conn.commit()
    from manifiestos_etiquetas import create_schema
    create_schema(conn)
    conn.close()

# =========================
# MAESTRO SKU/EAN (AUTO)
# =========================
def load_master_from_path(path: str) -> tuple[dict, dict, dict, list]:
    inv_map_sku: dict[str, str] = {}
    familia_map_sku: dict[str, str] = {}
    barcode_to_sku: dict[str, str] = {}
    conflicts: list = []

    if not path or not os.path.exists(path):
        return inv_map_sku, familia_map_sku, barcode_to_sku, conflicts

    df = pd.read_excel(path, dtype=str)
    cols = df.columns.tolist()
    lower = [str(c).strip().lower() for c in cols]

    sku_col = None
    if "sku" in lower:
        sku_col = cols[lower.index("sku")]

    tech_col = None
    for cand in ["artículo", "articulo", "descripcion", "descripción", "nombre", "producto", "detalle"]:
        if cand in lower:
            tech_col = cols[lower.index(cand)]
            break

    fam_col = None
    # columna nueva: "Familia"
    for cand in ["familia", "family"]:
        if cand in lower:
            fam_col = cols[lower.index(cand)]
            break

    barcode_col = None
    for cand in ["codigos de barras", "códigos de barras", "codigo de barras", "código de barras", "barcode", "ean", "eans"]:
        if cand in lower:
            barcode_col = cols[lower.index(cand)]
            break

    # Fallback por si el archivo no trae headers claros
    if sku_col is None or tech_col is None:
        df0 = pd.read_excel(path, header=None, dtype=str)
        if df0.shape[1] >= 2:
            a, b = df0.columns[0], df0.columns[1]
            sample = df0.head(200)

            def score(series):
                s = 0
                for v in series:
                    if re.fullmatch(r"\d{4,}", normalize_sku(v)):
                        s += 1
                return s

            sa, sb = score(sample[a]), score(sample[b])
            if sb >= sa:
                sku_col, tech_col = b, a
            else:
                sku_col, tech_col = a, b
            df = df0
            barcode_col = None
            fam_col = None

    for _, r in df.iterrows():
        sku = normalize_sku(r.get(sku_col, ""))
        if not sku:
            continue

        tech = str(r.get(tech_col, "")).strip() if tech_col is not None else ""
        if tech and tech.lower() != "nan":
            inv_map_sku[sku] = tech

        if fam_col is not None:
            fam = str(r.get(fam_col, "")).strip()
            if fam and fam.lower() != "nan":
                familia_map_sku[sku] = fam

        if barcode_col is not None:
            codes = split_barcodes(r.get(barcode_col, ""))
            for code in codes:
                if code in barcode_to_sku and barcode_to_sku[code] != sku:
                    conflicts.append((code, barcode_to_sku[code], sku))
                    continue
                barcode_to_sku[code] = sku

    return inv_map_sku, familia_map_sku, barcode_to_sku, conflicts



# Cache extra: lookup directo del título "tal cual" en el maestro (sin limpiar)
_MASTER_DF_CACHE = {"path": None, "mtime": None, "df": None}

def _load_master_df_cached(path: str):
    """Carga el Excel del maestro una sola vez (por mtime) para poder buscar el texto crudo."""
    if not path or not os.path.exists(path):
        return None
    try:
        mtime = os.path.getmtime(path)
    except Exception:
        mtime = None

    if (_MASTER_DF_CACHE.get("path") == path and _MASTER_DF_CACHE.get("mtime") == mtime
            and _MASTER_DF_CACHE.get("df") is not None):
        return _MASTER_DF_CACHE["df"]

    try:
        dfm = pd.read_excel(path, dtype=str)
    except Exception:
        return None

    _MASTER_DF_CACHE.update({"path": path, "mtime": mtime, "df": dfm})
    return dfm

def master_raw_title_lookup(path: str, sku: str) -> str:
    """Devuelve el texto EXACTO del maestro para ese SKU (tal cual viene en la celda)."""
    dfm = _load_master_df_cached(path)
    if dfm is None or dfm.empty:
        return ""
    cols = list(dfm.columns)
    lower = [str(c).strip().lower() for c in cols]

    # columna SKU
    sku_col = None
    if "sku" in lower:
        sku_col = cols[lower.index("sku")]
    if sku_col is None:
        return ""

    # preferir columnas típicas de descripción/título
    pref = [
        "descripción", "descripcion", "artículo", "articulo",
        "detalle", "producto", "nombre", "descripción pack", "nombre pack"
    ]
    title_col = None
    for cand in pref:
        if cand in lower:
            title_col = cols[lower.index(cand)]
            break
    # si no hay, tomar la primera no-SKU
    if title_col is None:
        for c in cols:
            if c != sku_col:
                title_col = c
                break
    if title_col is None:
        return ""

    target = normalize_sku(sku)
    if not target:
        return ""

    try:
        ser = dfm[sku_col].astype(str).map(normalize_sku)
        hits = dfm.loc[ser == target]
    except Exception:
        return ""

    if hits.empty:
        return ""

    val = hits.iloc[0][title_col]
    if val is None:
        return ""
    sval = str(val)
    if sval.lower() == "nan":
        return ""
    return sval


def upsert_barcodes_to_db(barcode_to_sku: dict):
    """Guarda el mapa EAN->SKU en SQLite.

    Importante: en Streamlit Cloud la DB puede quedar con esquemas antiguos. Esta función es defensiva:
    - verifica que exista la tabla y columnas esperadas (barcode, sku_ml)
    - si no calza, intenta recrearla (es seguro: se reconstruye desde el maestro)
    """
    if not barcode_to_sku:
        return

    # TODO QUIRÚRGICO: mantener toda la lógica original, pero asegurando que el cursor
    # se use dentro de la conexión (antes se cerraba el conn al salir del context manager).
    with db_conn(commit=True) as conn:
        c = conn.cursor()
        try:
            # asegurar tabla/columnas
            c.execute("""CREATE TABLE IF NOT EXISTS sku_barcodes (
                barcode TEXT PRIMARY KEY,
                sku_ml TEXT
            );""")
            cols = [r[1] for r in c.execute("PRAGMA table_info(sku_barcodes);").fetchall()]
            if "barcode" not in cols or ("sku_ml" not in cols):
                try:
                    c.execute("ALTER TABLE sku_barcodes RENAME TO sku_barcodes_old;")
                except Exception:
                    pass
                c.execute("DROP TABLE IF EXISTS sku_barcodes;")
                c.execute("""CREATE TABLE IF NOT EXISTS sku_barcodes (
                    barcode TEXT PRIMARY KEY,
                    sku_ml TEXT
                );""")
        except Exception:
            # si no podemos asegurar schema, no bloqueamos la app
            return

        try:
            c.execute("BEGIN;")
            for bc, sku in (barcode_to_sku or {}).items():
                bc = only_digits(bc)
                if not bc:
                    continue
                c.execute(
                    "INSERT OR REPLACE INTO sku_barcodes (barcode, sku_ml) VALUES (?, ?)",
                    (bc, str(sku).strip()),
                )
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass


def resolve_scan_to_sku(scan: str, barcode_to_sku: dict) -> str:
    raw = str(scan).strip()
    digits = only_digits(raw)

    # 1) Prefer in-memory map loaded from maestro
    if digits and digits in (barcode_to_sku or {}):
        return barcode_to_sku[digits]

    # 2) Fallback to DB map (persists across reruns)
    if digits:
        try:
            row = db_fetchone("SELECT sku_ml FROM sku_barcodes WHERE barcode=?", (digits,))
            if row and row[0]:
                return str(row[0]).strip()
        except Exception:
            pass

    # 3) As last resort: treat scan as SKU text
    return normalize_sku(raw)


def extract_location_suffix(text: str) -> str:
    """Extracts location/UBC suffix like '[UBC: 1234]' from a title."""
    t = str(text or "").strip()
    if not t:
        return ""
    # Common pattern in Aurora: '[UBC: 2260]' or '[ubc: 2260]'
    m = re.search(r"(\[\s*UBC\s*:\s*[^\]]+\])\s*$", t, flags=re.IGNORECASE)
    if m:
        return m.group(1).strip()
    # Sometimes without brackets: 'UBC: 2260' at end
    m = re.search(r"(UBC\s*:\s*\d+)\s*$", t, flags=re.IGNORECASE)
    if m:
        return f"[{m.group(1).strip()}]"
    return ""






@st.cache_data(show_spinner=False)
def get_master_cached(master_path: str) -> tuple[dict, dict, dict, list]:
    return load_master_from_path(master_path)


def master_bootstrap(master_path: str):
    inv_map_sku, familia_map_sku, barcode_to_sku, conflicts = get_master_cached(master_path)
    upsert_barcodes_to_db(barcode_to_sku)
    return inv_map_sku, familia_map_sku, barcode_to_sku, conflicts



# =========================
# PUBLICACIONES (Links + Fotos por SKU)
# =========================
ML_ITEM_RE = re.compile(r"\b(ML[A-Z]{1,3}[-]?(\d+))\b", re.IGNORECASE)

def extract_ml_item_id(value: str) -> str:
    """Extrae un ID tipo MLC123 o MLC-123 desde un link o celda."""
    s = str(value or "").strip()
    if not s:
        return ""
    m = ML_ITEM_RE.search(s)
    if not m:
        return ""
    prefix = m.group(1).upper().replace("-", "")
    # Normalizar: MLC123456789 (sin guión)
    return prefix

def import_publication_links_excel(file) -> pd.DataFrame:
    """Lee Excel de publicaciones/imágenes.

    Soporta formatos:
      - Antiguo: SKU, Id, Título, Link
      - Nuevo: SKU, Link, Imagen (y opcional ImgStatus / Título / Id)

    Devuelve columnas: sku_ml, ml_item_id, title, link, image_url
    """
    df = pd.read_excel(file, dtype=str)
    cols = {str(c).strip().lower(): c for c in df.columns}

    sku_c = cols.get("sku") or cols.get("codigo") or cols.get("código") or cols.get("sku_ml")
    id_c = cols.get("id") or cols.get("item") or cols.get("item_id") or cols.get("ml_item_id")
    title_c = cols.get("título") or cols.get("titulo") or cols.get("title")
    link_c = cols.get("link") or cols.get("url") or cols.get("enlace")
    image_c = cols.get("imagen") or cols.get("image_url") or cols.get("image") or cols.get("foto")

    if not sku_c:
        raise ValueError("No encuentro columna SKU en el Excel.")
    if not link_c and not id_c:
        raise ValueError("El Excel debe traer Link (url) o Id (item).")

    out = pd.DataFrame()
    out["sku_ml"] = df[sku_c].astype(str).map(normalize_sku)
    out["title"] = df[title_c].astype(str).fillna("").map(lambda x: str(x).strip()) if title_c else ""
    out["link"] = df[link_c].astype(str).fillna("").map(lambda x: str(x).strip()) if link_c else ""

    out["image_url"] = df[image_c].astype(str).fillna("").map(lambda x: str(x).strip()) if image_c else ""

    if id_c:
        out["ml_item_id"] = df[id_c].astype(str).fillna("").map(extract_ml_item_id)
    else:
        out["ml_item_id"] = ""
    need = out["ml_item_id"].eq("")
    if need.any():
        out.loc[need, "ml_item_id"] = out.loc[need, "link"].map(extract_ml_item_id)

    out = out[out["sku_ml"].ne("")].copy()
    out = out.drop_duplicates(subset=["sku_ml"], keep="last")
    return out[["sku_ml", "ml_item_id", "title", "link", "image_url"]]

def upsert_publications_to_db(df_pub: pd.DataFrame) -> tuple[int, int]:
    """Inserta/actualiza tabla sku_publications desde el Excel.

    Retorna (ok_count, missing_id_count).
    """
    if df_pub is None or df_pub.empty:
        return 0, 0

    ok = 0
    noid = 0

    with db_conn(commit=True) as conn:
        c = conn.cursor()

        for _, r in df_pub.iterrows():
            sku = normalize_sku(r.get("sku_ml", ""))
            if not sku:
                continue

            item_id = str(r.get("ml_item_id", "") or "").strip().upper().replace("-", "")
            title = str(r.get("title", "") or "").strip()
            link = str(r.get("link", "") or "").strip()
            image_url = str(r.get("image_url", "") or "").strip()

            if not item_id:
                noid += 1

            c.execute(
                """INSERT INTO sku_publications (sku_ml, ml_item_id, title, link, image_url, updated_at)
                   VALUES (?,?,?,?,?,?)
                   ON CONFLICT(sku_ml) DO UPDATE SET
                     ml_item_id=excluded.ml_item_id,
                     title=excluded.title,
                     link=excluded.link,
                     image_url=excluded.image_url,
                     updated_at=excluded.updated_at
                """,
                (sku, item_id, title, link, image_url, now_iso())
            )
            ok += 1

    return ok, noid

def get_publication_row(sku: str) -> dict:
    sku = normalize_sku(sku)
    if not sku:
        return {}
    row = db_fetchone("SELECT sku_ml, ml_item_id, title, link, image_url, updated_at FROM sku_publications WHERE sku_ml=?", (sku,))
    if not row:
        return {}
    return {"sku_ml": row[0], "ml_item_id": row[1], "title": row[2], "link": row[3], "image_url": row[4], "updated_at": row[5]}

OG_IMAGE_RE_1 = re.compile(
    r'<meta[^>]+(?:property|name)=["\']og:image(?::secure_url)?["\'][^>]+content=["\']([^"\']+)["\']',
    re.IGNORECASE
)
OG_IMAGE_RE_2 = re.compile(
    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']og:image(?::secure_url)?["\']',
    re.IGNORECASE
)
TWITTER_IMAGE_RE_1 = re.compile(
    r'<meta[^>]+(?:property|name)=["\']twitter:image(?::src)?["\'][^>]+content=["\']([^"\']+)["\']',
    re.IGNORECASE
)
TWITTER_IMAGE_RE_2 = re.compile(
    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']twitter:image(?::src)?["\']',
    re.IGNORECASE
)

def _extract_main_image_from_html(html_text: str) -> str:
    """Extrae una URL de imagen principal desde HTML sin depender de un único formato.

    Prioridad: og:image (incluye secure_url) -> twitter:image.
    """
    if not html_text:
        return ""
    for rx in (OG_IMAGE_RE_1, OG_IMAGE_RE_2, TWITTER_IMAGE_RE_1, TWITTER_IMAGE_RE_2):
        m = rx.search(html_text)
        if m:
            return (m.group(1) or "").strip()
    return ""


@st.cache_data(show_spinner=False, ttl=24*3600)
def publication_main_image_from_html(link: str) -> str:
    """Devuelve 1 URL de imagen principal leyendo metatags desde el HTML.

    Sin API: usa el link público de la publicación (puede fallar si ML bloquea el request).
    """
    url = (link or "").strip()
    if not url:
        return ""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "es-CL,es;q=0.9,en;q=0.8",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            # Referer ayuda a evitar ciertos bloqueos básicos
            "Referer": "https://www.google.com/",
        }
        r = requests.get(url, headers=headers, timeout=12, allow_redirects=True)
        if r.status_code != 200:
            return ""
        html_text = r.text or ""
        return _extract_main_image_from_html(html_text)
    except Exception:
        return ""



def get_picture_urls_for_sku(sku: str) -> tuple[list[str], str]:
    """Retorna (urls, link_publicacion).

    Nuevo modo estable:
      - Usa image_url desde la tabla sku_publications (cargada desde links_con_imagenes.xlsx).
      - No hace scraping HTML ni requests a Mercado Libre.
    """
    row = get_publication_row(sku)
    if not row:
        return [], ""
    link = (row.get("link") or "").strip()
    image_url = (row.get("image_url") or "").strip()
    if image_url:
        return [image_url], link
    return [], link

def load_cortes_set(path: str = CORTES_FILE) -> set:
    """Carga listado de SKUs que requieren corte manual desde Excel (defensivo)."""
    # Cache en session_state para evitar leer el Excel en cada rerun
    try:
        ss = st.session_state
        if ss.get("_cortes_cache_path") == path and ss.get("_cortes_cache_skus") is not None:
            return set(ss.get("_cortes_cache_skus") or [])
    except Exception:
        pass

    try:
        if not path or not os.path.exists(path):
            return set()
        df = pd.read_excel(path, dtype=str)
    except Exception:
        return set()

    try:
        cols = {str(c).strip().upper(): c for c in df.columns}
        col_sku = cols.get("SKU") or cols.get("SKUS") or cols.get("CODIGO") or cols.get("CÓDIGO")
        if not col_sku:
            col_sku = df.columns[0]

        skus = set()
        for v in df[col_sku].fillna("").tolist():
            s = normalize_sku(v)
            if s:
                skus.add(s)

        try:
            st.session_state["_cortes_cache_path"] = path
            st.session_state["_cortes_cache_skus"] = list(skus)
        except Exception:
            pass

        return skus
    except Exception:
        return set()
# =========================
# PARSER PDF MANIFIESTO
# =========================

def parse_manifest_pdf(uploaded_file) -> pd.DataFrame:
    """
    Parser robusto para Manifiesto PDF (etiquetas).

    Cubre casos reales de ML donde el PDF puede traer, en cualquier orden:
      - "Venta: <id> SKU:<sku>" en el mismo renglón
      - "Pack ID: ... SKU:<sku>" en un renglón y luego "Venta: <id> Cantidad: <n>" en el siguiente
      - "SKU:<sku>" en un renglón y "Cantidad:<n>" en el siguiente
      - Varias ocurrencias de SKU/Cantidad dentro de un mismo renglón

    Regla: cada vez que se detecta una Cantidad, si existe un SKU "vigente" y una Venta vigente,
    se crea un registro (línea) para esa venta+sku.
    """
    if not HAS_PDF_LIB:
        raise RuntimeError("Falta pdfplumber. Agrega 'pdfplumber' a requirements.txt")

    records: list[dict] = []

    re_venta = re.compile(r"\bVenta\s*[:#]?\s*([0-9]+)\b", re.IGNORECASE)
    re_sku = re.compile(r"\bSKU\s*[:#]?\s*([0-9A-Za-z.\-]+)\b", re.IGNORECASE)
    re_qty = re.compile(r"\bCantidad\s*[:#]?\s*([0-9]+)\b", re.IGNORECASE)

    def _is_noise_line(s: str) -> bool:
        low = (s or "").strip().lower()
        if not low:
            return True
        bad = [
            "código carrier", "codigo carrier", "firma carrier",
            "fecha y hora", "despacha tus productos", "identifi",
        ]
        if any(b in low for b in bad):
            return True
        if re.fullmatch(r"[0-9 .:/\-]+", low):
            return True
        return False

    def _maybe_buyer(line: str) -> str:
        # Quitamos cosas típicas que se pegan al nombre (ej: "Diámetro de la cupla: ...")
        # sin ser demasiado agresivos.
        cut_tokens = ["diámetro", "diametro", "color:", "acabado:", "pack id", "sku", "cantidad", "venta:"]
        low = line.lower()
        for tok in cut_tokens:
            idx = low.find(tok)
            if idx > 0:
                return line[:idx].strip()
        return line.strip()

    with pdfplumber.open(uploaded_file) as pdf:
        for page_idx, page in enumerate(pdf.pages, start=1):
            text = (page.extract_text() or "").replace("\r", "\n")
            lines = [ln.strip() for ln in text.splitlines() if ln and str(ln).strip()]

            current_order: str | None = None
            current_buyer: str = ""

            # SKU "vigente" para el próximo "Cantidad"
            sku_current: str | None = None

            # SKU visto antes de que aparezca la Venta (caso: "Pack ID ... SKU:xxxx" y luego "Venta ... Cantidad ...")
            pending_sku_before_order: str | None = None

            for line in lines:
                if _is_noise_line(line):
                    continue

                # Capturar Venta (no reseteamos SKU aquí; hay PDFs donde el SKU viene en la línea anterior)
                mv = re_venta.search(line)
                if mv:
                    current_order = mv.group(1).strip()
                    current_buyer = ""
                    # Si hay un SKU pendiente (visto antes de la venta), lo activamos
                    if pending_sku_before_order and not sku_current:
                        sku_current = pending_sku_before_order
                        pending_sku_before_order = None

                # Buyer: primera línea razonable después de "Venta:" que no sea metadata
                if current_order and not current_buyer:
                    low = line.lower()
                    if (not _is_noise_line(line)
                        and "venta" not in low
                        and "sku" not in low
                        and "cantidad" not in low
                        and ":" not in line  # evita "Color:" etc
                        and len(line) <= 120):
                        cand = _maybe_buyer(line)
                        if cand and len(cand) >= 3:
                            current_buyer = cand

                # Tokenizar SKU y Cantidad en orden de aparición en el renglón
                tokens = []
                for ms in re_sku.finditer(line):
                    tokens.append((ms.start(), "SKU", normalize_sku(ms.group(1))))
                for mq in re_qty.finditer(line):
                    try:
                        qv = int(mq.group(1))
                    except Exception:
                        qv = 0
                    tokens.append((mq.start(), "QTY", qv))
                tokens.sort(key=lambda x: x[0])

                for _, kind, val in tokens:
                    if kind == "SKU":
                        if current_order:
                            sku_current = val
                        else:
                            pending_sku_before_order = val
                    elif kind == "QTY":
                        qty = int(val) if val is not None else 0
                        if current_order and sku_current and qty > 0:
                            records.append(
                                {
                                    "ml_order_id": str(current_order).strip(),
                                    "buyer": str(current_buyer or "").strip(),
                                    "sku_ml": str(sku_current).strip(),
                                    "title_ml": "",
                                    "qty": qty,
                                    "pagina_ml": page_idx,
                                }
                            )
                            # Importante: NO limpiamos sku_current aquí, porque puede venir otra Cantidad asociada
                            # al mismo SKU en el mismo bloque (raro, pero seguro).

    return pd.DataFrame(records, columns=["ml_order_id", "buyer", "sku_ml", "title_ml", "qty", "pagina_ml"])



# =========================
# AUTO-CARGA PUBLICACIONES (desde repo)
# =========================
def publications_bootstrap(path: str = PUBLICATIONS_FILE):
    """Carga/actualiza automáticamente los links de publicaciones desde el repo.
    - Evita recargar en cada rerun usando mtime.
    - No requiere upload manual desde el panel administrador.
    """
    ss = st.session_state
    cache_key = "_pub_links_mtime"

    if not path or not os.path.exists(path):
        ss["_pub_links_status"] = ("missing", str(path or ""))
        return 0, 0, False

    try:
        mtime = os.path.getmtime(path)
    except Exception:
        mtime = None

    if ss.get(cache_key) == mtime and ss.get("_pub_links_loaded", False):
        return int(ss.get("_pub_links_ok", 0) or 0), int(ss.get("_pub_links_noid", 0) or 0), False

    try:
        dfp = import_publication_links_excel(path)
        ok_n, noid_n = upsert_publications_to_db(dfp)
        ss[cache_key] = mtime
        ss["_pub_links_loaded"] = True
        ss["_pub_links_ok"] = int(ok_n or 0)
        ss["_pub_links_noid"] = int(noid_n or 0)
        ss["_pub_links_status"] = ("ok", str(path))
        return int(ok_n or 0), int(noid_n or 0), True
    except Exception as e:
        ss["_pub_links_status"] = ("err", str(e))
        return 0, 0, False



# =========================
# IMPORTAR VENTAS (FLEX)
# =========================
def import_sales_excel(file) -> pd.DataFrame:
    """Importa reporte de ventas ML.

    Importante: en los reportes de ML, los envíos con varios productos vienen con una fila
    de cabecera 'Paquete de X productos' (sin SKU / sin unidades) y luego X filas con los ítems.
    Para que el KPI 'Ventas' refleje lo que tú ves por colores (paquetes/envíos), agrupamos esos
    ítems bajo el ID de la fila cabecera.
    """
    df = pd.read_excel(file, header=[4, 5])
    df.columns = [" | ".join([str(x) for x in col if str(x) != "nan"]) for col in df.columns]

    COLUMN_ORDER_ID = "Ventas | # de venta"
    COLUMN_STATUS = "Ventas | Estado"
    COLUMN_QTY = "Ventas | Unidades"
    COLUMN_SKU = "Publicaciones | SKU"
    COLUMN_TITLE = "Publicaciones | Título de la publicación"
    COLUMN_BUYER = "Compradores | Comprador"

    required = [COLUMN_ORDER_ID, COLUMN_STATUS, COLUMN_QTY, COLUMN_SKU, COLUMN_TITLE, COLUMN_BUYER]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Faltan columnas: {missing}")

    # Normalizamos a texto para trabajar seguro
    work = df[required].copy()
    work.columns = ["ml_order_id", "status", "qty", "sku_ml", "title_ml", "buyer"]

    # Helpers
    def _clean_str(x) -> str:
        if pd.isna(x):
            return ""
        return str(x).strip()

    records = []
    current_pkg_id = None
    current_pkg_buyer = ""
    remaining_items = 0

    pkg_re = re.compile(r"^Paquete\s+de\s+(\d+)\s+productos?$", re.IGNORECASE)

    for _, r in work.iterrows():
        status = _clean_str(r.get("status"))
        ml_id = _clean_str(r.get("ml_order_id"))
        buyer = _clean_str(r.get("buyer"))
        sku = _clean_str(r.get("sku_ml"))
        title = _clean_str(r.get("title_ml"))
        qty = pd.to_numeric(r.get("qty"), errors="coerce")

        # Detecta fila cabecera del paquete (no trae SKU/qty)
        m = pkg_re.match(status)
        if m:
            try:
                remaining_items = int(m.group(1))
            except Exception:
                remaining_items = 0
            current_pkg_id = ml_id if ml_id else None
            current_pkg_buyer = buyer
            continue

        # Filas sin SKU/qty -> se ignoran
        if not sku or pd.isna(qty):
            continue

        qty_int = int(qty) if not pd.isna(qty) else 0
        if qty_int <= 0:
            continue

        sku_norm = normalize_sku(sku)

        # Si estamos dentro de un paquete, agrupamos bajo el ID del paquete
        if current_pkg_id and remaining_items > 0:
            records.append(
                {
                    "ml_order_id": current_pkg_id,
                    "buyer": current_pkg_buyer or buyer,
                    "sku_ml": sku_norm,
                    "title_ml": title,
                    "qty": qty_int,
                }
            )
            remaining_items -= 1
            if remaining_items <= 0:
                current_pkg_id = None
                current_pkg_buyer = ""
            continue

        # Venta normal (1 producto)
        records.append(
            {
                "ml_order_id": ml_id,
                "buyer": buyer,
                "sku_ml": sku_norm,
                "title_ml": title,
                "qty": qty_int,
            }
        )

    out = pd.DataFrame(records, columns=["ml_order_id", "buyer", "sku_ml", "title_ml", "qty"])
    return out
def _next_picker_numbers(existing_names: list[str], qty: int) -> list[int]:
    nums = []
    for pname in existing_names or []:
        m = re.fullmatch(r"P(\d+)", str(pname or "").strip().upper())
        if m:
            nums.append(int(m.group(1)))
    start_n = (max(nums) + 1) if nums else 1
    return list(range(start_n, start_n + int(qty)))


def _get_current_picker_names() -> list[str]:
    conn = get_conn()
    c = conn.cursor()
    try:
        c.execute("SELECT name FROM pickers ORDER BY id")
        rows = [str(r[0]) for r in c.fetchall()]
    except Exception:
        rows = []
    conn.close()
    return rows


def _build_picking_batch_label(source_label: str | None, model: str, picker_names: list[str]) -> str:
    src = str(source_label or "Manifiesto").strip() or "Manifiesto"
    picks = ", ".join(picker_names) if picker_names else "Sin pickeadores"
    model_txt = "Por ventas" if str(model or "VENTAS").upper().strip() == "VENTAS" else "Por SKU"
    return f"{src} · {model_txt} · {picks}"


def _get_picking_batches_summary() -> list[dict]:
    conn = get_conn()
    c = conn.cursor()
    rows = []
    try:
        c.execute("""
            SELECT
                COALESCE(po.batch_key, po.ot_code, 'SIN_LOTE') AS batch_key,
                COALESCE(MAX(NULLIF(po.batch_label,'')), MAX(po.ot_code), 'Lote') AS batch_label,
                MIN(po.created_at) AS created_at,
                GROUP_CONCAT(DISTINCT pk.name) AS pickers,
                COUNT(DISTINCT po.id) AS ots,
                COUNT(DISTINCT oo.order_id) AS orders_count,
                SUM(CASE WHEN pt.status='PENDING' THEN 1 ELSE 0 END) AS pending_tasks,
                SUM(CASE WHEN pt.status IN ('DONE','INCIDENCE') THEN 1 ELSE 0 END) AS done_tasks,
                COUNT(DISTINCT CASE WHEN po.status='OPEN' THEN po.id END) AS open_ots
            FROM picking_ots po
            JOIN pickers pk ON pk.id = po.picker_id
            LEFT JOIN ot_orders oo ON oo.ot_id = po.id
            LEFT JOIN picking_tasks pt ON pt.ot_id = po.id
            GROUP BY COALESCE(po.batch_key, po.ot_code, 'SIN_LOTE')
            ORDER BY MIN(po.created_at) DESC, batch_key DESC
        """)
        for batch_key, batch_label, created_at, pickers, ots, orders_count, pending_tasks, done_tasks, open_ots in c.fetchall():
            pending_tasks = int(pending_tasks or 0)
            done_tasks = int(done_tasks or 0)
            total_tasks = pending_tasks + done_tasks
            pct = 0.0 if total_tasks == 0 else round((done_tasks * 100.0) / total_tasks, 1)
            rows.append({
                "batch_key": str(batch_key),
                "batch_label": str(batch_label or "Lote"),
                "created_at": created_at,
                "pickers": str(pickers or ""),
                "ots": int(ots or 0),
                "orders_count": int(orders_count or 0),
                "pending_tasks": pending_tasks,
                "done_tasks": done_tasks,
                "total_tasks": total_tasks,
                "open_ots": int(open_ots or 0),
                "progress_pct": pct,
            })
    except Exception:
        rows = []
    conn.close()
    return rows



# =========================
# REPARTO DINÁMICO PICKING POR SKU + FAMILIA
# =========================
SKU_BALANCE_WEIGHT = 0.70
UNIT_BALANCE_WEIGHT = 0.30
FAMILY_SPLIT_TOLERANCE = 1.20  # 20% sobre la carga objetivo antes de dividir una familia


def _prepare_sku_family_workload(sales_df: pd.DataFrame, familia_map_sku: dict | None = None):
    """Normaliza ventas y devuelve totales únicos por Familia + SKU.

    La familia directa del maestro tiene prioridad. Para packs/SKU sin familia directa
    se conserva el fallback histórico por los primeros 6 dígitos del SKU.
    """
    familia_map_sku = familia_map_sku or {}
    dfw = sales_df.copy()
    if dfw.empty:
        return pd.DataFrame(columns=["family", "sku_ml", "qty"]), {}

    dfw["sku_ml"] = dfw["sku_ml"].map(normalize_sku)
    dfw = dfw[dfw["sku_ml"].ne("")].copy()
    if dfw.empty:
        return pd.DataFrame(columns=["family", "sku_ml", "qty"]), {}

    dfw["qty"] = pd.to_numeric(dfw["qty"], errors="coerce").fillna(0).astype(int)
    dfw = dfw[dfw["qty"] > 0].copy()

    title_ml_by_sku = {}
    if "title_ml" in dfw.columns:
        for sku, g in dfw.groupby("sku_ml"):
            title = ""
            for value in g["title_ml"].tolist():
                value = str(value or "").strip()
                if value and value.lower() != "nan":
                    title = value
                    break
            title_ml_by_sku[str(sku)] = title

    fam_counts = {}
    try:
        for key, value in familia_map_sku.items():
            base_sku = normalize_sku(key)
            fam = str(value or "").strip()
            if not base_sku or len(base_sku) < 6 or not fam or fam.lower() == "nan":
                continue
            pref6 = base_sku[:6]
            fam_counts.setdefault(pref6, {})
            fam_counts[pref6][fam] = fam_counts[pref6].get(fam, 0) + 1
    except Exception:
        fam_counts = {}

    fam_prefix6 = {}
    for pref6, fam_map in fam_counts.items():
        fam_prefix6[pref6] = sorted(fam_map.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]

    def _fam_for_sku(sku: str) -> str:
        direct = str(familia_map_sku.get(sku, "") or "").strip()
        if direct and direct.lower() != "nan":
            return direct
        sku_norm = normalize_sku(sku)
        if not sku_norm:
            return "Sin Familia"
        inferred = fam_prefix6.get(sku_norm[:6], "")
        return inferred if inferred else "Sin Familia"

    dfw["family"] = dfw["sku_ml"].map(_fam_for_sku)
    grp = dfw.groupby(["family", "sku_ml"], as_index=False)["qty"].sum()
    grp["qty"] = grp["qty"].astype(int)
    return grp, title_ml_by_sku


def _normalized_picker_load(sku_count: int, units: int, target_skus: float, target_units: float) -> float:
    sku_ratio = (float(sku_count) / float(target_skus)) if target_skus > 0 else 0.0
    unit_ratio = (float(units) / float(target_units)) if target_units > 0 else 0.0
    return (SKU_BALANCE_WEIGHT * sku_ratio) + (UNIT_BALANCE_WEIGHT * unit_ratio)


def _plan_sku_family_distribution(
    sales_df: pd.DataFrame,
    num_pickers: int,
    familia_map_sku: dict | None = None,
    cortes_set: set | None = None,
    picker_names: list[str] | None = None,
) -> dict:
    """Planifica el reparto SKU/Familia sin escribir en BD.

    Reglas:
      1. Un SKU completo siempre queda en un solo picker.
      2. La familia se mantiene junta mientras no rompa el equilibrio.
      3. Una familia se divide en el mínimo número razonable de pickers; sólo puede abrirse
         un picker adicional si el ajuste fino demuestra que mejora el equilibrio.
      4. El balance pondera 70% SKU distintos y 30% unidades.
      5. La cantidad de pickers es totalmente dinámica.
    """
    n = max(1, int(num_pickers or 1))
    cortes_set = {normalize_sku(x) for x in (cortes_set or set()) if normalize_sku(x)}
    grp, title_ml_by_sku = _prepare_sku_family_workload(sales_df, familia_map_sku)

    names = [str(x).strip() for x in (picker_names or []) if str(x).strip()]
    if len(names) < n:
        names.extend([f"P{i+1}" for i in range(len(names), n)])
    names = names[:n]

    if grp.empty:
        summary = pd.DataFrame([
            {"Picker": names[i], "SKU": 0, "Unidades": 0, "Familias": 0, "Detalle familias": "-", "Carga estimada": "0%"}
            for i in range(n)
        ])
        return {
            "assignments": [[] for _ in range(n)],
            "title_ml_by_sku": title_ml_by_sku,
            "summary": summary,
            "metrics": {
                "total_skus": 0, "total_units": 0, "total_families": 0,
                "split_families": 0, "split_family_names": [],
                "avg_skus": 0.0, "avg_units": 0.0,
                "min_skus": 0, "max_skus": 0, "min_units": 0, "max_units": 0,
                "sku_imbalance_pct": 0.0, "unit_imbalance_pct": 0.0,
                "max_weighted_deviation_pct": 0.0, "quality": "Sin carga",
            },
        }

    grp = grp.copy()
    grp["is_corte"] = grp["sku_ml"].map(lambda x: normalize_sku(x) in cortes_set)
    work_grp = grp[~grp["is_corte"]].copy()

    total_skus = int(len(work_grp))
    total_units = int(work_grp["qty"].sum()) if not work_grp.empty else 0
    target_skus = (float(total_skus) / n) if total_skus > 0 else 1.0
    target_units = (float(total_units) / n) if total_units > 0 else 1.0

    family_infos = []
    for fam, fam_all in grp.groupby("family", sort=False):
        fam_all = fam_all.copy()
        fam_work = fam_all[~fam_all["is_corte"]].copy()
        fam_cortes = fam_all[fam_all["is_corte"]].copy()
        fam_skus = int(len(fam_work))
        fam_units = int(fam_work["qty"].sum()) if not fam_work.empty else 0
        fam_score = _normalized_picker_load(fam_skus, fam_units, target_skus, target_units)

        if fam_skus <= 0:
            chunk_count = 1
        else:
            # SKU es la señal principal: ningún bloque de familia debería superar
            # por sí solo el objetivo de SKU + 20% si puede evitarse. La carga
            # ponderada (SKU + unidades) actúa como segunda señal.
            sku_upper = max(1.0, target_skus * FAMILY_SPLIT_TOLERANCE)
            chunks_by_sku = max(1, int(math.ceil(float(fam_skus) / sku_upper)))
            chunks_by_weight = max(1, int(math.ceil(fam_score / FAMILY_SPLIT_TOLERANCE)))
            chunk_count = max(chunks_by_sku, chunks_by_weight)
            chunk_count = min(chunk_count, n, fam_skus)

        chunks = [{"rows": [], "sku_count": 0, "units": 0} for _ in range(chunk_count)]

        if not fam_work.empty:
            ranked_rows = []
            for _, row in fam_work.iterrows():
                qty = int(row["qty"] or 0)
                indiv_score = _normalized_picker_load(1, qty, target_skus, target_units)
                ranked_rows.append((indiv_score, qty, str(row["sku_ml"]), row.to_dict()))
            ranked_rows.sort(key=lambda x: (-x[0], -x[1], x[2]))

            for _, qty, _, row_dict in ranked_rows:
                best_idx = min(
                    range(chunk_count),
                    key=lambda ci: (
                        _normalized_picker_load(
                            chunks[ci]["sku_count"] + 1,
                            chunks[ci]["units"] + qty,
                            target_skus,
                            target_units,
                        ),
                        chunks[ci]["sku_count"],
                        chunks[ci]["units"],
                        ci,
                    ),
                )
                chunks[best_idx]["rows"].append(row_dict)
                chunks[best_idx]["sku_count"] += 1
                chunks[best_idx]["units"] += qty

        # CORTES no consumen carga de picking PDA. Se mantienen junto a la primera
        # porción de su familia sólo para conservar trazabilidad de la OT.
        if not fam_cortes.empty:
            for _, row in fam_cortes.iterrows():
                chunks[0]["rows"].append(row.to_dict())

        family_infos.append({
            "family": str(fam),
            "score": fam_score,
            "sku_count": fam_skus,
            "units": fam_units,
            "chunks": chunks,
        })

    # Familias más pesadas primero para evitar que una grande quede sin capacidad al final.
    family_infos.sort(key=lambda x: (-x["score"], -x["sku_count"], -x["units"], x["family"]))
    # El ajuste fino puede abrir como máximo un picker adicional por familia sobre
    # la división mínima calculada. Evita que una familia termine dispersa por toda la bodega.
    family_picker_cap = {
        info["family"]: min(n, max(1, len(info["chunks"])) + 1)
        for info in family_infos
    }

    picker_state = [
        {"name": names[i], "rows": [], "sku_count": 0, "units": 0, "families": set()}
        for i in range(n)
    ]

    # Asignar los bloques globalmente, del más pesado al más liviano. La cantidad
    # de bloques de cada familia ya fue limitada arriba, por lo que este paso mejora
    # el balance sin fragmentar familias adicionales.
    all_chunks = []
    for fam_info in family_infos:
        family = fam_info["family"]
        for chunk in fam_info["chunks"]:
            all_chunks.append({"family": family, "chunk": chunk})

    all_chunks.sort(
        key=lambda item: (
            -_normalized_picker_load(
                item["chunk"]["sku_count"],
                item["chunk"]["units"],
                target_skus,
                target_units,
            ),
            -item["chunk"]["sku_count"],
            -item["chunk"]["units"],
            item["family"],
        )
    )

    family_used_pickers = {}
    for item in all_chunks:
        family = item["family"]
        chunk = item["chunk"]
        used_for_family = family_used_pickers.setdefault(family, set())
        available = [i for i in range(n) if i not in used_for_family]
        if not available:
            available = list(range(n))

        target_idx = min(
            available,
            key=lambda pi: (
                _normalized_picker_load(
                    picker_state[pi]["sku_count"] + int(chunk["sku_count"]),
                    picker_state[pi]["units"] + int(chunk["units"]),
                    target_skus,
                    target_units,
                ),
                picker_state[pi]["sku_count"],
                picker_state[pi]["units"],
                pi,
            ),
        )

        picker_state[target_idx]["rows"].extend(chunk["rows"])
        picker_state[target_idx]["sku_count"] += int(chunk["sku_count"])
        picker_state[target_idx]["units"] += int(chunk["units"])
        if chunk["rows"]:
            picker_state[target_idx]["families"].add(family)
        used_for_family.add(target_idx)

    # Ajuste fino: si el reparto por bloques aún queda fuera de la banda deseada,
    # mover SKU de una misma familia desde pickers sobrecargados hacia los más livianos.
    # Sólo se hace cuando mejora objetivamente el balance y se penaliza abrir una familia
    # en un picker adicional, para conservar la zonificación como prioridad.
    def _state_deviation(sku_count: int, units: int) -> float:
        sku_dev = abs(float(sku_count) - target_skus) / target_skus if target_skus > 0 else 0.0
        unit_dev = abs(float(units) - target_units) / target_units if target_units > 0 else 0.0
        return (SKU_BALANCE_WEIGHT * sku_dev) + (UNIT_BALANCE_WEIGHT * unit_dev)

    def _state_load_score(state: dict) -> float:
        return _normalized_picker_load(state["sku_count"], state["units"], target_skus, target_units)

    max_rebalance_steps = min(100, max(10, total_skus * 2))
    for _ in range(max_rebalance_steps):
        current_devs = [_state_deviation(x["sku_count"], x["units"]) for x in picker_state]
        current_max_dev = max(current_devs) if current_devs else 0.0
        if current_max_dev <= 0.20:
            break

        ranked_high = sorted(range(n), key=lambda i: (-_state_load_score(picker_state[i]), i))[:3]
        ranked_low = sorted(range(n), key=lambda i: (_state_load_score(picker_state[i]), i))[:3]
        best_move = None

        # Mapa actual de presencia de familia para calcular cuánto se fragmentaría.
        fam_presence = {}
        for pi, state in enumerate(picker_state):
            for row in state["rows"]:
                if bool(row.get("is_corte", False)):
                    continue
                fam = str(row.get("family") or "Sin Familia")
                fam_presence.setdefault(fam, set()).add(pi)

        for src in ranked_high:
            src_state = picker_state[src]
            if src_state["sku_count"] <= 1:
                continue

            src_families = {}
            for row in src_state["rows"]:
                if bool(row.get("is_corte", False)):
                    continue
                fam = str(row.get("family") or "Sin Familia")
                src_families.setdefault(fam, []).append(row)

            for dst in ranked_low:
                if dst == src:
                    continue
                dst_state = picker_state[dst]
                if _state_load_score(src_state) <= _state_load_score(dst_state):
                    continue

                other_devs = [current_devs[i] for i in range(n) if i not in (src, dst)]
                other_max = max(other_devs) if other_devs else 0.0

                for fam, fam_rows in src_families.items():
                    if not fam_rows:
                        continue

                    # Evaluamos prefijos por cantidad alta y baja. Como cada SKU pesa 70%,
                    # probar todos los tamaños permite acercarse al objetivo sin dividir SKU.
                    candidate_orders = [
                        sorted(fam_rows, key=lambda r: (-int(r.get("qty") or 0), str(r.get("sku_ml") or ""))),
                        sorted(fam_rows, key=lambda r: (int(r.get("qty") or 0), str(r.get("sku_ml") or ""))),
                    ]
                    seen_candidates = set()
                    for ordered in candidate_orders:
                        qty_acc = 0
                        for k in range(1, len(ordered) + 1):
                            qty_acc += int(ordered[k - 1].get("qty") or 0)
                            move_skus = tuple(sorted(str(r.get("sku_ml") or "") for r in ordered[:k]))
                            if move_skus in seen_candidates:
                                continue
                            seen_candidates.add(move_skus)

                            src_skus_new = src_state["sku_count"] - k
                            src_units_new = src_state["units"] - qty_acc
                            dst_skus_new = dst_state["sku_count"] + k
                            dst_units_new = dst_state["units"] + qty_acc
                            if src_skus_new < 0 or src_units_new < 0:
                                continue

                            src_dev_new = _state_deviation(src_skus_new, src_units_new)
                            dst_dev_new = _state_deviation(dst_skus_new, dst_units_new)
                            new_max_dev = max(other_max, src_dev_new, dst_dev_new)
                            if new_max_dev >= current_max_dev - 1e-9:
                                continue

                            before_presence = set(fam_presence.get(fam, set()))
                            after_presence = set(before_presence)
                            # Si salen todos los SKU de picking de esta familia desde src, deja de estar allí.
                            if k >= len(fam_rows):
                                after_presence.discard(src)
                            after_presence.add(dst)
                            spread_delta = max(0, len(after_presence) - len(before_presence))
                            if len(after_presence) > int(family_picker_cap.get(fam, n)):
                                continue

                            # Primero agotamos movimientos que NO aumentan la dispersión de familias.
                            # Sólo si ya no existen, se permite abrir un picker adicional para esa familia.
                            candidate = (
                                spread_delta,
                                new_max_dev,
                                k,
                                qty_acc,
                                src,
                                dst,
                                fam,
                                move_skus,
                            )
                            if best_move is None or candidate < best_move:
                                best_move = candidate

        if best_move is None:
            break

        _, new_max_dev, _, _, src, dst, fam, move_skus = best_move
        move_set = set(move_skus)
        moved_rows = []
        kept_rows = []
        for row in picker_state[src]["rows"]:
            is_match = (
                not bool(row.get("is_corte", False))
                and str(row.get("family") or "Sin Familia") == fam
                and str(row.get("sku_ml") or "") in move_set
            )
            if is_match:
                moved_rows.append(row)
            else:
                kept_rows.append(row)

        if not moved_rows:
            break
        moved_units = sum(int(r.get("qty") or 0) for r in moved_rows)
        picker_state[src]["rows"] = kept_rows
        picker_state[src]["sku_count"] -= len(moved_rows)
        picker_state[src]["units"] -= moved_units
        picker_state[dst]["rows"].extend(moved_rows)
        picker_state[dst]["sku_count"] += len(moved_rows)
        picker_state[dst]["units"] += moved_units

    # Métricas y control de familias realmente divididas en carga de PDA (excluye CORTES).
    family_picker_map = {}
    for pi, state in enumerate(picker_state):
        for row in state["rows"]:
            if bool(row.get("is_corte", False)):
                continue
            fam = str(row.get("family") or "Sin Familia")
            family_picker_map.setdefault(fam, set()).add(pi)

    split_family_names = sorted([fam for fam, pis in family_picker_map.items() if len(pis) > 1])
    total_families = len(family_picker_map)

    sku_counts = [int(x["sku_count"]) for x in picker_state]
    unit_counts = [int(x["units"]) for x in picker_state]
    avg_skus = (sum(sku_counts) / n) if n else 0.0
    avg_units = (sum(unit_counts) / n) if n else 0.0
    # Desviación máxima respecto del objetivo/promedio. Así un rango 18–27 con
    # objetivo 22,5 se informa como 20% (no como 40% entre extremos).
    sku_imbalance = (max(abs(x - avg_skus) for x in sku_counts) / avg_skus * 100.0) if avg_skus > 0 else 0.0
    unit_imbalance = (max(abs(x - avg_units) for x in unit_counts) / avg_units * 100.0) if avg_units > 0 else 0.0

    weighted_deviations = []
    for state in picker_state:
        sku_dev = abs(state["sku_count"] - target_skus) / target_skus if target_skus > 0 else 0.0
        unit_dev = abs(state["units"] - target_units) / target_units if target_units > 0 else 0.0
        weighted_deviations.append((SKU_BALANCE_WEIGHT * sku_dev) + (UNIT_BALANCE_WEIGHT * unit_dev))
    max_weighted_dev = max(weighted_deviations) if weighted_deviations else 0.0

    if total_skus == 0:
        quality = "Sin carga de picking"
    elif max_weighted_dev <= 0.20:
        quality = "Buena"
    elif max_weighted_dev <= 0.35:
        quality = "Aceptable"
    else:
        quality = "Revisar"

    summary_rows = []
    for state in picker_state:
        fams = sorted({
            str(row.get("family") or "Sin Familia")
            for row in state["rows"]
            if not bool(row.get("is_corte", False))
        })
        load_pct = _normalized_picker_load(state["sku_count"], state["units"], target_skus, target_units) * 100.0
        summary_rows.append({
            "Picker": state["name"],
            "SKU": int(state["sku_count"]),
            "Unidades": int(state["units"]),
            "Familias": len(fams),
            "Detalle familias": ", ".join(fams) if fams else "-",
            "Carga estimada": f"{load_pct:.0f}%",
        })

    return {
        "assignments": [state["rows"] for state in picker_state],
        "title_ml_by_sku": title_ml_by_sku,
        "summary": pd.DataFrame(summary_rows),
        "metrics": {
            "total_skus": total_skus,
            "total_units": total_units,
            "total_families": total_families,
            "split_families": len(split_family_names),
            "split_family_names": split_family_names,
            "avg_skus": avg_skus,
            "avg_units": avg_units,
            "min_skus": min(sku_counts) if sku_counts else 0,
            "max_skus": max(sku_counts) if sku_counts else 0,
            "min_units": min(unit_counts) if unit_counts else 0,
            "max_units": max(unit_counts) if unit_counts else 0,
            "sku_imbalance_pct": sku_imbalance,
            "unit_imbalance_pct": unit_imbalance,
            "max_weighted_deviation_pct": max_weighted_dev * 100.0,
            "quality": quality,
        },
    }


def _render_sku_distribution_preview(plan: dict):
    """Panel visual de validación previo a crear las OTs."""
    metrics = plan.get("metrics") or {}
    summary = plan.get("summary")

    st.subheader("Distribución propuesta por familia")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("SKU de picking", int(metrics.get("total_skus", 0) or 0))
    c2.metric("Unidades", int(metrics.get("total_units", 0) or 0))
    c3.metric("Familias", int(metrics.get("total_families", 0) or 0))
    c4.metric("Familias divididas", int(metrics.get("split_families", 0) or 0))

    if isinstance(summary, pd.DataFrame) and not summary.empty:
        st.dataframe(summary, use_container_width=True, hide_index=True)

    st.caption(
        f"Objetivo dinámico: {float(metrics.get('avg_skus', 0.0)):.1f} SKU y "
        f"{float(metrics.get('avg_units', 0.0)):.1f} unidades por picker. "
        f"Rango SKU: {int(metrics.get('min_skus', 0))}–{int(metrics.get('max_skus', 0))}. "
        f"Desviación máx. SKU: {float(metrics.get('sku_imbalance_pct', 0.0)):.1f}% · "
        f"Desviación máx. unidades: {float(metrics.get('unit_imbalance_pct', 0.0)):.1f}%."
    )

    quality = str(metrics.get("quality") or "").strip()
    if quality == "Buena":
        st.success("Calidad del reparto: BUENA. Se priorizaron las familias manteniendo una carga equilibrada.")
    elif quality == "Aceptable":
        st.info("Calidad del reparto: ACEPTABLE. La estructura de familias limita un balance más exacto sin fragmentarlas de más.")
    elif quality == "Revisar":
        st.warning("Calidad del reparto: REVISAR. Hay SKU o familias cuya carga individual dificulta igualar más a los pickers.")
    elif quality:
        st.info(f"Calidad del reparto: {quality}.")

    split_names = metrics.get("split_family_names") or []
    if split_names:
        st.caption("Familias divididas sólo por balance: " + ", ".join(map(str, split_names)))
    else:
        st.caption("No fue necesario dividir ninguna familia para esta carga.")


def _filter_sales_not_loaded_for_preview(sales_df: pd.DataFrame) -> pd.DataFrame:
    """Replica para la vista previa el filtro de ventas ya cargadas usado al anexar tandas."""
    if sales_df is None or sales_df.empty or "ml_order_id" not in sales_df.columns:
        return sales_df.copy() if isinstance(sales_df, pd.DataFrame) else pd.DataFrame()

    work = sales_df.copy()
    work["ml_order_id"] = work["ml_order_id"].astype(str).str.strip()
    conn = get_conn()
    c = conn.cursor()
    try:
        c.execute("""
            SELECT DISTINCT TRIM(o.ml_order_id)
            FROM ot_orders oo
            JOIN orders o ON o.id = oo.order_id
        """)
        existing = {str(r[0]).strip() for r in c.fetchall() if str(r[0]).strip()}
    except Exception:
        existing = set()
    conn.close()
    return work[~work["ml_order_id"].isin(existing)].copy()

def save_orders_and_build_ots(
    sales_df: pd.DataFrame,
    inv_map_sku: dict,
    num_pickers: int,
    model: str = "VENTAS",
    familia_map_sku: dict | None = None,
    source_label: str | None = None,
    dispatch_payload: dict | None = None,
):
    """
    Genera la tanda de picking.

    model:
      - "VENTAS" (actual): reparte ventas por OT y crea tareas por SKU dentro de esas ventas.
      - "SKU" (nuevo): agrupa SKUs por Familia (desde maestro) y asigna familias a OTs (batch picking).
        Nota: para evitar conflictos con pantallas antiguas, igual se mantiene la asignación de ventas->OT
        en ot_orders (registro de ventas por OT), pero las tareas de picking se construyen por familia/SKU.
    """
    model = (model or "VENTAS").upper().strip()
    if model not in ("VENTAS", "SKU"):
        model = "VENTAS"

    familia_map_sku = familia_map_sku or {}
    picker_names = [f"P{i+1}" for i in range(max(1, int(num_pickers or 1)))]
    batch_key = f"PK-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}-{''.join(random.choice(string.ascii_uppercase + string.digits) for _ in range(4))}"
    batch_label = _build_picking_batch_label(source_label, model, picker_names)

    conn = get_conn()
    c = conn.cursor()

    # SKUs que se van a CORTES (no aparecen en picking)
    cortes_set = load_cortes_set()

    # Reset corrida (no borra histórico; eso lo hace admin reset total)
    c.execute("DELETE FROM picking_tasks;")
    c.execute("DELETE FROM picking_incidences;")
    c.execute("DELETE FROM cortes_tasks;")
    c.execute("DELETE FROM ot_orders;")
    c.execute("DELETE FROM picking_ots;")
    c.execute("DELETE FROM pickers;")

    order_id_by_ml = {}
    for ml_order_id, g in sales_df.groupby("ml_order_id"):
        ml_order_id = str(ml_order_id).strip()
        buyer = str(g["buyer"].iloc[0]) if "buyer" in g.columns else ""
        created = now_iso()

        c.execute("SELECT id FROM orders WHERE ml_order_id = ?", (ml_order_id,))
        row = c.fetchone()
        if row:
            order_id = row[0]
            c.execute("UPDATE orders SET buyer=?, created_at=? WHERE id=?", (buyer, created, order_id))
            c.execute("DELETE FROM order_items WHERE order_id=?", (order_id,))
        else:
            c.execute("INSERT INTO orders (ml_order_id, buyer, created_at) VALUES (?,?,?)", (ml_order_id, buyer, created))
            order_id = c.lastrowid

        order_id_by_ml[ml_order_id] = order_id

        for _, r in g.iterrows():
            sku = normalize_sku(r["sku_ml"])
            qty = int(r["qty"])
            title_ml = str(r.get("title_ml", "") or "").strip()
            title_tec = inv_map_sku.get(sku, "")
            title_eff = title_tec if title_tec else title_ml

            c.execute(
                "INSERT INTO order_items (order_id, sku_ml, title_ml, title_tec, qty) VALUES (?,?,?,?,?)",
                (order_id, sku, title_eff, title_tec, qty)
            )

    # pickers
    picker_ids = []
    for name in picker_names:
        c.execute("INSERT INTO pickers (name) VALUES (?)", (name,))
        picker_ids.append(c.lastrowid)

    # ots
    ot_ids = []
    for pid in picker_ids:
        c.execute(
            "INSERT INTO picking_ots (ot_code, picker_id, status, created_at, closed_at, model, batch_key, batch_label) VALUES (?,?,?,?,?,?,?,?)",
            ("", pid, "OPEN", now_iso(), None, model, batch_key, batch_label)
        )
        ot_id = c.lastrowid
        ot_code = f"OT{ot_id:06d}"
        c.execute("UPDATE picking_ots SET ot_code=? WHERE id=?", (ot_code, ot_id))
        ot_ids.append(ot_id)

    # Mantener asignación de ventas -> OT (para compatibilidad con módulos existentes)
    unique_orders = sales_df[["ml_order_id"]].drop_duplicates().reset_index(drop=True)
    assignments = {}
    for idx, row in unique_orders.iterrows():
        ot_id = ot_ids[idx % len(ot_ids)]
        assignments[str(row["ml_order_id"]).strip()] = ot_id

    for idx, (ml_order_id, ot_id) in enumerate(assignments.items()):
        order_id = order_id_by_ml[ml_order_id]
        mesa = (idx % NUM_MESAS) + 1
        c.execute("INSERT INTO ot_orders (ot_id, order_id) VALUES (?,?)", (ot_id, order_id))

    if model == "VENTAS":
        # === Modelo actual (sin cambios) ===
        for ot_id in ot_ids:
            c.execute("""
                SELECT oi.sku_ml,
                       COALESCE(NULLIF(oi.title_tec,''), oi.title_ml) AS title,
                       MAX(COALESCE(oi.title_tec,'')) AS title_tec_any,
                       SUM(oi.qty) as total
                FROM ot_orders oo
                JOIN order_items oi ON oi.order_id = oo.order_id
                WHERE oo.ot_id = ?
                GROUP BY oi.sku_ml, title
                ORDER BY CAST(oi.sku_ml AS INTEGER), oi.sku_ml
            """, (ot_id,))
            rows = c.fetchall()
            for sku, title, title_tec_any, total in rows:
                if sku in cortes_set:
                    c.execute(
                        "INSERT INTO cortes_tasks (ot_id, sku_ml, title_ml, title_tec, qty_total, created_at) VALUES (?,?,?,?,?,?)",
                        (ot_id, sku, title, title_tec_any, int(total), now_iso())
                    )
                else:
                    c.execute("""
                    INSERT INTO picking_tasks (ot_id, sku_ml, title_ml, title_tec, qty_total, qty_picked, status, decided_at, confirm_mode, family)
                    VALUES (?,?,?,?,?,?,?,?,?,?)
                    """, (ot_id, sku, title, title_tec_any, int(total), 0, "PENDING", None, None, None))

        from manifiestos_etiquetas import save_with_picking
        save_with_picking(conn, batch_key, batch_label, dispatch_payload)
        conn.commit()
        conn.close()
        _picking_meta_set("intentional_reset", "")
        queue_picking_batch_snapshot(batch_key, motivo="LOTE_CREADO", include_lote_created=True)
        return {"created": True, "reason": "ok", "new_orders": int(sales_df["ml_order_id"].nunique()), "picker_names": picker_names, "batch_key": batch_key, "batch_label": batch_label}

    # === Modelo por SKU + Familia: prioridad familia + balance dinámico ===
    # El planificador trabaja con N pickers, conserva cada SKU completo y sólo divide
    # una familia cuando mantenerla íntegra rompe el balance objetivo.
    plan = _plan_sku_family_distribution(
        sales_df,
        len(ot_ids),
        familia_map_sku=familia_map_sku,
        cortes_set=cortes_set,
        picker_names=picker_names,
    )
    title_ml_by_sku = plan.get("title_ml_by_sku") or {}
    assigned_rows = plan.get("assignments") or []

    # Insertar exactamente los SKU asignados a cada OT. Un SKU nunca se duplica entre OTs.
    for ot_pos, ot_id in enumerate(ot_ids):
        rows = assigned_rows[ot_pos] if ot_pos < len(assigned_rows) else []
        rows = sorted(
            rows,
            key=lambda r: (
                str(r.get("family") or ""),
                int(str(r.get("sku_ml"))) if str(r.get("sku_ml", "")).isdigit() else 10**18,
                str(r.get("sku_ml") or ""),
            ),
        )

        for r in rows:
            fam = str(r.get("family") or "Sin Familia")
            sku = normalize_sku(r.get("sku_ml"))
            total = int(r.get("qty") or 0)
            if not sku or total <= 0:
                continue
            title_tec = inv_map_sku.get(sku, "") or ""
            title_ml = title_ml_by_sku.get(sku, "") or ""
            title_eff = title_tec.strip() if title_tec.strip() else title_ml.strip()

            if sku in cortes_set:
                c.execute(
                    "INSERT INTO cortes_tasks (ot_id, sku_ml, title_ml, title_tec, qty_total, created_at) VALUES (?,?,?,?,?,?)",
                    (ot_id, sku, title_eff, title_tec, total, now_iso())
                )
            else:
                c.execute("""
                    INSERT INTO picking_tasks (ot_id, sku_ml, title_ml, title_tec, qty_total, qty_picked, status, decided_at, confirm_mode, family)
                    VALUES (?,?,?,?,?,?,?,?,?,?)
                """, (ot_id, sku, title_eff, title_tec, total, 0, "PENDING", None, None, fam))

    from manifiestos_etiquetas import save_with_picking
    save_with_picking(conn, batch_key, batch_label, dispatch_payload)
    conn.commit()
    conn.close()
    _picking_meta_set("intentional_reset", "")
    queue_picking_batch_snapshot(batch_key, motivo="LOTE_CREADO", include_lote_created=True)
    return {"created": True, "reason": "ok", "new_orders": int(sales_df["ml_order_id"].nunique()), "picker_names": picker_names, "batch_key": batch_key, "batch_label": batch_label}


def append_orders_and_build_ots(
    sales_df: pd.DataFrame,
    inv_map_sku: dict,
    num_pickers: int,
    model: str = "VENTAS",
    familia_map_sku: dict | None = None,
    source_label: str | None = None,
    dispatch_payload: dict | None = None,
):
    """Agrega una nueva carga de picking sin borrar la tanda actual."""
    model = (model or "VENTAS").upper().strip()
    if model not in ("VENTAS", "SKU"):
        model = "VENTAS"

    familia_map_sku = familia_map_sku or {}
    sales_df = sales_df.copy()
    if sales_df.empty:
        return {"created": False, "reason": "empty", "new_orders": 0, "picker_names": []}

    sales_df["ml_order_id"] = sales_df["ml_order_id"].astype(str).str.strip()

    conn = get_conn()
    c = conn.cursor()

    try:
        c.execute("""
            SELECT DISTINCT TRIM(o.ml_order_id)
            FROM ot_orders oo
            JOIN orders o ON o.id = oo.order_id
        """)
        existing_loaded = {str(r[0]).strip() for r in c.fetchall() if str(r[0]).strip()}
    except Exception:
        existing_loaded = set()

    new_order_ids = [oid for oid in sales_df["ml_order_id"].drop_duplicates().tolist() if str(oid).strip() and str(oid).strip() not in existing_loaded]
    if not new_order_ids:
        conn.close()
        return {"created": False, "reason": "duplicate", "new_orders": 0, "picker_names": []}

    sales_df = sales_df[sales_df["ml_order_id"].isin(new_order_ids)].copy()

    existing_picker_names = _get_current_picker_names()
    picker_numbers = _next_picker_numbers(existing_picker_names, int(num_pickers))
    picker_names = [f"P{n}" for n in picker_numbers]
    batch_key = f"PK-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}-{''.join(random.choice(string.ascii_uppercase + string.digits) for _ in range(4))}"
    batch_label = _build_picking_batch_label(source_label, model, picker_names)

    cortes_set = load_cortes_set()
    order_id_by_ml = {}
    for ml_order_id, g in sales_df.groupby("ml_order_id"):
        ml_order_id = str(ml_order_id).strip()
        buyer = str(g["buyer"].iloc[0]) if "buyer" in g.columns else ""
        created = now_iso()

        c.execute("SELECT id FROM orders WHERE ml_order_id = ?", (ml_order_id,))
        row = c.fetchone()
        if row:
            order_id = row[0]
            c.execute("UPDATE orders SET buyer=?, created_at=? WHERE id=?", (buyer, created, order_id))
            c.execute("DELETE FROM order_items WHERE order_id=?", (order_id,))
        else:
            c.execute("INSERT INTO orders (ml_order_id, buyer, created_at) VALUES (?,?,?)", (ml_order_id, buyer, created))
            order_id = c.lastrowid

        order_id_by_ml[ml_order_id] = order_id

        for _, r in g.iterrows():
            sku = normalize_sku(r["sku_ml"])
            qty = int(r["qty"])
            title_ml = str(r.get("title_ml", "") or "").strip()
            title_tec = inv_map_sku.get(sku, "")
            title_eff = title_tec if title_tec else title_ml
            c.execute(
                "INSERT INTO order_items (order_id, sku_ml, title_ml, title_tec, qty) VALUES (?,?,?,?,?)",
                (order_id, sku, title_eff, title_tec, qty)
            )

    picker_ids = []
    for name in picker_names:
        c.execute("INSERT OR IGNORE INTO pickers (name) VALUES (?)", (name,))
        c.execute("SELECT id FROM pickers WHERE name=?", (name,))
        row = c.fetchone()
        if row:
            picker_ids.append(int(row[0]))

    ot_ids = []
    for pid in picker_ids:
        c.execute(
            "INSERT INTO picking_ots (ot_code, picker_id, status, created_at, closed_at, model, batch_key, batch_label) VALUES (?,?,?,?,?,?,?,?)",
            ("", pid, "OPEN", now_iso(), None, model, batch_key, batch_label)
        )
        ot_id = c.lastrowid
        ot_code = f"OT{ot_id:06d}"
        c.execute("UPDATE picking_ots SET ot_code=? WHERE id=?", (ot_code, ot_id))
        ot_ids.append(ot_id)

    unique_orders = sales_df[["ml_order_id"]].drop_duplicates().reset_index(drop=True)
    assignments = {}
    for idx, row in unique_orders.iterrows():
        ot_id = ot_ids[idx % len(ot_ids)]
        assignments[str(row["ml_order_id"]).strip()] = ot_id

    for ml_order_id, ot_id in assignments.items():
        order_id = order_id_by_ml[ml_order_id]
        c.execute("INSERT INTO ot_orders (ot_id, order_id) VALUES (?,?)", (ot_id, order_id))

    if model == "VENTAS":
        for ot_id in ot_ids:
            c.execute("""
                SELECT oi.sku_ml,
                       COALESCE(NULLIF(oi.title_tec,''), oi.title_ml) AS title,
                       MAX(COALESCE(oi.title_tec,'')) AS title_tec_any,
                       SUM(oi.qty) as total
                FROM ot_orders oo
                JOIN order_items oi ON oi.order_id = oo.order_id
                WHERE oo.ot_id = ?
                GROUP BY oi.sku_ml, title
                ORDER BY CAST(oi.sku_ml AS INTEGER), oi.sku_ml
            """, (ot_id,))
            rows = c.fetchall()
            for sku, title, title_tec_any, total in rows:
                if sku in cortes_set:
                    c.execute(
                        "INSERT INTO cortes_tasks (ot_id, sku_ml, title_ml, title_tec, qty_total, created_at) VALUES (?,?,?,?,?,?)",
                        (ot_id, sku, title, title_tec_any, int(total), now_iso())
                    )
                else:
                    c.execute("""
                    INSERT INTO picking_tasks (ot_id, sku_ml, title_ml, title_tec, qty_total, qty_picked, status, decided_at, confirm_mode, family)
                    VALUES (?,?,?,?,?,?,?,?,?,?)
                    """, (ot_id, sku, title, title_tec_any, int(total), 0, "PENDING", None, None, None))

        from manifiestos_etiquetas import save_with_picking
        save_with_picking(conn, batch_key, batch_label, dispatch_payload)
        conn.commit()
        conn.close()
        _picking_meta_set("intentional_reset", "")
        queue_picking_batch_snapshot(batch_key, motivo="LOTE_CREADO", include_lote_created=True)
        return {"created": True, "reason": "ok", "new_orders": len(new_order_ids), "picker_names": picker_names, "batch_key": batch_key, "batch_label": batch_label}

    # Modelo SKU/Familia con el mismo planificador usado por la vista previa.
    plan = _plan_sku_family_distribution(
        sales_df,
        len(ot_ids),
        familia_map_sku=familia_map_sku,
        cortes_set=cortes_set,
        picker_names=picker_names,
    )
    title_ml_by_sku = plan.get("title_ml_by_sku") or {}
    assigned_rows = plan.get("assignments") or []

    for ot_pos, ot_id in enumerate(ot_ids):
        rows = assigned_rows[ot_pos] if ot_pos < len(assigned_rows) else []
        rows = sorted(
            rows,
            key=lambda r: (
                str(r.get("family") or ""),
                int(str(r.get("sku_ml"))) if str(r.get("sku_ml", "")).isdigit() else 10**18,
                str(r.get("sku_ml") or ""),
            ),
        )
        for r in rows:
            fam = str(r.get("family") or "Sin Familia")
            sku = normalize_sku(r.get("sku_ml"))
            total = int(r.get("qty") or 0)
            if not sku or total <= 0:
                continue
            title_tec = inv_map_sku.get(sku, "") or ""
            title_ml = title_ml_by_sku.get(sku, "") or ""
            title_eff = title_tec.strip() if title_tec.strip() else title_ml.strip()
            if sku in cortes_set:
                c.execute(
                    "INSERT INTO cortes_tasks (ot_id, sku_ml, title_ml, title_tec, qty_total, created_at) VALUES (?,?,?,?,?,?)",
                    (ot_id, sku, title_eff, title_tec, total, now_iso())
                )
            else:
                c.execute("""
                    INSERT INTO picking_tasks (ot_id, sku_ml, title_ml, title_tec, qty_total, qty_picked, status, decided_at, confirm_mode, family)
                    VALUES (?,?,?,?,?,?,?,?,?,?)
                """, (ot_id, sku, title_eff, title_tec, total, 0, "PENDING", None, None, fam))

    from manifiestos_etiquetas import save_with_picking
    save_with_picking(conn, batch_key, batch_label, dispatch_payload)
    conn.commit()
    conn.close()
    _picking_meta_set("intentional_reset", "")
    queue_picking_batch_snapshot(batch_key, motivo="LOTE_CREADO", include_lote_created=True)
    return {"created": True, "reason": "ok", "new_orders": len(new_order_ids), "picker_names": picker_names, "batch_key": batch_key, "batch_label": batch_label}



# =========================
# UI: IMPORTAR TANDA + ETIQUETAS
# =========================
def page_import(inv_map_sku: dict, familia_map_sku: dict):
    """Carga simultánea de ventas + etiquetas en una sola tanda de picking."""
    from manifiestos_etiquetas import prepare_upload

    st.header("2) Importar ventas y etiquetas")
    flash = st.session_state.pop("picking_import_flash", None)
    if flash:
        st.success(flash) if isinstance(flash, str) else st.info(str(flash))

    batches = _get_picking_batches_summary()
    if batches:
        st.subheader("Tandas operativas de picking")
        table = [{"Tanda": b["batch_label"], "Ventas": b["orders_count"],
                  "Pickeadores": b["pickers"], "Avance": f"{b['progress_pct']:.1f}%"}
                 for b in batches]
        st.dataframe(table, hide_index=True, use_container_width=True)

    origen = st.radio("Origen de ventas", ["Excel Mercado Libre", "Manifiesto PDF (etiquetas)"], horizontal=True)
    num_pickers = st.number_input("Cantidad de pickeadores para esta tanda", min_value=1, max_value=20,
                                  value=3 if batches else 5, step=1)
    model_pick = st.radio("Modelo de distribución", ["Por ventas", "Por sku"], horizontal=True)
    canal = st.radio("Tipo de despacho de esta tanda", ["Flex", "Colecta"], horizontal=True)
    next_names = [f"P{n}" for n in _next_picker_numbers(_get_current_picker_names(), int(num_pickers))]
    st.info(f"Se crearán: **{', '.join(next_names)}**")

    if origen == "Excel Mercado Libre":
        sales_file = st.file_uploader("Archivo de ventas ML (XLSX)", type=["xlsx"], key="ml_excel")
    else:
        sales_file = st.file_uploader("Manifiesto de Mercado Libre (PDF)", type=["pdf"], key="ml_pdf")

    # Los documentos se exigen aquí, ANTES de guardar las OTs.
    label_files = st.file_uploader(
        "Etiquetas de envío de ESTA misma tanda (TXT/ZPL o PDF)",
        type=["txt", "zpl", "pdf"], accept_multiple_files=True,
        key="picking_etiquetas_tanda",
        help="Puedes seleccionar varios archivos del mismo formato. No mezcles PDF y ZPL/TXT.",
    )
    if not sales_file:
        st.info("Primero carga las ventas y las etiquetas que corresponden exactamente a esa tanda.")
        return
    try:
        sales_df = (import_sales_excel(sales_file) if origen.startswith("Excel")
                    else parse_manifest_pdf(sales_file))
    except Exception as exc:
        st.error(f"No fue posible leer el archivo de ventas: {exc}")
        return
    if sales_df is None or sales_df.empty:
        st.error("No se encontraron ventas con SKU y cantidad. Revisa el archivo antes de continuar.")
        return
    st.subheader("Vista previa de ventas")
    st.dataframe(sales_df.head(30), use_container_width=True)
    if not label_files:
        st.warning("Debes cargar las etiquetas correspondientes a estas ventas para crear la tanda.")
        return

    # No permitir cargas parciales inadvertidas: importar duplicados de OT haría que
    # el archivo de etiquetas ya no correspondiera a las ventas realmente agregadas.
    if batches:
        filtered = _filter_sales_not_loaded_for_preview(sales_df)
        if len(filtered["ml_order_id"].unique()) != len(sales_df["ml_order_id"].unique()):
            st.error("Hay ventas ya asignadas a una tanda operativa. Quita esas ventas y sus etiquetas del archivo "
                     "antes de agregar una tanda nueva; así evitamos imprimir despachos duplicados.")
            return

    try:
        uploaded = [(f.name, f.getvalue()) for f in label_files]
        payload = prepare_upload(sales_df, inv_map_sku, uploaded, sales_file.name,
                                 "EXCEL" if origen.startswith("Excel") else "PDF", canal.upper())
    except Exception as exc:
        st.error(f"Las etiquetas no coinciden con la carga: {exc}")
        return

    c1, c2, c3 = st.columns(3)
    c1.metric("Ventas", len(payload["sales"]))
    c2.metric("Etiquetas", payload["label_count"])
    c3.metric("Tipo", f"{canal} · {payload['label_type']}")
    if payload["pairing"] == "VENTA_ZPL":
        st.success("Cada etiqueta ZPL quedó vinculada con una venta del manifiesto.")
    for warning in payload["warnings"]:
        st.warning(warning)

    if model_pick.startswith("Por sku"):
        plan = _plan_sku_family_distribution(
            sales_df, int(num_pickers), familia_map_sku=familia_map_sku,
            cortes_set=load_cortes_set(), picker_names=next_names,
        )
        _render_sku_distribution_preview(plan)

    if st.button("Crear tanda de picking y guardar sus documentos", type="primary"):
        model = "VENTAS" if model_pick.startswith("Por ventas") else "SKU"
        try:
            if batches:
                result = append_orders_and_build_ots(
                    sales_df, inv_map_sku, int(num_pickers), model=model,
                    familia_map_sku=familia_map_sku, source_label=sales_file.name,
                    dispatch_payload=payload,
                )
            else:
                result = save_orders_and_build_ots(
                    sales_df, inv_map_sku, int(num_pickers), model=model,
                    familia_map_sku=familia_map_sku, source_label=sales_file.name,
                    dispatch_payload=payload,
                )
            if not result.get("created"):
                st.error("No se creó la tanda: " + str(result.get("reason", "desconocido")))
                return
            st.session_state["picking_import_flash"] = (
                f"Tanda creada: {result['batch_label']}. Documentos guardados para descargar en "
                "5) Manifiestos y etiquetas."
            )
            st.rerun()
        except Exception as exc:
            st.error(f"No se pudo guardar la tanda y sus documentos: {exc}")


# =========================
# UI: CORTES (PDF de la tanda)
# =========================
def page_cortes_pdf_batch():
    st.header("Cortes de la tanda (PDF)")
    st.caption("Lista de productos que requieren corte manual (rollos). No aparecen en el picking PDA.")

    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        SELECT po.ot_code,
               ct.sku_ml,
               COALESCE(NULLIF(ct.title_tec,''), ct.title_ml) AS title,
               ct.qty_total
        FROM cortes_tasks ct
        JOIN picking_ots po ON po.id = ct.ot_id
        ORDER BY po.ot_code, CAST(ct.sku_ml AS INTEGER), ct.sku_ml
    """)
    rows = c.fetchall()
    conn.close()

    if not rows:
        st.info("No hay SKUs de corte en la tanda actual.")
        return

    df_raw = pd.DataFrame(rows, columns=["OT", "SKU", "Producto", "Cantidad"])

    # Consolidar por SKU (mismo producto) sumando cantidades
    df = (
        df_raw.groupby(["SKU", "Producto"], as_index=False)
        .agg(Cantidad=("Cantidad", "sum"), OTs=("OT", lambda s: sorted(set(map(str, s)))))
    )
    df["OTs"] = df["OTs"].apply(lambda xs: ", ".join(xs))
    # Orden por SKU numérico si aplica
    try:
        df["_sku_num"] = pd.to_numeric(df["SKU"], errors="coerce")
        df = df.sort_values(["_sku_num", "SKU"]).drop(columns=["_sku_num"])
    except Exception:
        df = df.sort_values(["SKU"])

    st.dataframe(df[["SKU", "Producto", "Cantidad"]], use_container_width=True, hide_index=True)

    from io import BytesIO
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    import textwrap

    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    w, h = A4
    y = h - 40

    pdf.setFont("Helvetica-Bold", 14)
    pdf.drawString(40, y, "Ferretería Aurora - Cortes")
    y -= 18
    pdf.setFont("Helvetica", 10)
    pdf.drawString(40, y, f"Generado: {to_chile_display(now_iso())}")
    y -= 22

    pdf.setFont("Helvetica-Bold", 10)
    pdf.drawString(40, y, "SKU")
    pdf.drawString(140, y, "Producto")
    pdf.drawString(520, y, "Cant.")
    y -= 14

    pdf.setFont("Helvetica", 10)
    for _, r in df.iterrows():
        if y < 60:
            pdf.showPage()
            y = h - 40
            pdf.setFont("Helvetica-Bold", 14)
            pdf.drawString(40, y, "Ferretería Aurora - Cortes")
            y -= 18
            pdf.setFont("Helvetica", 10)
            pdf.drawString(40, y, f"Generado: {to_chile_display(now_iso())}")
            y -= 22
            pdf.setFont("Helvetica-Bold", 10)
            pdf.drawString(40, y, "SKU")
            pdf.drawString(140, y, "Producto")
            pdf.drawString(460, y, "OTs")
            pdf.drawString(540, y, "Cant.")
            y -= 14
            pdf.setFont("Helvetica", 10)

        sku = str(r["SKU"])
        title_full = str(r["Producto"])
        qty = str(int(r["Cantidad"]))

        # Envolver título en 2 líneas máximo para que no se corte ni se mezcle con la cantidad
        wrap_width = 62  # aprox. caracteres para la columna de Producto en A4
        lines = textwrap.wrap(title_full, width=wrap_width)[:2]
        if not lines:
            lines = [""]

        # Línea 1: SKU + Producto + Cantidad
        pdf.drawString(40, y, sku)
        pdf.drawString(140, y, lines[0])
        pdf.drawRightString(565, y, qty)
        y -= 12

        # Línea 2 (si aplica): continuación del producto (sin tocar cantidad)
        if len(lines) > 1:
            pdf.drawString(140, y, lines[1])
            y -= 12

    pdf.save()
    pdf_bytes = buffer.getvalue()
    buffer.close()

    st.download_button(
        "⬇️ Descargar PDF de Cortes (tanda)",
        data=pdf_bytes,
        file_name=f"cortes_tanda_{now_iso().replace(':','-')}.pdf",
        mime="application/pdf",
        use_container_width=True,
    )


# =========================
# UI: PICKING (FLEX)
# =========================
def picking_lobby():
    st.markdown("### Picking")
    st.caption("Selecciona tu pickeador")

    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT name FROM pickers ORDER BY name")
    rows = c.fetchall()
    conn.close()

    if not rows:
        st.info("Aún no hay pickeadores. Primero importa ventas y genera OTs.")
        return False

    pickers = [r[0] for r in rows]

    st.markdown(
        """
        <style>
        .bigbtn button {
            width: 100% !important;
            padding: 18px 10px !important;
            font-size: 22px !important;
            font-weight: 900 !important;
            border-radius: 16px !important;
        }
        </style>
        """,
        unsafe_allow_html=True
    )

    cols = st.columns(3)
    chosen = None
    for i, p in enumerate(pickers):
        with cols[i % 3]:
            st.markdown('<div class="bigbtn">', unsafe_allow_html=True)
            if st.button(p, key=f"pick_{p}"):
                chosen = p
            st.markdown('</div>', unsafe_allow_html=True)

    if chosen:
        st.session_state.selected_picker = chosen
        st.rerun()

    return "selected_picker" in st.session_state


def page_picking():
    if "selected_picker" not in st.session_state:
        ok = picking_lobby()
        if not ok:
            return

    picker_name = st.session_state.get("selected_picker", "")
    if not picker_name:
        st.session_state.pop("selected_picker", None)
        st.rerun()

    topA, topB = st.columns([2, 1])
    with topA:
        st.markdown(f"### Picking (PDA) — {picker_name}")
    with topB:
        if st.button("Cambiar pickeador"):
            st.session_state.pop("selected_picker", None)
            st.rerun()

    st.markdown(
        """
        <style>
        div.block-container { padding-top: 0.6rem; padding-bottom: 1rem; }
        .hero { padding: 10px 12px; border-radius: 12px; background: rgba(0,0,0,0.04); margin: 6px 0 8px 0; }
        .hero .sku { font-size: 26px; font-weight: 900; margin: 0; }
        .hero .prod { font-size: 22px; font-weight: 800; margin: 6px 0 0 0; line-height: 1.15; }
        .hero .qty { font-size: 26px; font-weight: 900; margin: 8px 0 0 0; }
.hero .loc { font-size: 18px; font-weight: 900; margin: 6px 0 0 0; opacity: 0.9; }
        .smallcap { font-size: 12px; opacity: 0.75; margin: 0 0 4px 0; }
        .scanok { display:inline-block; padding: 6px 10px; border-radius: 10px; font-weight: 900; }
        .ok { background: rgba(0, 200, 0, 0.15); }
        .bad { background: rgba(255, 0, 0, 0.12); }
        </style>
        """,
        unsafe_allow_html=True
    )

    conn = get_conn()
    c = conn.cursor()

    c.execute("SELECT barcode, sku_ml FROM sku_barcodes")
    barcode_to_sku = {r[0]: r[1] for r in c.fetchall()}

    c.execute("""
        SELECT po.id, po.ot_code, po.status
        FROM picking_ots po
        JOIN pickers pk ON pk.id = po.picker_id
        WHERE pk.name = ?
        ORDER BY po.ot_code
    """, (picker_name,))
    ots = c.fetchall()
    if not ots:
        st.error(f"No existe OT para {picker_name}. Importa ventas y genera OTs.")
        conn.close()
        return

    ot_row = None
    for r in ots:
        if r[2] != "PICKED":
            ot_row = r
            break
    if ot_row is None:
        ot_row = ots[0]

    ot_id, ot_code, ot_status = ot_row

    if ot_status == "PICKED":
        st.success("OT cerrada (PICKED).")
        conn.close()
        return

    c.execute("""
        SELECT id, sku_ml, title_ml, title_tec,
               qty_total, qty_picked, status
        FROM picking_tasks
        WHERE ot_id=?
        ORDER BY COALESCE(defer_rank,0) ASC, CAST(sku_ml AS INTEGER), sku_ml
    """, (ot_id,))
    tasks = c.fetchall()

    total_tasks = len(tasks)
    done_small = sum(1 for t in tasks if t[6] in ("DONE", "INCIDENCE"))
    st.caption(f"Resueltos: {done_small}/{total_tasks}")

    current = next((t for t in tasks if t[6] == "PENDING"), None)
    if current is None:
        st.success("No quedan SKUs pendientes.")
        if st.button("Cerrar OT"):
            c.execute("UPDATE picking_ots SET status='PICKED', closed_at=? WHERE id=?", (now_iso(), ot_id))
            conn.commit()
            ot_payload = _picking_ot_payload(ot_id)
            queue_picking_ot_state(ot_id)
            queue_picking_batch_closed_if_complete(_picking_clean(ot_payload.get("batch_key")))
            st.success("OT cerrada.")
        conn.close()
        return

    task_id, sku_expected, title_ml, title_tec, qty_total, qty_picked, status = current

    # Título: prioridad absoluta al texto crudo del maestro (tal cual). Si no existe, cae a title_tec/title_ml.
    raw_master = master_raw_title_lookup(MASTER_FILE, sku_expected)
    producto_show = raw_master if raw_master else (title_tec if title_tec not in (None, "") else (title_ml or ""))
    if "pick_state" not in st.session_state:
        st.session_state.pick_state = {}
    state = st.session_state.pick_state
    if str(task_id) not in state:
        state[str(task_id)] = {
            "confirmed": False,
            "confirm_mode": None,
            "scan_value": "",
            "qty_input": "",
            "needs_decision": False,
            "missing": 0,
            "show_manual_confirm": False,
            "scan_status": "idle",
            "scan_msg": "",
            "last_sku_expected": None
        }
    s = state[str(task_id)]

    if s.get("last_sku_expected") != sku_expected:
        s["last_sku_expected"] = sku_expected
        s["confirmed"] = False
        s["confirm_mode"] = None
        s["needs_decision"] = False
        s["missing"] = 0
        s["show_manual_confirm"] = False
        s["scan_status"] = "idle"
        s["scan_msg"] = ""
        s["qty_input"] = ""
        s["scan_value"] = ""

    # Tarjeta principal: mostrar el título tal cual (incluye UBC/ubicación aunque venga al inicio/medio/final)
    st.caption(f"OT: {ot_code}")
    st.markdown(f"### SKU: {sku_expected}")

    st.markdown(
        f'<div class="hero"><div class="prod" style="white-space: normal; overflow-wrap: anywhere; word-break: break-word;">{html.escape(str(producto_show))}</div></div>',
        unsafe_allow_html=True,
    )

    # Fotos del producto (si existe match por SKU en publicaciones)
    try:
        pics, pub_link = get_picture_urls_for_sku(sku_expected)
    except Exception:
        pics, pub_link = [], ""
    if pics:
        st.image(pics[0], width=55)
        if len(pics) > 1:
            with st.expander(f"Ver más fotos ({len(pics)})", expanded=False):
                st.image(pics, use_container_width=True)

    st.markdown(f"### Solicitado: {qty_total}")

    try:
        stock_kame = obtener_stock_kame(sku_expected)
        fecha_stock_kame = obtener_fecha_stock_kame()
    except Exception:
        stock_kame = None
        fecha_stock_kame = ""

    fecha_stock_label = to_chile_display(fecha_stock_kame) if fecha_stock_kame else "N/D"
    if stock_kame is None:
        st.info(f"Stock Kame: N/D · Actualizado: {fecha_stock_label}")
    elif float(stock_kame) <= 0:
        st.error(f"Stock Kame: {format_stock_kame(stock_kame)} · Actualizado: {fecha_stock_label}")
    elif float(stock_kame) < float(qty_total):
        st.warning(f"Stock Kame: {format_stock_kame(stock_kame)} · Actualizado: {fecha_stock_label}")
    else:
        st.success(f"Stock Kame: {format_stock_kame(stock_kame)} · Actualizado: {fecha_stock_label}")

    if s["scan_status"] == "ok":
        st.markdown(
            f'<span class="scanok ok">✅ OK</span> {s["scan_msg"]}',
            unsafe_allow_html=True,
        )
    elif s["scan_status"] == "bad":
        st.markdown(
            f'<span class="scanok bad">❌ ERROR</span> {s["scan_msg"]}',
            unsafe_allow_html=True,
        )
        st.markdown(f'<span class="scanok bad">❌ ERROR</span> {s["scan_msg"]}', unsafe_allow_html=True)

    col1, col2, col3, col4 = st.columns([2, 1, 1, 1])

    with col1:
        scan_label = "Escaneo"
        scan = st.text_input(scan_label, value=s["scan_value"], key=f"scan_{task_id}")

        # Autofocus en PDA: después de elegir desde la lista, dejar listo el campo de escaneo
        if st.session_state.get("focus_scan", False):
            components.html(
                "<script>"
                "setTimeout(function(){"
                "const el=document.querySelector('input[type=\"text\"]');"
                "if(el){el.focus(); if(el.select){el.select();}}"
                "}, 50);"
                "</script>",
                height=0,
            )
            st.session_state["focus_scan"] = False
        force_tel_keyboard(scan_label)
        # Autofocus inteligente:
        # - Si ya validó el producto (confirmed), llevar el foco a "Cantidad"
        # - Si no, mantener foco en "Escaneo"
        if s.get("confirmed", False):
            autofocus_input("Cantidad")
        else:
            autofocus_input(scan_label)

    with col2:
        if st.button("Validar"):
            sku_detected = resolve_scan_to_sku(scan, barcode_to_sku)
            if not sku_detected:
                s["scan_status"] = "bad"
                s["scan_msg"] = "No se pudo leer el código."
                s["confirmed"] = False
                s["confirm_mode"] = None
            elif sku_detected != sku_expected:
                s["scan_status"] = "bad"
                s["scan_msg"] = f"Leído: {sku_detected}"
                s["confirmed"] = False
                s["confirm_mode"] = None
            else:
                s["scan_status"] = "ok"
                s["scan_msg"] = "Producto correcto."
                s["confirmed"] = True
                s["confirm_mode"] = "SCAN"
                s["scan_value"] = scan
            if s.get("scan_status") == "ok":
                sfx_emit("OK")
            elif s.get("scan_status") == "bad":
                sfx_emit("ERR")
            st.rerun()

    with col3:
        if st.button("Sin EAN"):
            s["show_manual_confirm"] = True
            st.rerun()

    with col4:
        if st.button("Siguiente"):
            # Siempre manda este SKU al final de la fila (rotación circular).
            # Implementación: defer_rank = (máximo defer_rank en esta OT) + 1
            try:
                c.execute("SELECT COALESCE(MAX(defer_rank), 0) FROM picking_tasks WHERE ot_id=?", (ot_id,))
                max_rank = c.fetchone()[0] or 0
                new_rank = int(max_rank) + 1
                c.execute(
                    "UPDATE picking_tasks SET defer_rank=?, defer_at=? WHERE id=?",
                    (new_rank, now_iso(), task_id)
                )
                conn.commit()
                queue_picking_task_state(task_id)
            except Exception:
                pass
            # Limpiar estado UI de este task y seguir con el siguiente
            state.pop(str(task_id), None)
            st.rerun()

    if s.get("show_manual_confirm", False) and not s["confirmed"]:
        st.info("Confirmación manual")
        st.write(f"✅ {producto_show}")
        if st.button("Confirmar", key=f"confirm_manual_{task_id}"):
            s["confirmed"] = True
            s["confirm_mode"] = "MANUAL_NO_EAN"
            s["show_manual_confirm"] = False
            s["scan_status"] = "ok"
            s["scan_msg"] = "Confirmado manual."
            st.rerun()

    qty_label = "Cantidad"
    qty_in = st.text_input(
        qty_label,
        value=s["qty_input"],
        disabled=not s["confirmed"],
        key=f"qty_{task_id}"
    )
    force_tel_keyboard(qty_label)

    if st.button("Confirmar cantidad", disabled=not s["confirmed"]):
        try:
            q = int(str(qty_in).strip())
        except Exception:
            st.error("Ingresa un número válido.")
            sfx_emit("ERR")
            q = None

        if q is not None:
            s["qty_input"] = str(q)

            if q > int(qty_total):
                st.error(f"La cantidad ({q}) supera solicitado ({qty_total}).")
                s["needs_decision"] = False

            elif q == int(qty_total):
                # Si el picker usó "Sin EAN", lo registramos en incidencias para trazabilidad
                manual_incidence_id = None
                if str(s.get("confirm_mode") or "") == "MANUAL_NO_EAN":
                    try:
                        c.execute("""INSERT INTO picking_incidences
                                     (ot_id, sku_ml, qty_total, qty_picked, qty_missing, reason, note, created_at)
                                     VALUES (?,?,?,?,?,?,?,?)""",
                                  (ot_id, sku_expected, int(qty_total), int(q), 0, "SIN_EAN", "", now_iso()))
                        manual_incidence_id = int(c.lastrowid)
                    except Exception:
                        pass

                c.execute("""
                    UPDATE picking_tasks
                    SET qty_picked=?, status='DONE', decided_at=?, confirm_mode=?
                    WHERE id=?
                """, (q, now_iso(), s["confirm_mode"], task_id))
                conn.commit()
                # Respaldo no bloqueante: SQLite ya confirmó la operación.
                backup_events = []
                task_payload = _picking_task_payload(task_id)
                if task_payload:
                    backup_events.append(("picking_task_state", task_payload))
                if manual_incidence_id:
                    inc_payload = _picking_incidence_payload(manual_incidence_id)
                    if inc_payload:
                        backup_events.append(("picking_incidence_state", inc_payload))
                enqueue_picking_backup_events_batch(backup_events)
                state.pop(str(task_id), None)
                st.success("OK. Siguiente…")
                sfx_emit("OK")
                st.rerun()
            else:
                missing = int(qty_total) - q
                s["needs_decision"] = True
                s["missing"] = missing
                st.warning(f"Faltan {missing}. Debes decidir (incidencias o reintentar).")

    if s["needs_decision"]:
        st.error(f"DECISIÓN: faltan {s['missing']} unidades.")
        colA, colB = st.columns(2)

        with colA:
            # Incidencia con nota (igual que una incidencia documentada): pedir motivo antes de guardar
            if "pick_inc_pending" not in st.session_state:
                st.session_state["pick_inc_pending"] = None

            pending = st.session_state.get("pick_inc_pending")
            is_pending = bool(pending and pending.get("task_id") == task_id)

            if (not is_pending) and st.button("A incidencias y seguir"):
                st.session_state["pick_inc_pending"] = {"task_id": task_id}
                st.rerun()

            if is_pending:
                st.warning("Incidencia: escribe el motivo antes de guardar.")
                note_val = st.text_area("Motivo / Nota", key=f"pick_inc_note_{task_id}", height=90,
                                        placeholder="Ej: Falta producto, no se encontró en ubicación, etc.")
                c1, c2 = st.columns([1, 1])
                if c1.button("💾 Guardar incidencia", key=f"pick_inc_save_{task_id}"):
                    q = int(s["qty_input"])
                    missing = int(qty_total) - q

                    c.execute("""INSERT INTO picking_incidences
                                 (ot_id, sku_ml, qty_total, qty_picked, qty_missing, reason, note, created_at)
                                 VALUES (?,?,?,?,?,?,?,?)""",
                              (ot_id, sku_expected, int(qty_total), q, missing, "FALTANTE", note_val or "", now_iso()))
                    incidence_id = int(c.lastrowid)

                    c.execute("""UPDATE picking_tasks
                                 SET qty_picked=?, status='INCIDENCE', decided_at=?, confirm_mode=?
                                 WHERE id=?""",
                              (q, now_iso(), s["confirm_mode"], task_id))

                    conn.commit()
                    backup_events = []
                    task_payload = _picking_task_payload(task_id)
                    inc_payload = _picking_incidence_payload(incidence_id)
                    if task_payload:
                        backup_events.append(("picking_task_state", task_payload))
                    if inc_payload:
                        backup_events.append(("picking_incidence_state", inc_payload))
                    enqueue_picking_backup_events_batch(backup_events)
                    st.session_state["pick_inc_pending"] = None
                    state.pop(str(task_id), None)
                    st.success("Enviado a incidencias. Siguiente…")
                    st.rerun()

                if c2.button("Cancelar", key=f"pick_inc_cancel_{task_id}"):
                    st.session_state["pick_inc_pending"] = None
                    st.rerun()


        with colB:
            if st.button("Reintentar"):
                s["needs_decision"] = False
                st.info("Ajusta cantidad y confirma nuevamente.")
    # =========================
    
    # =========================
    # LISTA DE SKUS DE ESTA OT
    # =========================
    st.markdown("---")


    force_close_key = f"pick_force_close_list_{ot_id}"
    if force_close_key not in st.session_state:
        st.session_state[force_close_key] = False

    label_list = "📋 Lista de SKUs de esta OT" + ("\u200b" if st.session_state.get(force_close_key, False) else "")
    with st.expander(label_list, expanded=False):

        # Forzar cierre en la próxima recarga (especial PDA)
        st.session_state[force_close_key] = False

        st.caption("Toca un SKU pendiente para ponerlo como el próximo a escanear. Luego sigues normal.")

        # Pendientes primero
        ordered = sorted(
            tasks,
            key=lambda t: (0 if t[6] == "PENDING" else 1, str(t[1]))
        )

        for t in ordered:
            _tid, _sku, _title_ml, _title_tec, _qty_total, _qty_picked, _status = t

            raw_master_t = master_raw_title_lookup(MASTER_FILE, _sku)
            _title_show = raw_master_t if raw_master_t else (
                _title_tec if _title_tec not in (None, "") else (_title_ml or "")
            )

            disabled = (_status != "PENDING") or (_tid == task_id)
            label = f"{_title_show} [{_sku}]"

            if st.button(label, disabled=disabled, key=f"jump_{ot_id}_{_tid}"):

                try:
                    pending_order = [int(x[0]) for x in ordered if str(x[6]) == "PENDING"]
                    if _tid in pending_order:
                        idx_sel = pending_order.index(_tid)
                        rotated = pending_order[idx_sel:] + pending_order[:idx_sel]
                        base_rank = -len(rotated)
                        for i, tid_rot in enumerate(rotated):
                            c.execute(
                                "UPDATE picking_tasks SET defer_rank=?, defer_at=? WHERE id=?",
                                (base_rank + i, now_iso(), tid_rot)
                            )
                        conn.commit()
                        reorder_events = []
                        for tid_rot in rotated:
                            payload = _picking_task_payload(tid_rot)
                            if payload:
                                reorder_events.append(("picking_task_state", payload))
                        enqueue_picking_backup_events_batch(reorder_events)

                except Exception:
                    pass

                if "pick_state" in st.session_state:
                    st.session_state.pick_state.pop(str(task_id), None)
                    st.session_state.pick_state.pop(str(_tid), None)

                st.session_state[force_close_key] = True
                st.session_state['focus_scan'] = True
                st.rerun()

    conn.close()



# =========================
# UI: ADMIN (FLEX)
# =========================
def page_admin():
    st.header("Administrador")


    # =========================
    # PERSISTENCIA (Streamlit Community Cloud)
    # =========================
    st.subheader("Persistencia / Respaldo — PICKING")
    _render_module_backup_ui("picking", "Picking", PICKING_TABLES)
    render_picking_recovery_admin_ui()

    st.divider()

    conn = get_conn()
    c = conn.cursor()

    st.subheader("Resumen")
    c.execute("SELECT COUNT(*) FROM orders")
    n_orders = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM order_items")
    n_items = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM picking_ots")
    n_ots = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM picking_incidences")
    n_inc = c.fetchone()[0]

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Ventas", n_orders)
    col2.metric("Líneas", n_items)
    col3.metric("OTs", n_ots)
    col4.metric("Incidencias", n_inc)

    st.subheader("Estado OTs")
    c.execute("""
        SELECT po.ot_code, pk.name, po.status, po.created_at, po.closed_at,
               SUM(CASE WHEN pt.status='PENDING' THEN 1 ELSE 0 END) as pendientes,
               SUM(CASE WHEN pt.status IN ('DONE','INCIDENCE') THEN 1 ELSE 0 END) as resueltas,
               SUM(CASE WHEN pt.confirm_mode='MANUAL_NO_EAN' THEN 1 ELSE 0 END) as manual_no_ean
        FROM picking_ots po
        JOIN pickers pk ON pk.id = po.picker_id
        LEFT JOIN picking_tasks pt ON pt.ot_id = po.id
        GROUP BY po.ot_code, pk.name, po.status, po.created_at, po.closed_at
        ORDER BY po.ot_code
    """)
    df = pd.DataFrame(c.fetchall(), columns=[
        "OT", "Picker", "Estado", "Creada", "Cerrada",
        "Pendientes", "Resueltas", "Sin EAN"
    ])
    df["Creada"] = df["Creada"].apply(to_chile_display)
    df["Cerrada"] = df["Cerrada"].apply(to_chile_display)
    st.dataframe(df, use_container_width=True, hide_index=True)

    st.subheader("Fotos de productos (Publicaciones)")
    st.caption(f"Se carga automáticamente desde **{PUBLICATIONS_FILE}** (incluido en el repo).")
    
    if not os.path.exists(PUBLICATIONS_FILE):
        st.warning(f"No se encontró el archivo: {PUBLICATIONS_FILE}. (No se mostrarán fotos por SKU)")
    else:
        # Estado simple desde DB
        conn2 = get_conn()
        c2 = conn2.cursor()
        try:
            c2.execute("SELECT COUNT(1), MAX(updated_at) FROM sku_publications;")
            n_pubs, last_upd = c2.fetchone()
        except Exception:
            n_pubs, last_upd = 0, None
        conn2.close()
    
        st.info(f"Links cargados: **{int(n_pubs or 0)}** SKUs. Última actualización: **{to_chile_display(last_upd) if last_upd else '-'}**")

    st.divider()

    st.divider()
    st.subheader("Liberar y repartir tareas pendientes (por SKU)")

    # Nota: aquí "OT" se refiere a la tarea/línea (SKU + cantidad). Solo mueve tareas PENDING (sin avance).
    try:
        c.execute("SELECT id, name FROM pickers ORDER BY id")
        pickers_rows = c.fetchall()
    except Exception:
        pickers_rows = []
    if not pickers_rows:
        st.info("No hay pickeadores creados todavía.")
    else:
        picker_id_to_name = {int(pid): pname for pid, pname in pickers_rows}
        picker_names = [pname for _, pname in pickers_rows]
        picker_name_to_id = {pname: int(pid) for pid, pname in pickers_rows}

        colA, colB = st.columns([1, 2])
        with colA:
            src_name = st.selectbox("Picker origen", picker_names, key="adm_reassign_src_picker")
        src_id = picker_name_to_id.get(src_name)

        # Traer tareas PENDING del picker origen (sin avance)
        c.execute("""
            SELECT pt.id,
                   po.ot_code,
                   pt.sku_ml,
                   COALESCE(NULLIF(pt.title_tec,''), NULLIF(pt.title_ml,''), pt.sku_ml) AS producto,
                   pt.qty_total
            FROM picking_tasks pt
            JOIN picking_ots po ON po.id = pt.ot_id
            WHERE po.picker_id = ?
              AND pt.status = 'PENDING'
              AND COALESCE(pt.qty_picked,0) = 0
            ORDER BY pt.id
        """, (src_id,))
        task_rows = c.fetchall()

        if not task_rows:
            st.info(f"No hay tareas pendientes para {src_name}.")
        else:
            df_tasks = pd.DataFrame(task_rows, columns=["task_id","OT_origen","SKU","Producto","Cantidad"])
            # Editor con selección
            df_edit = df_tasks.copy()
            df_edit.insert(0, "Mover", False)

            # Botón: seleccionar todo (solo afecta la columna "Mover")
            col_sel1, _ = st.columns([1, 5])
            with col_sel1:
                if st.button("Seleccionar todo", key="adm_reassign_select_all"):
                    st.session_state["adm_reassign_select_all_flag"] = True

            if st.session_state.get("adm_reassign_select_all_flag"):
                df_edit["Mover"] = True
                st.session_state["adm_reassign_select_all_flag"] = False

            edited = st.data_editor(
                df_edit,
                use_container_width=True,
                hide_index=True,
                key="adm_reassign_editor",
                column_order=["Mover", "OT_origen", "SKU", "Producto", "Cantidad"],
                column_config={
                    "Mover": st.column_config.CheckboxColumn("Mover", help="Marca para mover esta tarea a otro pickeador"),
                    "task_id": st.column_config.NumberColumn("ID", disabled=True),
                    "OT_origen": st.column_config.TextColumn("OT origen", disabled=True),
                    "SKU": st.column_config.TextColumn("SKU", disabled=True),
                    "Producto": st.column_config.TextColumn("Producto", disabled=True),
                    "Cantidad": st.column_config.NumberColumn("Cant.", disabled=True),
                },
                disabled=["task_id","OT_origen","SKU","Producto","Cantidad"]
            )

            selected_ids = [int(r["task_id"]) for _, r in edited.iterrows() if bool(r.get("Mover"))]
            n_sel = len(selected_ids)
            st.write(f"**Seleccionadas:** {n_sel}")

            if n_sel > 0:
                def _next_picker_numbers(existing_names: list[str], qty: int) -> list[int]:
                    nums = []
                    for pname in existing_names:
                        m = re.fullmatch(r"P(\d+)", str(pname or "").strip().upper())
                        if m:
                            nums.append(int(m.group(1)))
                    start_n = (max(nums) + 1) if nums else 2
                    return list(range(start_n, start_n + int(qty)))

                with st.expander("➕ Crear pickeadores destino", expanded=(len(picker_names) <= 1)):
                    qty_new_pickers = st.number_input(
                        "Cuántos crear",
                        min_value=1,
                        max_value=20,
                        value=1,
                        step=1,
                        key="adm_new_picker_qty"
                    )

                    preview_nums = _next_picker_numbers(picker_names, int(qty_new_pickers))
                    preview_names = [f"P{n}" for n in preview_nums]
                    st.caption(f"Se crearán automáticamente: {', '.join(preview_names)}")

                    if st.button("Crear pickeador(es)", key="adm_create_picker_btn"):
                        created_names = []
                        try:
                            for n in _next_picker_numbers(picker_names, int(qty_new_pickers)):
                                pname = f"P{n}"
                                cur = conn.execute("INSERT OR IGNORE INTO pickers (name) VALUES (?)", (pname,))
                                if getattr(cur, "rowcount", 0) > 0:
                                    created_names.append(pname)
                            conn.commit()

                            if created_names:
                                st.success(f"Creados: {', '.join(created_names)}. Ya puedes repartir tareas.")
                                st.rerun()
                            else:
                                st.warning("No se creó ningún pickeador nuevo.")
                        except Exception as e:
                            try:
                                conn.rollback()
                            except Exception:
                                pass
                            st.error(f"No se pudieron crear pickeadores: {e}")

                try:
                    c.execute("SELECT id, name FROM pickers ORDER BY id")
                    pickers_rows_live = c.fetchall()
                except Exception:
                    pickers_rows_live = pickers_rows

                picker_names_live = [pname for _, pname in pickers_rows_live]
                picker_name_to_id_live = {pname: int(pid) for pid, pname in pickers_rows_live}
                other_picker_names = [n for n in picker_names_live if n != src_name]

                if not other_picker_names:
                    st.warning("No hay pickeadores destino disponibles. Crea uno arriba para repartir las tareas.")
                else:
                    dests = st.multiselect("Pickeadores destino", other_picker_names, default=other_picker_names, key="adm_reassign_dests")
                    if not dests:
                        st.info("Elige al menos un pickeador destino.")
                    else:
                        st.caption("Define cuántas tareas mover a cada destino. La suma debe ser igual a las seleccionadas.")
                        dest_counts = {}
                        cols = st.columns(min(4, len(dests)))
                        for i, dname in enumerate(dests):
                            with cols[i % len(cols)]:
                                dest_counts[dname] = st.number_input(f"{dname}", min_value=0, max_value=n_sel, value=0, step=1, key=f"adm_reassign_cnt_{dname}")

                        total_move = int(sum(dest_counts.values()))
                        st.write(f"**Total a mover según reparto:** {total_move} / {n_sel}")

                        def _new_ot_code(prefix: str = "LIB"):
                            ts = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
                            rnd = ''.join(random.choice(string.ascii_uppercase + string.digits) for _ in range(4))
                            return f"{prefix}-{ts}-{rnd}"

                        if st.button("Repartir tareas seleccionadas", type="primary"):
                            if total_move != n_sel:
                                sfx_emit("ERR")
                                st.error("La suma por destino debe ser exactamente igual al número de tareas seleccionadas.")
                            else:
                                # Reparto determinístico: por id ascendente
                                selected_ids_sorted = sorted(selected_ids)
                                cursor = 0
                                moved_total = 0
                                try:
                                    # La OT LIB conserva el lote original. No se recalcula ni mezcla el reparto.
                                    src_meta = c.execute(
                                        """SELECT COALESCE(po.batch_key,''),COALESCE(po.batch_label,''),COALESCE(po.model,'SKU')
                                             FROM picking_tasks pt JOIN picking_ots po ON po.id=pt.ot_id
                                            WHERE pt.id=?""",
                                        (selected_ids_sorted[0],),
                                    ).fetchone()
                                    src_batch_key = str(src_meta[0] or "") if src_meta else ""
                                    src_batch_label = str(src_meta[1] or "") if src_meta else ""
                                    src_model = str(src_meta[2] or "SKU") if src_meta else "SKU"
                                    for dname, cnt in dest_counts.items():
                                        cnt = int(cnt)
                                        if cnt <= 0:
                                            continue
                                        chunk = selected_ids_sorted[cursor:cursor+cnt]
                                        cursor += cnt
                                        if not chunk:
                                            continue
                                        dest_id = picker_name_to_id.get(dname)
                                        # Crear OT nueva para el destino
                                        new_code = _new_ot_code("LIB")
                                        now_iso_ts = now_iso()
                                        c.execute(
                                            """INSERT INTO picking_ots
                                               (ot_code, picker_id, status, created_at, closed_at, model, batch_key, batch_label)
                                               VALUES (?,?,?,?,NULL,?,?,?)""",
                                            (new_code, dest_id, "OPEN", now_iso_ts, src_model, src_batch_key, src_batch_label)
                                        )
                                        new_ot_id = int(c.lastrowid)

                                        qmarks = ",".join(["?"] * len(chunk))
                                        c.execute(f"UPDATE picking_tasks SET ot_id=? WHERE id IN ({qmarks})", [new_ot_id] + chunk)
                                        moved_total += len(chunk)

                                    conn.commit()
                                    if src_batch_key:
                                        queue_picking_batch_snapshot(src_batch_key, motivo="REASIGNACION_ADMIN", include_lote_created=False)
                                    sfx_emit("OK")
                                    st.success(f"Listo: movidas {moved_total} tareas desde {src_name}. Se crearon OTs 'LIB-*' para los destinos.")
                                    st.rerun()
                                except Exception as e:
                                    conn.rollback()
                                    sfx_emit("ERR")
                                    st.error(f"No se pudo repartir: {e}")
            else:
                st.caption("Marca al menos una tarea para habilitar el reparto.")


    st.subheader("Incidencias")
    c.execute("""
        SELECT po.ot_code, pk.name, pi.sku_ml, pi.qty_total, pi.qty_picked, pi.qty_missing, pi.reason, pi.note, pi.created_at
        FROM picking_incidences pi
        JOIN picking_ots po ON po.id = pi.ot_id
        JOIN pickers pk ON pk.id = po.picker_id
        ORDER BY pi.created_at DESC
    """)
    inc_rows = c.fetchall()
    if inc_rows:
        df_inc = pd.DataFrame(inc_rows, columns=["OT","Picker","SKU","Solicitado","Pickeado","Faltante","Motivo","Nota","Hora"])
        # Producto (título técnico): maestro si existe; si no, SKU
        try:
            df_inc["Producto"] = df_inc["SKU"].apply(lambda x: (master_raw_title_lookup(MASTER_FILE, str(x).strip()) or str(x).strip()))
        except Exception:
            df_inc["Producto"] = df_inc["SKU"].astype(str)

        df_inc["Hora"] = df_inc["Hora"].apply(to_chile_display)
        # Orden de columnas más útil
        try:
            df_inc = df_inc[["OT","Picker","SKU","Producto","Solicitado","Pickeado","Faltante","Motivo","Nota","Hora"]]
        except Exception:
            pass
        st.dataframe(df_inc, use_container_width=True, hide_index=True)
    else:
        st.info("Sin incidencias en la corrida actual.")

    st.divider()
    st.subheader("Acciones")

    if "confirm_reset" not in st.session_state:
        st.session_state.confirm_reset = False

    if not st.session_state.confirm_reset:
        if st.button("Reiniciar corrida de Picking"):
            st.session_state.confirm_reset = True
            st.warning("Se borrarán OTs, tareas, incidencias y ventas operativas. "
                       "Los manifiestos y etiquetas ya archivados seguirán disponibles en el módulo 5.")
            st.rerun()
    else:
        st.error("CONFIRMACIÓN: se reiniciará Picking, pero se conservarán los documentos históricos.")
        colA, colB = st.columns(2)
        with colA:
            if st.button("✅ Sí, borrar todo y reiniciar"):
                # Marcar lotes como eliminados en Sheets para que un reinicio intencional
                # no sea interpretado como pérdida de SQLite y no resurja automáticamente.
                try:
                    batch_rows = c.execute(
                        """SELECT DISTINCT COALESCE(batch_key,''),COALESCE(batch_label,''),MIN(created_at)
                             FROM picking_ots WHERE COALESCE(batch_key,'')<>''
                            GROUP BY batch_key,batch_label"""
                    ).fetchall()
                    delete_events = []
                    for bk, bl, bcreated in batch_rows:
                        delete_events.append(("lote_eliminado", {
                            "lote_id": str(bk), "lote_nombre": str(bl or bk), "backup_lote_key": str(bk),
                            "lote_created_at": bcreated or "", "hoja": "PICKING", "status": "ELIMINADO",
                            "usuario": "ADMIN", "created_at": now_iso(),
                        }))
                    enqueue_picking_backup_events_batch(delete_events)
                except Exception:
                    pass
                _picking_meta_set("intentional_reset", now_iso())
                c.execute("DELETE FROM picking_tasks;")
                c.execute("DELETE FROM picking_incidences;")
                c.execute("DELETE FROM ot_orders;")
                c.execute("DELETE FROM picking_ots;")
                c.execute("DELETE FROM pickers;")
                c.execute("DELETE FROM order_items;")
                c.execute("DELETE FROM orders;")
                conn.commit()
                st.session_state.confirm_reset = False
                st.success("Sistema reiniciado (todo borrado).")
                st.session_state.pop("selected_picker", None)
                st.rerun()
        with colB:
            if st.button("Cancelar"):
                st.session_state.confirm_reset = False
                st.info("Reinicio cancelado.")
                st.rerun()

    conn.close()

# =========================
# 5) MANIFIESTOS Y ETIQUETAS - DOCUMENTOS DE TANDAS PICKING
# =========================
def page_manifiestos_y_etiquetas(inv_map_sku: dict):
    from io import BytesIO
    from pypdf import PdfReader, PdfWriter
    from despacho_kame import create_kame_manifest_pdf, convert_zpl_labels_to_pdf, merge_label_pdfs
    from manifiestos_etiquetas import list_batches, load_selected

    st.header("5) Manifiestos y etiquetas")
    st.caption("Documentos de las tandas cargadas en Picking, incluso tras reiniciar la corrida.")
    conn = get_conn()
    try:
        tandas = list_batches(conn)
    finally:
        conn.close()
    if not tandas:
        st.info("Aún no hay tandas con documentos. Ve a 2) Importar ventas y adjunta sus etiquetas.")
        return

    options = {f"[{t['operation']}] {t['created']} · {t['label']} · {t['sales']} ventas / {t['labels']} etiquetas":t["batch_key"]
               for t in tandas}
    default_option = next((label for label in options if label.startswith("[FLEX]")), next(iter(options)))
    selection = st.multiselect("Tandas para imprimir o descargar", list(options),
                               default=[default_option], key="docs_picking_batches")
    if not selection:
        st.info("Selecciona al menos una tanda.")
        return
    keys = [options[o] for o in selection]
    flex_keys = [k for k in keys if next(t for t in tandas if t["batch_key"] == k)["operation"] == "FLEX"]
    flex_label_count = sum(t["labels"] for t in tandas if t["batch_key"] in flex_keys)
    conn = get_conn()
    try:
        data = load_selected(conn, keys, inv_map_sku)
    except Exception as exc:
        st.error(f"No fue posible recuperar los documentos: {exc}")
        return
    finally:
        conn.close()
    a,b,c = st.columns(3)
    a.metric("Ventas",len(data["sales"]))
    b.metric("Etiquetas Flex",flex_label_count)
    c.metric("Tandas",len(keys))

    if data["unknown"]:
        st.warning("SKU sin descripción Kame: " + ", ".join(data["unknown"][:15]))
    if "SIN_CRUCE" in data["pairings"]:
        st.warning("Algunas etiquetas no contienen un identificador de venta comprobable. "
                   "Se ha respetado su orden de origen. Verifica que coincidan con las ventas antes de despachar.")
    st.subheader("Manifiesto en idioma Kame")
    try:
        manifest_bytes = create_kame_manifest_pdf(data["sales"])
        st.download_button("Descargar manifiesto Kame (PDF)", data=manifest_bytes,
                           file_name="Manifiesto_Kame_" + "_".join(keys)[:100] + ".pdf",
                           mime="application/pdf", use_container_width=True)
    except Exception as exc:
        st.error(f"No se pudo crear el manifiesto: {exc}")

    st.subheader("Etiquetas Flex consolidadas")
    st.caption("Incluye únicamente las tandas marcadas Flex. Conserva su orden y las etiquetas originales.")
    if not flex_keys:
        st.info("Las tandas elegidas son Colecta. El PDF de etiquetas consolidadas está reservado a Flex.")
        return
    # Reunir segmentos en orden de selección para soportar tandas ZPL y PDF mixtas.
    conn = get_conn()
    try:
        segments = []
        for key in flex_keys:
            meta = conn.execute("SELECT label_type FROM despacho_tandas WHERE batch_key=?", (key,)).fetchone()
            if not meta:
                raise ValueError("Tanda no encontrada.")
            if meta[0] == "ZPL":
                blocks = [x[0] for x in conn.execute(
                    "SELECT zpl FROM despacho_etiquetas WHERE batch_key=? ORDER BY ordinal", (key,))]
                segments.append(("ZPL", blocks))
            else:
                pdfs = [x[0] for x in conn.execute(
                    "SELECT pdf_bytes FROM despacho_etiquetas_pdf WHERE batch_key=? ORDER BY ordinal", (key,))]
                segments.append(("PDF", pdfs))
    except Exception as exc:
        st.error(f"No se pudieron preparar las etiquetas: {exc}")
        return
    finally:
        conn.close()

    needs_online = any(kind == "ZPL" for kind, _ in segments)
    if needs_online:
        st.info("Para etiquetas ZPL se usa Labelary (requiere Internet). "
                "Sus archivos contienen nombres y domicilios que se transmitirán al servicio al convertirlos. "
                "Las etiquetas PDF originales se unen localmente.")
        consent = st.checkbox("Autorizo enviar las etiquetas ZPL seleccionadas a Labelary para su conversión a PDF",
                              key="docs_picking_consent")
    else:
        consent = True
        st.success("Todos los archivos ya están en PDF. Se unirán localmente, sin transmitir etiquetas a terceros.")

    # No presentar una descarga antigua después de cambiar el conjunto de tandas.
    signature = tuple(keys)
    cached = st.session_state.get("picking_docs_consolidated_pdf")
    if cached and cached["key"] != signature:
        cached = None
        st.session_state.pop("picking_docs_consolidated_pdf",None)
    if st.button("Preparar PDF de todas las etiquetas", type="primary",
                 disabled=not consent, use_container_width=True):
        try:
            writer = PdfWriter()
            progress = st.progress(0, text="Preparando etiquetas…")
            for index, (kind, files) in enumerate(segments):
                part = (convert_zpl_labels_to_pdf(files) if kind == "ZPL"
                        else merge_label_pdfs(files))
                reader = PdfReader(BytesIO(part), strict=False)
                for page in reader.pages:
                    writer.add_page(page)
                progress.progress((index+1)/len(segments),
                                  text=f"Tanda {index+1} de {len(segments)}")
            output = BytesIO()
            writer.write(output)
            result = output.getvalue()
            count = len(PdfReader(BytesIO(result), strict=False).pages)
            if count != flex_label_count:
                raise ValueError(f"Se esperaban {flex_label_count} páginas, pero se generaron {count}.")
            st.session_state["picking_docs_consolidated_pdf"] = {"key":signature,"pdf":result,"pages":count}
            st.success(f"PDF preparado: {count} páginas de etiquetas.")
        except Exception as exc:
            st.session_state.pop("picking_docs_consolidated_pdf",None)
            st.error(f"No se completó la generación. No se entregará un PDF parcial: {exc}")
    cached = st.session_state.get("picking_docs_consolidated_pdf")
    if cached and cached["key"] == signature:
        st.download_button("Descargar PDF único de etiquetas", data=cached["pdf"],
                           file_name="Etiquetas_"+"_".join(keys)[:100]+".pdf",
                           mime="application/pdf", use_container_width=True)


def main():
    st.set_page_config(page_title="Aurora ML – WMS (TEST)", layout="wide")
    sfx_sidebar()
    _sfx_unlock_render()
    _sfx_global_click_hook()
    sfx_render_pending()
    init_db()
    st.sidebar.warning("MODO PRUEBAS · SIN GOOGLE SHEETS · BD LOCAL INDEPENDIENTE")
    inv_map_sku, familia_map_sku, barcode_to_sku, conflicts = master_bootstrap(MASTER_FILE)
    _ = publications_bootstrap(PUBLICATIONS_FILE)
    st.sidebar.title("Ferretería Aurora – WMS")
    if os.path.exists(MASTER_FILE):
        st.sidebar.success(f"Maestro: {len(inv_map_sku)} SKUs / {len(barcode_to_sku)} EAN")
    else:
        st.sidebar.warning(f"No se encontró {MASTER_FILE}")
    pages = [
        "1) Picking",
        "2) Importar ventas",
        "3) Cortes de la tanda (PDF)",
        "4) Administrador",
        "5) Manifiestos y etiquetas",
    ]
    page = st.sidebar.radio("Menú", pages)
    if page.startswith("1"):
        page_picking()
    elif page.startswith("2"):
        page_import(inv_map_sku, familia_map_sku)
    elif page.startswith("3"):
        page_cortes_pdf_batch()
    elif page.startswith("4"):
        page_admin()
    else:
        page_manifiestos_y_etiquetas(inv_map_sku)


if __name__ == "__main__":
    main()
