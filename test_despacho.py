from io import BytesIO
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from despacho_kame import create_kame_manifest_pdf,convert_zpl_labels_to_pdf,merge_label_pdfs,_is_printable_zpl
from pypdf import PdfReader
from reportlab.pdfgen import canvas

assert not _is_printable_zpl('^XA^MCY^XZ')
assert _is_printable_zpl('^XA^FO20,20^FDML^FS^XZ')

records=[]
for lot,page,n in [('Lote #1',1,15),('Lote #1',2,15),('Lote #2',1,20)]:
 for i in range(n):
  records.append(dict(lote=lot, pagina_ml=page, mesa=2,venta=str(200000000000+i),envio=str(46000000000+i),cliente='Maria Ejemplo',pack_id='',items=[dict(sku='500101301800',descripcion_kame='ADHESIVO CERAMICO CON DESCRIPCION TECNICA KAME LARGA ANTIDESLIZANTE PARA CUBRIMIENTO EN CERAMICA DE BAJA ABSORCION '+str(i),cantidad=2), dict(sku='500101301801',descripcion_kame='',cantidad=1)]))

pdf=create_kame_manifest_pdf(records)
p=Path(__file__).resolve().parent / '_test_manifiesto_kame.pdf';p.write_bytes(pdf)
reader=PdfReader(BytesIO(pdf))
txt=' '.join(pg.extract_text() or '' for pg in reader.pages)
assert len(reader.pages)>=3
assert txt.count('VENTA:') >= 50, txt.count('VENTA:')
assert all(('VENTA: ' + str(200000000000+i)) in txt for i in range(15))
assert 'SIN DESCRIPCION KAME' in txt
assert '500101301800' in txt
assert 'Pag. Control ML: 2' in txt

calls=[]
def fake_pdf(batch):
 calls.append(len(batch))
 out=BytesIO()
 c=canvas.Canvas(out,pagesize=(288,432))
 for x in batch:
  c.drawString(30,300,'Etiqueta Prueba')
  c.showPage()
 c.save()
 return out.getvalue()

blocks=['^XA^FO20,20^FD' + str(n) + '^FS^XZ' for n in range(90)]
res=convert_zpl_labels_to_pdf(blocks,request_pdf=fake_pdf)
assert calls == [40,40,10],calls
assert len(PdfReader(BytesIO(res)).pages)==90
merge=merge_label_pdfs([res,fake_pdf(blocks[:2])])
assert len(PdfReader(BytesIO(merge)).pages)==92
print('OK: 50 ventas en',len(reader.pages),'páginas de manifiesto, 90 etiquetas en',calls,'y 92 fusionadas')
