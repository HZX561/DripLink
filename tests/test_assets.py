"""Archive integrity and safe restoration without real research assets or network."""
import hashlib
from pathlib import Path
import stat
import tempfile
import unittest
import zipfile

from scripts.download_assets import restore,assemble,ROOT


class AssetRestoreTests(unittest.TestCase):
    def setUp(self):
        runtime=ROOT/'.runtime';runtime.mkdir(exist_ok=True)
        self.temporary=tempfile.TemporaryDirectory(dir=runtime)
        self.addCleanup(self.temporary.cleanup);self.root=Path(self.temporary.name)

    def archive(self,entries):
        path=self.root/'bundle.zip'
        with zipfile.ZipFile(path,'w') as archive:
            for name,content in entries:archive.writestr(name,content)
        info={'name':path.name,'bytes':path.stat().st_size,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
        return path,info

    def test_restore_is_verified_and_repeatable(self):
        path,info=self.archive([('models/reader.pt',b'example-checkpoint'),('models/selection.json',b'{}')])
        destination=self.root/'project'
        self.assertEqual(restore(path,info,destination,'models'),2)
        self.assertEqual((destination/'models/reader.pt').read_bytes(),b'example-checkpoint')
        self.assertEqual(restore(path,info,destination,'models'),2)

    def test_corrupted_archive_cannot_create_destination(self):
        path,info=self.archive([('models/reader.pt',b'example')]);info['sha256']='0'*64
        destination=self.root/'project'
        with self.assertRaisesRegex(ValueError,'checksum'):restore(path,info,destination,'models')
        self.assertFalse(destination.exists())

    def test_path_traversal_is_rejected(self):
        path,info=self.archive([('models/../../outside.bin',b'bad')])
        with self.assertRaisesRegex(ValueError,'Unsafe archive member'):restore(path,info,self.root/'project','models')
        self.assertFalse((self.root/'outside.bin').exists())

    def test_conflict_does_not_partially_restore(self):
        path,info=self.archive([('models/new.pt',b'new'),('models/reader.pt',b'replacement')])
        destination=self.root/'project';(destination/'models').mkdir(parents=True)
        existing=destination/'models/reader.pt';existing.write_bytes(b'local-version')
        with self.assertRaises(FileExistsError):restore(path,info,destination,'models')
        self.assertEqual(existing.read_bytes(),b'local-version');self.assertFalse((destination/'models/new.pt').exists())

    def test_symlink_members_are_rejected(self):
        entry=zipfile.ZipInfo('models/linked.pt');entry.create_system=3
        entry.external_attr=(stat.S_IFLNK|0o777)<<16
        path,info=self.archive([(entry,b'../../outside')])
        with self.assertRaisesRegex(ValueError,'Unsafe archive member'):restore(path,info,self.root/'project','models')

    def test_verified_parts_reassemble_exact_archive(self):
        raw=b'first-part-and-second-part'
        parts=[]
        for number,chunk in enumerate((raw[:10],raw[10:]),1):
            path=self.root/f'bundle.zip.{number:03d}';path.write_bytes(chunk)
            parts.append({'name':path.name,'bytes':len(chunk),'sha256':hashlib.sha256(chunk).hexdigest()})
        info={'name':'bundle.zip','bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),'parts':parts}
        self.assertEqual(assemble(info,self.root).read_bytes(),raw)

    def test_bad_part_does_not_create_joined_archive(self):
        path=self.root/'bundle.zip.001';path.write_bytes(b'corrupt')
        info={'name':'bundle.zip','bytes':7,'sha256':'0'*64,'parts':[{'name':path.name,'bytes':7,'sha256':'0'*64}]}
        with self.assertRaisesRegex(ValueError,'checksum'):assemble(info,self.root)
        self.assertFalse((self.root/'bundle.zip').exists())


if __name__=='__main__':unittest.main()
