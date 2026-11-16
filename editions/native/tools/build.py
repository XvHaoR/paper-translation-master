"""Build the XPI from the source tree; no absolute machine paths are embedded."""
import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
manifest = json.loads((ROOT/'addon/manifest.json').read_text(encoding='utf-8'))
config = json.loads((ROOT/'addon/config.json').read_text(encoding='utf-8'))
assert not config.get('localRuntimeDescriptor') and not config.get('projectRoot')
assert manifest['applications']['zotero'].get('update_url'), 'Zotero requires applications.zotero.update_url'
target = ROOT/'dist'/f"paper-translation-master-{manifest['version']}.xpi"
target.parent.mkdir(exist_ok=True)
with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as archive:
    for file in sorted((ROOT/'addon').rglob('*')):
        if not file.is_file() or '__pycache__' in file.parts:
            continue
        info = zipfile.ZipInfo(file.relative_to(ROOT/'addon').as_posix(), (2026, 11, 14, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o644 << 16
        archive.writestr(info, file.read_bytes())
(target.parent/'SHA256SUMS.txt').write_text(
    f'{hashlib.sha256(target.read_bytes()).hexdigest()}  {target.name}\n', encoding='ascii')
print(target.name)
