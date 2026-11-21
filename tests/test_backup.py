"""Offline integration tests using a fake read-only adb executable."""
import importlib.util
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1] / 'windows' / 'ffd_backup.py'
spec = importlib.util.spec_from_file_location('ffd_backup', SOURCE)
backup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backup)

FAKE_ADB = r'''#!/usr/bin/env python3
import hashlib
import os
from pathlib import Path
import shutil
import sys
args=sys.argv[1:]
if args[:2] == ['-s','TEST-DEVICE']: args=args[2:]
if args == ['devices','-l']:
 print('List of devices attached\nTEST-DEVICE\tdevice product:test model:test\n')
elif args[:3] == ['shell','pm','path']:
 print('package:/data/app/test/base.apk')
 print('package:/data/app/test/split_config.arm64_v8a.apk')
elif args[:3] == ['shell','dumpsys','package']:
 print('versionCode=120 versionName=1.2.0')
elif args[:2] == ['shell','find']:
 if os.environ.get('SIM_NO_OBB') == '1':
  print('find: No such file or directory', file=sys.stderr)
  sys.exit(1)
 if os.environ.get('SIM_OBB_DENIED') == '1':
  print('find: Permission denied', file=sys.stderr)
  sys.exit(1)
 print(args[2] + '/main.120.com.square_enix.android_googleplay.ffl_gp.obb')
elif args[0] == 'pull':
 remote= args[1]
 target=Path(args[2]); target.parent.mkdir(parents=True,exist_ok=True)
 if remote.endswith('base.apk'): target.write_bytes(b'base apk contents')
 elif remote.endswith('.obb'): target.write_bytes(b'obbs are not bundled in the tool')
 else: target.write_bytes(b'arm64 split contents')
 print('1 file pulled')
elif args[0] == 'shell' and args[1].startswith('sha256sum '):
 import shlex
 r=shlex.split(args[1])[1]
 content = b'base apk contents' if r.endswith('base.apk') else (b'obbs are not bundled in the tool' if r.endswith('.obb') else b'arm64 split contents')
 print(hashlib.sha256(content).hexdigest() + '  ' + r)
else:
 print('Unhandled command: '+repr(args), file=sys.stderr);sys.exit(4)
'''


class BackupIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.fake = self.root / 'adb'
        self.fake.write_text(FAKE_ADB, encoding='utf-8')
        self.fake.chmod(self.fake.stat().st_mode | stat.S_IXUSR)
        self._saved = {k: os.environ.get(k) for k in ('SIM_NO_OBB', 'SIM_OBB_DENIED')}
        self.addCleanup(self.restore_env)
        os.environ.pop('SIM_NO_OBB', None)
        os.environ.pop('SIM_OBB_DENIED', None)

    def restore_env(self):
        for k,v in self._saved.items():
            if v is None: os.environ.pop(k,None)
            else: os.environ[k] = v

    def test_complete_backup_three_files_device_verified(self):
        output = backup.export(backup.Adb(str(self.fake), 'TEST-DEVICE'), self.root / 'out', verify_device=True, on_log=lambda _: None)
        manifest = json.loads((output/'manifest.json').read_text())
        self.assertEqual(manifest['status'], 'completed')
        self.assertEqual(manifest['obb_status'], 'found')
        self.assertEqual(manifest['version']['version_code'], '120')
        self.assertEqual(len(manifest['files']), 3)
        for info in manifest['files']:
            file = output / info['path']
            self.assertTrue(file.exists())
            self.assertEqual(info['sha256'], backup.hash_file(file))
            self.assertEqual(info['device_sha256'], info['sha256'])

    def test_no_obb_is_not_failure(self):
        os.environ['SIM_NO_OBB'] = '1'
        output=backup.export(backup.Adb(str(self.fake), 'TEST-DEVICE'), self.root, on_log=lambda _: None)
        manifest=json.loads((output/'manifest.json').read_text())
        self.assertEqual(manifest['obb_status'], 'directory_absent')
        self.assertEqual(manifest['status'], 'completed')
        self.assertEqual(len(manifest['files']), 2)

    def test_obb_denied_is_explicit_warning(self):
        os.environ['SIM_OBB_DENIED'] = '1'
        output=backup.export(backup.Adb(str(self.fake), 'TEST-DEVICE'), self.root, on_log=lambda _: None)
        manifest=json.loads((output/'manifest.json').read_text())
        self.assertEqual(manifest['status'], 'completed_with_warnings')
        self.assertTrue(manifest['obb_status'].startswith('unavailable'))
        self.assertEqual(len(manifest['files']), 2)

    def test_refuses_package_injection(self):
        for package in ['com.example; rm -rf /', 'com.hello/world', 'com.foo\nbar', '']:
            with self.assertRaises(backup.BackupError):
                backup.safe_package(package)

    def test_auto_detect_one_device(self):
        output=backup.export(backup.Adb(str(self.fake)), self.root, on_log=lambda _: None)
        manifest=json.loads((output/'manifest.json').read_text())
        self.assertEqual(manifest['device_serial'], 'TEST-DEVICE')


if __name__ == '__main__': unittest.main()
