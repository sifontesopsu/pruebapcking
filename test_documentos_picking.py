"""Pruebas sin Streamlit, Google Sheets ni llamadas externas."""
from io import BytesIO
import ast
from pathlib import Path
import sqlite3
import sys

import pandas as pd
from pypdf import PdfReader, PdfWriter
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen.canvas import Canvas

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from manifiestos_etiquetas import create_schema, prepare_upload, save_with_picking, list_batches, load_selected
from despacho_kame import create_kame_manifest_pdf, merge_label_pdfs, convert_zpl_labels_to_pdf

source=(ROOT/'app.py').read_text()
parse=ast.parse(source)
functions={node.name for node in parse.body if isinstance(node,ast.FunctionDef)}
assert 'page_sorting_upload' not in functions
assert 'page_sorting_camarero' not in functions
assert 'page_sorting_admin' not in functions
assert 'page_manifiestos_y_etiquetas' in functions
assert '"5) Manifiestos y etiquetas"' in source
assert 'PICKING_SHEETS_BACKUP_ENABLED = False' in source
assert 'DEFAULT_PICKING_SHEETS_WEBHOOK_URL = ""' in source

conn=sqlite3.connect(':memory:')
create_schema(conn)
master={'901':'LLAVE FRANCESA KAME 10 PULGADAS','902':'CINTA AISLADORA KAME 19MM'}
df=pd.DataFrame([
    {'ml_order_id':'200001111111','buyer':'Ana','sku_ml':'901','qty':2,'title_ml':'Nombre ML equivocado','pagina_ml':2},
    {'ml_order_id':'200001111111','buyer':'Ana','sku_ml':'902','qty':1,'title_ml':'Nombre ML equivocado','pagina_ml':2},
    {'ml_order_id':'200001222222','buyer':'Leo','sku_ml':'902','qty':3,'title_ml':'Otro ML','pagina_ml':3},
])
labels=(b'^XA^FO10,10^FDVenta: 200001222222^FS^FO40,10^FDLeo^FS^XZ'
        b'^XA^MCY^XZ'
        b'^XA^FO10,10^FDVenta: 200001111111^FS^FO40,10^FDAna^FS^XZ')
p=prepare_upload(df,master,[('etiquetas.txt',labels)],'manifiesto.pdf','PDF')
assert len(p['sales'])==2 and p['label_count']==2 and p['pairing']=='VENTA_ZPL'
assert '200001111111' in p['labels'][0]['sale_id']  # reordenado por manifiesto
assert p['sales'][0]['page_no']==2 and p['sales'][0]['is_original_page']
assert p['sales'][0]['items'][0]['descripcion_kame']=='LLAVE FRANCESA KAME 10 PULGADAS'
assert 'Nombre ML equivocado' not in str(p['sales'])

save_with_picking(conn,'PK-1','Primera tanda',p)
conn.commit()
assert list_batches(conn)[0]['label']=='Primera tanda'
result=load_selected(conn,['PK-1'],master)
assert len(result['sales'])==2 and len(result['zpl'])==2
manifest=create_kame_manifest_pdf(result['sales'])
assert manifest.startswith(b'%PDF-')
manifest_text='\n'.join(page.extract_text() for page in PdfReader(BytesIO(manifest)).pages)
assert 'LLAVE FRANCESA KAME' in manifest_text
assert 'Nombre ML equivocado' not in manifest_text
assert '200001111111' in manifest_text
assert 'Pag. manifiesto ML: 2' in manifest_text

# Simular reinicio de picking: sus tablas se borran; archivo independiente permanece.
conn.executescript('''CREATE TABLE picking_ots(batch_key TEXT);INSERT INTO picking_ots VALUES("PK-1");DELETE FROM picking_ots;''')
assert len(list_batches(conn)) == 1
assert len(load_selected(conn,['PK-1'])['sales'])==2

# Múltiples PDF de etiquetas originales con 1 página por venta.
def tiny_pdf(count):
    out=BytesIO(); cv=Canvas(out, pagesize=A4)
    for i in range(count):
        cv.drawString(15,800, f'Etiqueta {i+1}')
        cv.showPage()
    cv.save(); return out.getvalue()

pdf1=tiny_pdf(1);pdf2=tiny_pdf(1)
p2=prepare_upload(df,master,[('a.pdf',pdf1),('b.pdf',pdf2)],'ventas.xlsx','EXCEL')
assert p2['label_type']=='PDF' and p2['label_count']==2 and p2['pairing']=='SIN_CRUCE'
save_with_picking(conn,'PK-2','Segunda tanda',p2)
conn.commit()
combined=load_selected(conn,['PK-1','PK-2'],master)
assert combined['label_count']==4 and len(combined['sales'])==4
assert len(PdfReader(BytesIO(merge_label_pdfs(combined['pdfs']))).pages)==2
assert len(list_batches(conn))==2

# Rechazar PDF parcial (no se crea la tanda ni se omiten etiquetas silenciosamente).
try:
    prepare_upload(df,master,[('incompleto.pdf',pdf1)],'ventas.xlsx','EXCEL')
except ValueError as exc:
    assert 'páginas' in str(exc)
else:
    raise AssertionError('Se aceptó una etiqueta faltante')

# Rechazar archivos maliciosamente renombrados / mezcla de formatos.
for files in ([('no-es-pdf.pdf',b'hola')],[('a.pdf',pdf1),('x.zpl',labels)]):
    try:
        prepare_upload(df,master,files,'x','PDF')
    except ValueError:
        pass
    else:
        raise AssertionError('Se aceptó formato inválido')

# Convertir ZPL sin red ni datos de envío en terceros.
def mock_pdf(part):
    return tiny_pdf(len(part))
zpl_pdf=convert_zpl_labels_to_pdf(combined['zpl'],request_pdf=mock_pdf)
assert len(PdfReader(BytesIO(zpl_pdf)).pages)==2

# Simulación de archivo largo (límite 40 etiquetas por lote).
long_blocks=[f'^XA^FO15,15^FDSALE {i}^FS^XZ' for i in range(95)]
assert len(PdfReader(BytesIO(convert_zpl_labels_to_pdf(long_blocks,request_pdf=mock_pdf))).pages)==95

print('OK: 5 módulos, sin Sorting/Sheets, dos formatos, manifiesto Kame, archivo histórico, etiquetas 95, faltantes')
