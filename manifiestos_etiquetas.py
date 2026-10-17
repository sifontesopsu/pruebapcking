"""Documentos de despacho por tanda de PICKING, independientes de Google Sheets.

La carga se valida antes de crear las OTs, y se guarda en la misma transacción
SQLite del lote. El archivo de etiquetas conserva su orden y bytes originales.
"""
from __future__ import annotations

from collections import OrderedDict
from io import BytesIO
import json
import re
import sqlite3

from pypdf import PdfReader
from despacho_kame import _is_printable_zpl

MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # por archivo


def create_schema(conn: sqlite3.Connection):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS despacho_tandas (
        batch_key TEXT PRIMARY KEY,
        batch_label TEXT NOT NULL,
        created_at TEXT NOT NULL,
        source_name TEXT,
        source_type TEXT,
        label_type TEXT NOT NULL,
        operation_type TEXT NOT NULL DEFAULT 'FLEX',
        pairing TEXT NOT NULL,
        sales_count INTEGER NOT NULL,
        labels_count INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS despacho_ventas (
        batch_key TEXT NOT NULL,
        ordinal INTEGER NOT NULL,
        sale_id TEXT NOT NULL,
        buyer TEXT,
        page_no INTEGER,
        is_original_page INTEGER NOT NULL DEFAULT 0,
        items_json TEXT NOT NULL,
        PRIMARY KEY(batch_key, sale_id),
        FOREIGN KEY(batch_key) REFERENCES despacho_tandas(batch_key)
    );
    CREATE TABLE IF NOT EXISTS despacho_etiquetas (
        batch_key TEXT NOT NULL,
        ordinal INTEGER NOT NULL,
        sale_id TEXT,
        shipment_id TEXT,
        zpl TEXT NOT NULL,
        PRIMARY KEY(batch_key,ordinal),
        FOREIGN KEY(batch_key) REFERENCES despacho_tandas(batch_key)
    );
    CREATE TABLE IF NOT EXISTS despacho_etiquetas_pdf (
        batch_key TEXT NOT NULL,
        ordinal INTEGER NOT NULL,
        file_name TEXT NOT NULL,
        page_count INTEGER NOT NULL,
        pdf_bytes BLOB NOT NULL,
        PRIMARY KEY(batch_key,ordinal),
        FOREIGN KEY(batch_key) REFERENCES despacho_tandas(batch_key)
    );
    CREATE INDEX IF NOT EXISTS idx_despacho_created ON despacho_tandas(created_at);
    """)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(despacho_tandas)")}
    if "operation_type" not in cols:
        conn.execute("ALTER TABLE despacho_tandas ADD COLUMN operation_type TEXT NOT NULL DEFAULT 'FLEX'")


def _string(v):
    if v is None:
        return ""
    s = str(v).strip()
    return "" if s.lower() in {"nan", "none", "nat"} else s


def _sale_from_zpl(block: str) -> str:
    # Mercado Libre puede partir el ID entre dos campos ZPL consecutivos.
    for source in (block, "\n".join(re.findall(r"\^FD(.*?)\^FS", block, flags=re.I | re.S))):
        m = re.search(r"\bVenta\s*:\s*(\d{4,20})(?:\s*\^FS\s*\^FD\s*(\d{4,20}))?", source, re.I)
        if m:
            return m.group(1) + (m.group(2) or "")
    return ""


def _shipment_from_zpl(block: str) -> str:
    m = re.search(r'\\?"id\\?"\s*:\s*\\?"(\d{8,20})\\?"', block)
    if m:
        return m.group(1)
    m = re.search(r"(?:shipment|env[ií]o)\s*(?:id|n[uú]mero)?\s*[:#]\s*(\d{8,20})", block, re.I)
    return m.group(1) if m else ""


def _extract_labels(uploaded_files: list[tuple[str, bytes]]):
    if not uploaded_files:
        raise ValueError("Debes cargar también las etiquetas correspondientes a esta tanda.")
    kinds = {"PDF" if name.lower().endswith(".pdf") else "ZPL" if name.lower().endswith((".txt", ".zpl")) else "OTRO" for name, _ in uploaded_files}
    if len(kinds) != 1 or "OTRO" in kinds:
        raise ValueError("Carga solo ZPL/TXT o solo PDF. No mezcles formatos en una misma tanda.")
    kind = kinds.pop()
    records = []
    for filename, blob in uploaded_files:
        if not blob or len(blob) > MAX_UPLOAD_BYTES:
            raise ValueError(f"El archivo {filename} está vacío o supera 50 MB.")
        if kind == "ZPL":
            raw = blob.decode("utf-8-sig", errors="replace")
            blocks = re.findall(r"\^XA.*?\^XZ", raw, flags=re.I | re.S)
            printable = [b for b in blocks if _is_printable_zpl(b)]
            if not printable:
                raise ValueError(f"{filename} no contiene etiquetas ZPL imprimibles.")
            for block in printable:
                records.append({"sale_id": _sale_from_zpl(block), "shipment_id": _shipment_from_zpl(block), "zpl": block})
        else:
            if not blob.startswith(b"%PDF-"):
                raise ValueError(f"{filename} no es un PDF válido.")
            try:
                count = len(PdfReader(BytesIO(blob), strict=False).pages)
            except Exception as exc:
                raise ValueError(f"No se pudo leer {filename}: {exc}") from exc
            if count < 1:
                raise ValueError(f"{filename} no contiene páginas.")
            records.append({"name": filename, "bytes": blob, "pages": count})
    return kind, records


def prepare_upload(sales_df, master: dict, uploaded_files: list[tuple[str, bytes]], source_name: str, source_type: str, operation_type: str = "FLEX"):
    """Comprobación previa a la creación de OT. No escribe en BD."""
    if sales_df is None or sales_df.empty:
        raise ValueError("El manifiesto no contiene ventas válidas.")
    operation_type = operation_type.upper().strip()
    if operation_type not in {"FLEX", "COLECTA"}:
        raise ValueError("Tipo de despacho desconocido.")
    kind, labels = _extract_labels(uploaded_files)
    groups = OrderedDict()
    for _, r in sales_df.iterrows():
        sale_id = _string(r.get("ml_order_id"))
        sku = _string(r.get("sku_ml"))
        try:
            qty = int(r.get("qty"))
        except (ValueError, TypeError):
            qty = 0
        if not sale_id or not sku or qty <= 0:
            continue
        if sale_id not in groups:
            p = r.get("pagina_ml", 0)
            try:
                page = max(int(p), 0)
            except (ValueError, TypeError):
                page = 0
            groups[sale_id] = {
                "sale_id": sale_id, "buyer": _string(r.get("buyer")),
                "page_no": page, "is_original_page": bool(page), "items": [],
            }
        title = _string(master.get(sku, ""))
        groups[sale_id]["items"].append({"sku": sku, "cantidad": qty, "descripcion_kame": title})
    if not groups:
        raise ValueError("No hay ventas con SKU y cantidad positiva en el manifiesto.")
    sales = list(groups.values())
    num_labels = len(labels) if kind == "ZPL" else sum(x["pages"] for x in labels)
    sales_ids = set(groups)
    pairing = "SIN_CRUCE"
    warnings = []
    if kind == "ZPL":
        linked = [r["sale_id"] for r in labels if r["sale_id"] in sales_ids]
        if len(linked) == len(labels) and len(set(linked)) == len(labels) and len(linked) == len(sales):
            pairing = "VENTA_ZPL"
            # Solo reordena por número de venta cuando la asociación es completa.
            by_id = {r["sale_id"]: r for r in labels}
            labels = [by_id[s["sale_id"]] for s in sales]
        else:
            if num_labels != len(sales):
                raise ValueError(
                    f"No coinciden las ventas ({len(sales)}) con las etiquetas ({num_labels}). "
                    "Verifica que la tanda incluya todas las etiquetas y no haya paquetes compartidos."
                )
            warnings.append("No fue posible identificar todas las ventas dentro de las ZPL. Se conserva el orden original de las etiquetas; revisa su correspondencia antes de imprimir.")
    else:
        if num_labels != len(sales):
            raise ValueError(
                f"El PDF tiene {num_labels} páginas, pero el manifiesto registra {len(sales)} ventas. "
                "Se requiere una etiqueta por venta para esta modalidad."
            )
        warnings.append("PDF: se verificó la cantidad de páginas, pero no el número de venta de cada etiqueta. Confirma el orden de impresión antes del despacho.")
    unknown = sorted({i["sku"] for s in sales for i in s["items"] if not i["descripcion_kame"]})
    if unknown:
        warnings.append(f"{len(unknown)} SKU no tienen nombre Kame; aparecerán marcados en el manifiesto.")
    return {
        "sales": sales, "label_type": kind, "labels": labels, "pairing": pairing,
        "label_count": num_labels, "warnings": warnings,
        "source_name": _string(source_name), "source_type": _string(source_type),
        "operation_type": operation_type,
    }


def save_with_picking(conn: sqlite3.Connection, batch_key: str, batch_label: str, payload: dict):
    """Se ejecuta ANTES del commit de Picking; un fallo impide guardar una tanda incompleta."""
    if not payload or not payload.get("sales") or not payload.get("labels"):
        raise ValueError("No se recibieron el manifiesto y las etiquetas de la tanda.")
    # No re-ejecutar schema aquí: init_db lo inicializa antes de generar tandas.
    conn.execute("""INSERT INTO despacho_tandas
      (batch_key,batch_label,created_at,source_name,source_type,label_type,operation_type,pairing,sales_count,labels_count)
      VALUES(?, ?, datetime('now'), ?, ?, ?, ?, ?, ?, ?)""",
      (batch_key,batch_label,payload["source_name"],payload["source_type"],
       payload["label_type"],payload["operation_type"],payload["pairing"],len(payload["sales"]),payload["label_count"]))
    for idx, sale in enumerate(payload["sales"]):
        conn.execute("""INSERT INTO despacho_ventas
            (batch_key,ordinal,sale_id,buyer,page_no,is_original_page,items_json)
            VALUES (?,?,?,?,?,?,?)""",
            (batch_key, idx, sale["sale_id"], sale["buyer"], sale["page_no"],
             int(sale["is_original_page"]), json.dumps(sale["items"], ensure_ascii=False)))
    if payload["label_type"] == "ZPL":
        for idx, label in enumerate(payload["labels"]):
            conn.execute("""INSERT INTO despacho_etiquetas
             (batch_key,ordinal,sale_id,shipment_id,zpl) VALUES(?,?,?,?,?)""",
             (batch_key, idx, label["sale_id"], label["shipment_id"], label["zpl"]))
    else:
        for idx, file in enumerate(payload["labels"]):
            conn.execute("""INSERT INTO despacho_etiquetas_pdf
             (batch_key,ordinal,file_name,page_count,pdf_bytes) VALUES(?,?,?,?,?)""",
             (batch_key, idx, file["name"], file["pages"], file["bytes"]))


def list_batches(conn):
    return [dict(zip(("batch_key","label","created","source","type","operation","pairing","sales","labels"), row))
            for row in conn.execute("""SELECT batch_key,batch_label,created_at,source_name,label_type,
              operation_type,pairing,sales_count,labels_count FROM despacho_tandas ORDER BY created_at DESC, rowid DESC""")]


def load_selected(conn, keys: list[str], master: dict | None = None):
    if not keys:
        return {"sales":[],"zpl":[],"pdfs":[],"unknown":[],"label_count":0,"types":set(),"pairings":set()}
    keys = list(dict.fromkeys(keys))
    sales, zpl, pdfs, unknown, types, pairings = [], [], [], set(), set(), set()
    for key in keys:
        row = conn.execute("SELECT batch_label,label_type,pairing FROM despacho_tandas WHERE batch_key=?", (key,)).fetchone()
        if row is None:
            raise ValueError("Hay una tanda que ya no existe. Actualiza la selección.")
        label, kind, pairing = row
        types.add(kind); pairings.add(pairing)
        for sale_id, buyer, page, original_page, items_json in conn.execute("""SELECT sale_id,buyer,page_no,is_original_page,items_json
             FROM despacho_ventas WHERE batch_key=? ORDER BY ordinal""", (key,)):
            items = json.loads(items_json)
            for item in items:
                if not item.get("descripcion_kame") and master:
                    item["descripcion_kame"] = _string(master.get(item["sku"]))
                if not item.get("descripcion_kame"):
                    unknown.add(item["sku"])
            shipment = conn.execute("""SELECT shipment_id FROM despacho_etiquetas
                    WHERE batch_key=? AND sale_id=? AND COALESCE(shipment_id,'')<>'' LIMIT 1""", (key,sale_id)).fetchone()
            sales.append({"lote":label, "pagina_ml":int(page or 1), "pagina_ml_original": bool(original_page),
                          "mesa":"", "venta":sale_id, "cliente":buyer or "", "envio":shipment[0] if shipment else "",
                          "items":items})
        if kind == "ZPL":
            zpl.extend([r[0] for r in conn.execute("SELECT zpl FROM despacho_etiquetas WHERE batch_key=? ORDER BY ordinal", (key,))])
        else:
            pdfs.extend([r[0] for r in conn.execute("SELECT pdf_bytes FROM despacho_etiquetas_pdf WHERE batch_key=? ORDER BY ordinal", (key,))])
    return {"sales":sales,"zpl":zpl,"pdfs":pdfs,"unknown":sorted(unknown),
            "label_count":len(zpl) + sum(len(PdfReader(BytesIO(p),strict=False).pages) for p in pdfs),
            "types":types,"pairings":pairings}
