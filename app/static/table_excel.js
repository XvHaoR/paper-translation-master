/* Shared by the web reader, saved offline pages, and Zotero. Excel SpreadsheetML. */
(function (root) {
  "use strict";
  function xmlEsc(value) {
    return String(value == null ? "" : value).replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f]/g, "")
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }
  function tableRows(table) {
    const rows = [];
    for (const tr of table.rows) {
      const row = [];
      for (const cell of tr.cells) {
        const copy = cell.cloneNode(true);
        copy.querySelectorAll("button").forEach(node => node.remove());
        copy.querySelectorAll("[data-latex]").forEach(node => { node.textContent = node.dataset.latex; });
        row.push((copy.textContent || "").replace(/\s+/g, " ").trim());
      }
      rows.push(row);
    }
    return rows;
  }
  function buildWorkbook(items) {
    const sheets = items.map((item, i) => {
      const name = String(item.name || `表${i + 1}`).replace(/[\\/?*\[\]:]/g, "_").slice(0, 31);
      const rows = item.rows.map(row => "<Row>" + row.map(value =>
        '<Cell><Data ss:Type="String">' + xmlEsc(value) + "</Data></Cell>"
      ).join("") + "</Row>").join("");
      return '<Worksheet ss:Name="' + xmlEsc(name) + '"><Table>' + rows + "</Table></Worksheet>";
    }).join("");
    return '<?xml version="1.0" encoding="UTF-8"?>\n<?mso-application progid="Excel.Sheet"?>\n'
      + '<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet" xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet">'
      + sheets + "</Workbook>";
  }
  root.PaperLoomExcel = { tableRows, buildWorkbook };
})(globalThis);
