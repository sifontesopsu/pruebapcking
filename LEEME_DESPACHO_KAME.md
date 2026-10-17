# WMS Aurora — documentos de despacho Flex (nueva función)

## Ubicación en la aplicación

En **Sorting → Carga y Corridas** o **Sorting → Administrador**, despliega **Documentos de despacho: manifiesto Kame + PDF etiquetas Flex**.

1. Selecciona uno o más **lotes Flex** para el mismo despacho. La selección es manual porque el WMS actual no tiene un campo confiable que distinga automáticamente Flex de Colecta en la base de datos. Los lotes cerrados también están disponibles mientras permanezcan registrados.
2. Comprueba las métricas: número de ventas, etiquetas encontradas y ventas sin etiqueta. Si falta una descripción Kame se mostrará el SKU en alerta y se identificará en el manifiesto como `SIN DESCRIPCION KAME - REVISAR`.
3. Pulsa **Descargar manifiesto Kame (PDF)**. Las descripciones proceden exclusivamente de `maestro_sku_ean.xlsx` según el SKU. Se conserva el número de venta, envío, cantidad y página/mesa del Control. El Control original no se modifica.
4. Para obtener el PDF de etiquetas, elige una de estas fuentes:
   - **ZPL/TXT ya cargados en Sorting**: marca la autorización de transferencia de datos a **Labelary** y pulsa **Preparar PDF consolidado Flex desde ZPL**. Después descarga el resultado. Se envían por HTTPS los bloques ZPL seleccionados en grupos de hasta 40 etiquetas; el PDF se valida para comprobar el número de páginas.
   - **PDF originales de Mercado Libre (unir localmente)**: sube varios PDF y pulsa **Unir todos los PDF de etiquetas**. No se envía ningún dato al servicio externo. Se requiere que el número de páginas coincida con los envíos únicos seleccionados (una etiqueta por página).
5. Descarga **todas las etiquetas Flex** y revisa visualmente una muestra impresa, incluidos QR/códigos de barras, antes de usar los documentos en producción.

## Controles y limitaciones

- La conversión desde ZPL se bloquea si falta cualquier etiqueta asociada a una venta. No se entrega un PDF parcialmente convertido si falla una solicitud o el número final de páginas es diferente.
- Un mismo número de envío se imprime **una sola vez** aunque figure en varias ventas. El PDF se ordena según lote, página y orden de venta del manifiesto.
- No se convierten los pequeños bloques ZPL de control/limpieza `^XA^MCY^XZ` en hojas de etiqueta.
- El PDF Kame se elabora con los datos importados y almacenados en **Sorting**, no con una consulta en vivo a Mercado Libre/Kame. No modifica estados, cantidades, asignaciones de mesa ni el picking.
- El documento Kame es un **nuevo manifiesto operativo**, no una modificación pixel a pixel del PDF original de Mercado Libre. Las páginas añadidas por descripciones largas reiteran la referencia a la página y mesa originales.
- El tamaño de etiqueta por defecto es **4×6 pulgadas (aprox. 100×150 mm), 203 dpi**. Si el archivo original es de otro tamaño, habrá que ajustar la configuración del motor ZPL.
- La conversión Labelary requiere conexión a Internet desde el servidor de Streamlit y permiso explícito para transmitir datos personales de los destinatarios a este servicio externo. Si no está permitida, utiliza la opción PDF de unión local.
- No se han validado aquí archivos ZPL/Control reales del día: se han verificado estructura, cantidad, generación PDF y correspondencia de datos con casos de prueba.

## Archivos modificados

- `app.py`: interfaz y lectura de datos de Sorting, nueva herramienta de despacho; corrección menor del patrón de búsqueda de shipment al anexar archivos ZPL.
- `despacho_kame.py`: generación PDF Kame, conversión ZPL→PDF, unión de varios PDF.
- `requirements.txt`: incluye `pypdf==5.9.0` para unir y validar PDFs.

## Pruebas

Para pruebas del nuevo módulo sin necesidad de ejecutar Streamlit:

```bash
python tests/test_despacho.py
python tests/test_database_dispatch.py
```

La prueba de conversión ZPL utiliza un simulador de respuesta PDF, sin transmitir datos a Labelary. La API real debe probarse con una tanda de etiquetas Flex reales antes del uso de producción.
