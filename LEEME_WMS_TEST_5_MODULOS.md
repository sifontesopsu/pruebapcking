# WMS Aurora - versión TEST, 5 módulos, documentos por tanda

## Menú principal

1. **Picking**: flujo actual por OT y PDA.
2. **Importar ventas**: recibe **Excel ML o manifiesto PDF** y, en esa misma pantalla, **las etiquetas TXT/ZPL o PDF de esas ventas**. La importación exige ambos archivos antes de crear una tanda.
3. **Cortes de la tanda (PDF)**: flujo de cortes existente.
4. **Administrador**: tareas, incidencias, reset de Picking, respaldo/restauración local.
5. **Manifiestos y etiquetas**: selecciona una o varias tandas registradas, descarga el manifiesto Kame e imprime un PDF único de las etiquetas **Flex** seleccionadas.

**Sorting fue retirado del código y del menú.** No hay pantalla de camarero, mesas ni carga de Sorting. No se borran archivos de producción o bases de datos ajenas al entorno TEST.

## Flujo de prueba recomendado

1. En `2) Importar ventas`, selecciona Flex o Colecta, la modalidad de Picking y los pickeadores de la tanda.
2. Sube tu manifiesto PDF de Mercado Libre (o el Excel de ventas) y luego **los archivos de etiquetas de esa misma tanda**. Se admiten varios TXT/ZPL o varios PDF; **no mezclar ambos formatos dentro de una sola tanda**.
3. Comprueba las cantidades de ventas y etiquetas y el resultado de conciliación. Si el ZPL no tiene identificadores legibles de venta, el sistema comprueba el número de etiquetas pero **no puede garantizar el orden respecto del manifiesto**. Revísalo antes de imprimir.
4. Confirma `Crear tanda de picking y guardar sus documentos`. Ventas, tareas y documentos se almacenan juntos en SQLite. Si la carga no valida o falla el almacenamiento de documentos, no se confirma una tanda incompleta.
5. Trabaja el Picking normalmente.
6. Abre `5) Manifiestos y etiquetas`, selecciona una o varias tandas y descarga **Manifiesto Kame PDF**. La descripción sale del `maestro_sku_ean.xlsx`, nunca se sustituye por el título ML. Los SKU desconocidos aparecen marcados.
7. Descarga el **PDF único de etiquetas Flex**. Tandas de Colecta sirven para generar manifiestos, pero sus etiquetas se excluyen del PDF Flex. Si la tanda es ZPL, se requiere autorizar el servicio Labelary (datos personales de envío). Si la tanda es PDF original, la unión es local.

## Reglas y limitaciones

- Para esta primera versión, **cada venta debe corresponder a una etiqueta**, salvo que el ZPL tenga coincidencia inequívoca 1:1 con todas las ventas. Algunos paquetes combinados de Mercado Libre pueden representar varias ventas en una sola etiqueta; si el conteo no coincide se impide el alta para evitar imprimir envíos incompletos. Trae un ejemplo real para agregar soporte a esos paquetes.
- No convierte el formato del PDF original de ML; genera un manifiesto de preparación nuevo, con los mismos números de venta, SKU y cantidades que se importaron, los nombres Kame y referencia a página original cuando la fuente es PDF.
- La selección múltiple preserva el orden de los lotes elegidos. La conversión ZPL original es 4 x 6 pulgadas a 203 dpi mediante Labelary, en tandas de hasta 40 páginas por petición.
- Los documentos se conservan dentro de **cuatro tablas independientes de Picking**: `despacho_tandas`, `despacho_ventas`, `despacho_etiquetas`, `despacho_etiquetas_pdf`. El botón **Reiniciar corrida de Picking** deja este histórico sin alterar. El respaldo y restauración **manual** de Picking incluye estas tablas.
- Tandas creadas en versiones **anteriores** no tienen etiquetas asociadas a este archivo histórico. Será necesario cargarlas en esta edición para probarlas; no se inventan ni recuperan etiquetas antiguas desde Sheets.
- Esta versión tiene SQLite local independiente: `aurora_ml_PRUEBAS_SIN_SHEETS.db`. **No usa Apps Script, Google Sheets ni el servidor de sincronización de producción.** No cambies la ruta de base de datos ni sustituyas la aplicación de producción.
- La descarga en formato PDF de los ZPL **requiere conexión externa a Labelary y autorización**. Puede haber otras consultas de solo lectura (imágenes de publicaciones). Para pruebas totalmente locales, usa etiquetas originales en PDF y mantén la aplicación en tu equipo.
- El robot de stock de Kame/Git se **excluyó deliberadamente del ZIP TEST** para evitar ejecuciones accidentales. Se incluye `stock_kame.json` como muestra de existencias, que podría quedar desactualizada.

## Iniciar en Windows

Descomprime en una carpeta nueva y ejecuta desde PowerShell dentro de `v1-picking-main`:

```powershell
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

Verás en la barra lateral `MODO PRUEBAS · SIN GOOGLE SHEETS · BD LOCAL INDEPENDIENTE`.

## Pruebas de código (sin Internet)

```powershell
python tests/test_documentos_picking.py
python tests/test_integracion_sqlite.py
python tests/test_despacho.py
python tests/test_sin_sheets.py
```

Se validaron por código la importación conjunta, persistencia, 2 tandas de distinto tamaño, reinicio de picking con documentos conservados, respaldo/restauración manual, PDF Kame y PDF con 95 etiquetas simuladas. **Pendiente**: ejecutar Streamlit con una tanda real Flex, comprobar la correspondencia de los IDs de envíos y validar una conversión real ZPL contra Labelary antes de usarla para despachar.
