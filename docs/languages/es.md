# Leer y traducir artículos en Zotero

[简体中文](../../README.md) · [English](en.md) · [日本語](ja.md) · [Español](es.md) · [Français](fr.md) · [Deutsch](de.md)

Paper Translation Master muestra el PDF junto al texto reconocido o la traducción. Puedes corregir un pie de figura, anotar un pasaje y encontrar un artículo describiendo lo que recuerdas. Los resultados se guardan como adjuntos de Zotero.

## Instalar la versión 0.8.2

1. Descarga el paquete completo para Zotero 7–10. La edición para Mac requiere un chip de Apple y macOS 14 o posterior. [Windows x64](https://github.com/XvHaoR/paper-translation-master/releases/download/v0.8.2/paper-translation-master-windows-x64-0.8.2.zip) · [macOS arm64](https://github.com/XvHaoR/paper-translation-master/releases/download/v0.8.2/paper-translation-master-macos-arm64-0.8.2.zip)
2. Cierra Zotero, descomprime el ZIP en una carpeta permanente y ejecuta `Install.cmd` en Windows o `Install.command` en macOS.
3. Abre los complementos de Zotero desde **Herramientas**. En el menú del engranaje, elige instalar desde un archivo y selecciona el XPI de esa carpeta. Reinicia Zotero. Para actualizar, repite ambos pasos.

## Probar con un artículo

Haz clic derecho sobre un artículo y abre **论文翻译大师**. Para un PDF con texto seleccionable, elige **直接解析 PDF**. Para un escaneo puedes usar OCR local, los servicios de PaddleOCR o MinerU en la nube, o tu propio servidor MinerU. La opción en la nube envía el PDF seleccionado al proveedor. La traducción usa el modelo y la dirección que indiques en los ajustes.

**逐段双语** muestra cada párrafo con su traducción. Los comentarios sobre la traducción se conservan en ella. El paquete incluye un modelo local de búsqueda multilingüe. [Tutorial ilustrado con los controles en chino](../TUTORIAL.md)
