"""Exercise native-text region recovery without OCR, providers or Zotero UI."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest


FIRST = "A key plant trait linking canopy phenology with productivity was shown to be leaf age. Newly flushed leaves and maturing leaves show higher photosynthetic rates than old leaves. "
SECOND = "The leaf area of young and mature leaves increased during the dry seasons and promoted canopy photosynthesis. Overall, leaf age seasonality is a vital factor influencing forest productivity."
BASE = [0.08, 0.65, 0.47, 0.9]


def item(text, x, baseline):
    return {"str": text, "transform": [10, 0, 0, 10, x, baseline], "width": 35}


def recover(page_items, text=FIRST + SECOND):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node runtime required for native PDF region recovery")
    source = Path(__file__).resolve().parents[2] / "zotero/modules/source-regions.js"
    setup = "const PaperLoom={}; const Zotero={logError:()=>{}};\n"
    script = setup + source.read_text(encoding="utf-8") + "\n" + f"const fixtures={json.dumps(page_items)};const text={json.dumps(text)};const bbox={json.dumps(BASE)};" + """
let reads=0;
const pdf={numPages:fixtures.length,async getPage(number){reads++;return {
  getViewport(){return {width:100,height:100,convertToViewportPoint(x,y){return [x,100-y];}};},
  async getTextContent(){return {items:fixtures[number-1]};}
};}};
const state={frame:{},reader:{_internalReader:{_primaryView:{_iframeWindow:{PDFViewerApplication:{pdfDocument:pdf}}}}}};
(async()=>{
  const regions=await PaperLoom.SourceRegions.resolve(state,{block:'p',page:1,bbox,text,type:'paragraph'});
  const again=await PaperLoom.SourceRegions.resolve(state,{block:'p',page:1,bbox,text,type:'paragraph'});
  process.stdout.write(JSON.stringify({regions,again,reads}));
})().catch(error=>{process.stderr.write(String(error));process.exitCode=1;});
"""
    result = subprocess.run([node, "-"], input=script, encoding="utf-8", text=True, capture_output=True, check=True)
    return json.loads(result.stdout)


def test_cross_column_keeps_two_separate_rectangles_and_excludes_next_paragraph():
    result = recover([[item(FIRST, 10, 25), item(SECOND, 55, 60), item("A completely different following paragraph", 55, 35)]])
    assert len(result["regions"]) == 2
    assert result["regions"][0] == {"page": 1, "bbox": BASE}
    assert result["regions"][1]["bbox"][0] > 0.5
    assert result["regions"][1]["bbox"][3] < 0.5
    assert result["again"] == result["regions"] and result["reads"] == 1


def test_cross_page_recovers_continuation_without_header_or_footer():
    result = recover([
        [item(FIRST, 10, 25), item("ZZZZ QQQQ publication footer 77777", 10, 3)],
        [item("ZZZZ QQQQ page header 88888", 10, 95), item(SECOND, 55, 60)],
    ])
    assert [region["page"] for region in result["regions"]] == [1, 2]
    assert 0.3 < result["regions"][1]["bbox"][1] < result["regions"][1]["bbox"][3] < 0.5


def test_repeated_start_is_anchored_to_the_known_original_rectangle():
    result = recover([[item(FIRST, 55, 80), item(FIRST, 10, 25), item(SECOND, 55, 60)]])
    assert len(result["regions"]) == 2
    assert result["regions"][1]["bbox"][1] > 0.3


@pytest.mark.parametrize("pages,text", [
    ([[]], FIRST + SECOND),
    ([[item("Different text unrelated to this paragraph", 10, 25)]], FIRST + SECOND),
    ([[item(FIRST, 10, 25)]], FIRST + "A mismatched ending must not select the next paragraph."),
])
def test_unverifiable_or_scanned_text_keeps_only_known_coordinates(pages, text):
    result = recover(pages, text)
    assert result["regions"] == [{"page": 1, "bbox": BASE}]
