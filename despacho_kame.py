"""Manifiestos Kame y etiquetas de despacho por tanda de picking Aurora.

No modifica estados de picking ni comunicaciones en la nube.
"""
from __future__ import annotations

from io import BytesIO
import re
import time
from typing import Callable

import requests
from pypdf import PdfReader, PdfWriter
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen.canvas import Canvas


LABELARY_URL = "https://api.labelary.com/v1/printers/8dpmm/labels/4x6/"
ZPL_BATCH_LIMIT = 40  # Labelary: máximo 50 etiquetas por solicitud.
LABELARY_MAX_ATTEMPTS = 3


def _clean_text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _wrap(text: str, width_pt: float, font: str = "Helvetica", size: float = 8.0) -> list[str]:
    """Divide según ancho físico; respeta palabras largas y evita truncamientos."""
    words = _clean_text(text).split(" ")
    result: list[str] = []
    line = ""
    for word in words:
        if not word:
            continue
        candidate = (line + " " + word).strip()
        if stringWidth(candidate, font, size) <= width_pt:
            line = candidate
            continue
        if line:
            result.append(line)
            line = ""
        while word and stringWidth(word, font, size) > width_pt:
            low, high = 1, len(word)
            while low < high:
                mid = (low + high + 1) // 2
                if stringWidth(word[:mid], font, size) <= width_pt:
                    low = mid
                else:
                    high = mid - 1
            result.append(word[:low])
            word = word[low:]
        line = word
    if line:
        result.append(line)
    return result or ["-"]


def create_kame_manifest_pdf(sales: list[dict]) -> bytes:
    """PDF agrupado por lote / página del Control (referencias estables de mesa).

    Se crean páginas adicionales cuando una página del Control no alcanza;
    siempre se repite la referencia a la página ML y a la mesa asignada.
    Nunca se reemplaza un nombre desconocido por un título comercial.
    """
    if not sales:
        raise ValueError("No hay ventas para emitir un manifiesto Kame.")

    output = BytesIO()
    cv = Canvas(output, pagesize=A4, pageCompression=1)
    cv.setTitle("Aurora - Manifiesto de despacho en idioma Kame")
    w, h = A4
    m = 37
    y = 0.0
    sheet_num = 0

    def header(lot: str, control_page: int, mesa: object, continued: bool = False, original_page: bool = True):
        nonlocal y, sheet_num
        if sheet_num:
            cv.showPage()
        sheet_num += 1
        cv.setFillColor(colors.HexColor("#163854"))
        cv.rect(0, h - 75, w, 75, fill=1, stroke=0)
        cv.setFillColor(colors.white)
        cv.setFont("Helvetica-Bold", 14)
        cv.drawString(m, h - 34, "FERRETERIA AURORA  |  MANIFIESTO KAME")
        cv.setFont("Helvetica", 9)
        suffix = "  (continuacion)" if continued else ""
        page_reference = f"Pag. manifiesto ML: {control_page}" if original_page else "Documento generado desde Excel"
        lot_short = _clean_text(lot)[:46]
        cv.drawString(m, h - 54, f"{lot_short}  |  {page_reference}{suffix}")
        y = h - 90
        cv.setFillColor(colors.HexColor("#163854"))
        cv.setFont("Helvetica-Bold", 8)
        cv.drawString(m, y, "SKU KAME")
        cv.drawString(m + 118, y, "DESCRIPCION KAME")
        cv.drawRightString(w - m, y, "UNID.")
        y -= 8
        cv.setStrokeColor(colors.HexColor("#C9D6E1"))
        cv.line(m, y, w - m, y)
        y -= 13

    def ensure_space(required: float, sale: dict):
        if y - required < 45:
            header(sale["lote"], int(sale["pagina_ml"]), sale.get("mesa"), True, sale.get("pagina_ml_original", True))

    def sale_header(sale: dict, continued=False):
        nonlocal y
        parts = []
        if sale.get("cliente"):
            parts.append("Cliente: " + _clean_text(sale["cliente"]))
        if sale.get("pack_id"):
            parts.append("Pack: " + _clean_text(sale["pack_id"]))
        if sale.get("destino"):
            parts.append("Destino: " + _clean_text(sale["destino"]))
        lines = _wrap("  |  ".join(parts), w - 2 * m - 20, size=7) if parts else []
        box_height = max(38, 21 + len(lines) * 10)
        ensure_space(box_height + 35, sale)
        cv.setFillColor(colors.HexColor("#ECF2F7"))
        cv.rect(m, y - box_height + 10, w - 2 * m, box_height, fill=1, stroke=0)
        cv.setFillColor(colors.HexColor("#15344D"))
        cv.setFont("Helvetica-Bold", 9)
        text = "VENTA: " + _clean_text(sale["venta"])
        if continued:
            text += " (continua)"
        cv.drawString(m + 7, y - 2, text)
        cv.setFont("Helvetica", 8)
        cv.drawRightString(w - m - 7, y - 2, "ENVIO: " + _clean_text(sale.get("envio") or "No identificado"))
        cv.setFont("Helvetica", 7)
        for i, fragment in enumerate(lines):
            cv.drawString(m + 7, y - 15 - i * 10, fragment)
        y -= box_height + 5

    for sale in sales:
        is_new_group = (not sheet_num or
                        group_key != (sale["lote"], int(sale["pagina_ml"])))
        group_key = (sale["lote"], int(sale["pagina_ml"]))
        if is_new_group:
            header(sale["lote"], int(sale["pagina_ml"]), sale.get("mesa"), original_page=sale.get("pagina_ml_original", True))
        sale_header(sale)
        items = sale.get("items") or []
        if not items:
            ensure_space(18, sale)
            cv.setFillColor(colors.red)
            cv.setFont("Helvetica", 8)
            cv.drawString(m + 5, y, "ATENCION: Esta venta no tiene productos registrados")
            y -= 18
        for item in items:
            sku = _clean_text(item.get("sku"))
            name = _clean_text(item.get("descripcion_kame")) or "SIN DESCRIPCION KAME - REVISAR"
            lines = _wrap(name, w - 2 * m - 162, size=8)
            height = max(18, len(lines) * 11 + 7)
            if y - height < 45:
                header(sale["lote"], int(sale["pagina_ml"]), sale.get("mesa"), True, sale.get("pagina_ml_original", True))
                sale_header(sale, continued=True)
            cv.setFillColor(colors.HexColor("#212E39"))
            cv.setFont("Helvetica-Bold", 8)
            cv.drawString(m + 3, y, sku[:24])
            cv.setFont("Helvetica", 8)
            for i, line in enumerate(lines):
                cv.drawString(m + 121, y - i * 11, line)
            cv.setFont("Helvetica-Bold", 9)
            cv.drawRightString(w - m - 4, y, str(int(item["cantidad"])))
            y -= height
            cv.setStrokeColor(colors.HexColor("#DFE6EC"))
            cv.line(m, y + 6, w - m, y + 6)
        y -= 14

    if sheet_num:
        cv.showPage()
    cv.save()
    return output.getvalue()


def _is_printable_zpl(block: str) -> bool:
    """No enviar comandos de mantenimiento como ^XA^MCY^XZ como etiquetas."""
    s = re.sub(r"\s+", "", block or "").upper()
    if not (s.startswith("^XA") and s.endswith("^XZ")):
        return False
    if s in {"^XA^MCY^XZ", "^XA^XZ", "^XA^JUS^XZ"}:
        return False
    return bool(re.search(r"\^(?:FD|GF|BC|BQ|B3|BX|FO|FT|GB)", s))


def _request_labelary_pdf(zpl_blocks: list[str], session: requests.Session) -> bytes:
    body = "".join(zpl_blocks).encode("utf-8")
    last_error = ""
    for attempt in range(LABELARY_MAX_ATTEMPTS):
        try:
            response = session.post(
                LABELARY_URL,
                data=body,
                headers={"Accept": "application/pdf", "Content-Type": "application/x-www-form-urlencoded"},
                timeout=(15, 120),
            )
        except requests.RequestException as exc:
            last_error = str(exc)
            if attempt + 1 < LABELARY_MAX_ATTEMPTS:
                time.sleep(2 ** attempt)
                continue
            break
        if response.status_code == 200 and response.content.startswith(b"%PDF-"):
            return response.content
        last_error = f"HTTP {response.status_code}: {response.text[:200]}"
        if response.status_code not in (429, 500, 502, 503, 504):
            break
        if attempt + 1 < LABELARY_MAX_ATTEMPTS:
            time.sleep(max(2 ** attempt, int(response.headers.get("Retry-After", "0")) if response.headers.get("Retry-After", "0").isdigit() else 0))
    raise RuntimeError(f"No se pudo convertir el lote de etiquetas: {last_error}")


def convert_zpl_labels_to_pdf(
    blocks: list[str], *, progress: Callable[[int, int], None] | None = None,
    request_pdf: Callable[[list[str]], bytes] | None = None,
) -> bytes:
    """Conversión fiel con motor ZPL (Labelary), 40 etiquetas por envío.

    El servicio externo recibe nombre/dirección presentes en cada etiqueta;
    pedir autorización en la UI ANTES de llamar a esta función.
    """
    if not blocks:
        raise ValueError("No hay etiquetas para convertir.")
    invalid = [i + 1 for i, block in enumerate(blocks) if not _is_printable_zpl(block)]
    if invalid:
        raise ValueError(f"ZPL inválido o sin contenido imprimible en etiqueta(s): {invalid[:12]}")

    writer = PdfWriter()
    session = requests.Session() if request_pdf is None else None
    batches = [blocks[i:i + ZPL_BATCH_LIMIT] for i in range(0, len(blocks), ZPL_BATCH_LIMIT)]
    try:
        for idx, part in enumerate(batches, 1):
            pdf_bytes = request_pdf(part) if request_pdf is not None else _request_labelary_pdf(part, session)
            reader = PdfReader(BytesIO(pdf_bytes), strict=False)
            # Cualquier diferencia indica etiquetas omitidas; no entregar PDF parcial.
            if len(reader.pages) != len(part):
                raise RuntimeError(
                    f"El conversor devolvió {len(reader.pages)} páginas para {len(part)} etiquetas (bloque {idx})."
                )
            for page in reader.pages:
                writer.add_page(page)
            if progress:
                progress(idx, len(batches))
            # Respeta límite del servicio compartido sin demorar artificialmente cada envío.
            if idx < len(batches) and request_pdf is None:
                time.sleep(0.40)
        out = BytesIO()
        writer.write(out)
        result = out.getvalue()
        if len(PdfReader(BytesIO(result)).pages) != len(blocks):
            raise RuntimeError("No coincide la cantidad final de páginas con las etiquetas solicitadas.")
        return result
    finally:
        if session is not None:
            session.close()


def merge_label_pdfs(files: list[bytes]) -> bytes:
    """Unir PDFs originales localmente, sin enviar información a terceros."""
    if not files:
        raise ValueError("No se seleccionaron PDF de etiquetas.")
    writer = PdfWriter()
    for raw in files:
        reader = PdfReader(BytesIO(raw), strict=False)
        for page in reader.pages:
            writer.add_page(page)
    out = BytesIO()
    writer.write(out)
    return out.getvalue()
