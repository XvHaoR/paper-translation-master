import json
from pathlib import Path
import shutil
import subprocess
import xml.etree.ElementTree as ET

import pytest


def test_shared_excel_workbook_preserves_text_and_escapes_xml():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node runtime required for shared browser exporter")
    source = Path(__file__).resolve().parents[2] / "app" / "static" / "table_excel.js"
    script = source.read_text(encoding="utf-8") + '\nprocess.stdout.write(PaperLoomExcel.buildWorkbook(' + json.dumps([
        {"name": "表/1", "rows": [["A & B", "<value>"], ["=1+2", "中文", "0012"]]},
    ]) + '));'
    result = subprocess.run([node, "-"], input=script, text=True, encoding="utf-8", capture_output=True, check=True)
    root = ET.fromstring(result.stdout)
    ns = {"s": "urn:schemas-microsoft-com:office:spreadsheet"}
    cells = root.findall(".//s:Data", ns)
    assert [node.text for node in cells] == ["A & B", "<value>", "=1+2", "中文", "0012"]
    assert all(node.attrib["{urn:schemas-microsoft-com:office:spreadsheet}Type"] == "String" for node in cells)
