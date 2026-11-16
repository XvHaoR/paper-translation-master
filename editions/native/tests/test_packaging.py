"""Standalone source packages exclude runtime state and private inputs."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('native_package', ROOT / 'tools/package.py')
package = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package)


class Packaging(unittest.TestCase):
    def test_source_package_omits_environment_documents_and_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            names = ['README.md', 'addon/manifest.json', 'runtime/app/server.py', 'runtime/scripts/start.py',
                     'runtime/requirements.txt', 'licenses/notice.txt', 'tests/test_example.py',
                     'runtime/vendor/library.py', 'python/python.exe', 'runtime/app/__pycache__/file.pyc',
                     'runtime/scripts/launcher.lock', 'runtime/settings.json', 'runtime/app/.env',
                     'runtime/app/workspace/private.pdf', 'runtime/app/service-state-test.json', 'dist/old.xpi',
                     'tests/private.docx', 'tools/private.exe']
            for name in names:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('fixture', 'utf-8')
            selected = {p.relative_to(root).as_posix() for p in package.source_files(root)}
            self.assertEqual(selected, set(names[:7]))

    def test_audit_rejects_user_paths_and_token_patterns(self):
        # Build fixtures dynamically so the packaging audit does not mistake the
        # regression itself for a real user path or API credential.
        for content in [b'C:' + bytes([92]) + b'Users' + bytes([92]) + b'Example' + bytes([92]),
                        b'sk-' + b'x' * 32]:
            with self.assertRaises(ValueError):
                package.audit('example.json', content)
        package.audit('example.json', b'{"model":"example"}')


if __name__ == '__main__':
    unittest.main(verbosity=2)
