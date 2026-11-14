"""Regression checks for the retained full-pointer workflow."""
import tempfile
import io
import json
import unittest
import zipfile
from pathlib import Path
import numpy as np
import torch

from watermeter.data import DATA,load_manifest,read_image,image_path,pack_regions,value_digits,read_json,recover_images
from watermeter.model import load_reader,ReadingNetwork
from watermeter.pipeline import load_selection
from watermeter.evaluation import summarize
from watermeter.training import PackedDataset,move_batch,reader_loss
from watermeter.runtime import ROOT
from watermeter.external import validate_collection


class CurrentPipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        required=(DATA/'manifest.json',DATA/'cache/gt_patches.npy',DATA/'cache/pred_patches.npy',
                  ROOT/'models/selection.json',ROOT/'models/reader.pt',ROOT/'models/detector.pt')
        if not all(p.is_file() for p in required):
            raise unittest.SkipTest('Local research assets are not bundled; see data/README.md and models/README.md')
        torch.set_num_threads(2)
        cls.manifest=load_manifest();cls.selection=load_selection()

    def test_fixed_counts_and_no_group_leakage(self):
        splits=self.manifest['splits'];self.assertEqual({k:len(v) for k,v in splits.items()},{'train':2601,'val':607,'test':572})
        owners={};seen=set()
        for split,indices in splits.items():
            for i in indices:
                self.assertNotIn(i,seen);seen.add(i)
                self.assertEqual(owners.setdefault(self.manifest['records'][i]['group'],split),split)
        self.assertEqual(len(seen),3780)

    def test_decimal_readings_are_exact(self):
        self.assertEqual(value_digits('05617.812'),[0,0,5,6,1,7,8,1,2,0])
        self.assertEqual(value_digits('0.0001')[-1],1)
        for value in ('-1','1000000','1.00001'):
            with self.assertRaises(ValueError):value_digits(value)

    def test_migrated_gt_geometry_and_pixels(self):
        indices=[self.manifest['splits'][s][0] for s in ('train','val','test')]
        caches=[np.load(DATA/f'cache/{name}.npy',mmap_mode='r') for name in ('gt_patches','gt_masks','gt_states')]
        for i in indices:
            r=self.manifest['records'][i];packed=pack_regions(read_image(image_path(r)),r['regions'])
            for actual,cache in zip(packed,caches):np.testing.assert_array_equal(actual,cache[i])

    def test_missing_predictions_count_as_errors(self):
        rows=[{'target_scaled':123456,'p':{'scaled_value':123456}}, {'target_scaled':123456}]
        metrics=summarize(rows,'p')
        self.assertEqual(metrics['coverage'],.5);self.assertEqual(metrics['integer_exact'],.5)
        self.assertEqual(metrics['full_exact'],.5)

    def test_integer_and_fractional_accuracy_differ(self):
        metrics=summarize([{'target_scaled':123456,'p':{'scaled_value':123457}}],'p')
        self.assertEqual(metrics['integer_exact'],1);self.assertEqual(metrics['full_exact'],0)
        self.assertAlmostEqual(metrics['mae_m3_on_returned'],.0001)

    def test_no_wheel_model_branch(self):
        model,checkpoint=load_reader(self.selection['reader'])
        self.assertEqual(sum(p.numel() for p in model.parameters()),953194)
        self.assertFalse(any('wheel' in key for key in model.state_dict()))
        self.assertEqual(checkpoint['manifest_sha256'],self.selection['manifest_sha256'])
        with self.assertRaises(ValueError):ReadingNetwork(pointer_only=False)

    def test_real_training_step_has_finite_gradients(self):
        indices=self.manifest['splits']['train'][:2];dataset=PackedDataset(DATA,indices)
        batch={k:torch.stack([dataset[i][k] for i in range(2)]) for k in dataset[0]}
        batch=move_batch(batch,torch.device('cpu'),augment=True)
        model,_=load_reader(self.selection['reader']);model.train()
        loss=reader_loss(model(batch['patches'],batch['mask']),batch);loss.backward()
        self.assertTrue(torch.isfinite(loss));self.assertTrue(torch.isfinite(model.correction.weight.grad).all())
        self.assertGreater(model.pointer_stem.layers[0].weight.grad.abs().sum().item(),0)

    def test_prediction_cache_excludes_test(self):
        masks=np.load(DATA/'cache/pred_masks.npy',mmap_mode='r')
        self.assertFalse(masks[self.manifest['splits']['test']].any())
        meta=read_json(DATA/'cache/pred_metadata.json')
        self.assertFalse(meta['test_processed'])

    def test_raw_archive_restores_to_new_image_directory(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.runtime') as folder:
            directory=Path(folder);record=dict(self.manifest['records'][0]);record['name']='restored.jpg'
            image=image_path(self.manifest['records'][0]).read_bytes()
            manifest={'scope':'full_pointer','records':[record]}
            (directory/'manifest.json').write_text(json.dumps(manifest),encoding='utf-8')
            import hashlib
            (directory/'manifest.sha256').write_text(hashlib.sha256((directory/'manifest.json').read_bytes()).hexdigest())
            inner=io.BytesIO()
            with zipfile.ZipFile(inner,'w') as archive:archive.writestr('WMeter5K/images/restored.jpg',image)
            (directory/'raw').mkdir()
            with zipfile.ZipFile(directory/'raw/WMeter5K_source.zip','w') as archive:archive.writestr('WMeter5K.zip',inner.getvalue())
            self.assertEqual(recover_images(directory),1)
            self.assertEqual((directory/'images/restored.jpg').read_bytes(),image)
            self.assertEqual(recover_images(directory),0)

    def test_empty_independent_collection_does_not_produce_accuracy(self):
        with self.assertRaisesRegex(ValueError,'No independent photographs'):validate_collection(DATA/'collection/independent.json')

    def test_independent_collection_rejects_previously_seen_image(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.runtime') as folder:
            path=Path(folder)/'collection.json'
            collection={'unseen_physical_meters_confirmed':True,'frozen_before_evaluation':True,'confirmed_by':'collector',
                        'samples':[{'image':str(image_path(self.manifest['records'][0])),'meter_id':'m1',
                                    'reading':'0','verified_by':['a','b']}]}
            path.write_text(json.dumps(collection),encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'previously exposed'):validate_collection(path)

    def test_test_report_cannot_choose_model(self):
        from scripts.select_model import select
        with tempfile.TemporaryDirectory(dir=ROOT/'.runtime') as folder:
            with self.assertRaisesRegex(ValueError,'validation reports only'):
                select([ROOT/'reports/historical_test/report.json'],Path(folder)/'selection.json')

    def test_yolo_training_lists_exclude_test(self):
        from train_detector import prepare_detector_data
        # A tiny temporary view verifies list/label generation without modifying the real dataset.
        with tempfile.TemporaryDirectory(dir=ROOT/'.runtime') as folder:
            directory=Path(folder);(directory/'images').mkdir()
            records=[]
            for split in ('train','val','test'):
                record=dict(self.manifest['records'][self.manifest['splits'][split][0]])
                (directory/'images'/record['name']).write_bytes(image_path(record).read_bytes());records.append(record)
            manifest={'scope':'full_pointer','records':records,'splits':{'train':[0],'val':[1],'test':[2]}}
            raw=json.dumps(manifest);(directory/'manifest.json').write_text(raw)
            import hashlib
            (directory/'manifest.sha256').write_text(hashlib.sha256(raw.encode()).hexdigest())
            config=prepare_detector_data(directory)
            import yaml
            settings=yaml.safe_load(config.read_text());self.assertNotIn('test',settings)
            self.assertNotIn(records[2]['name'],(directory/'detector/train.txt').read_text())
            self.assertNotIn(records[2]['name'],(directory/'detector/val.txt').read_text())
            label=(directory/'labels'/Path(records[0]['name']).with_suffix('.txt')).read_text().splitlines()[0]
            self.assertEqual(len(label.split()),17)


if __name__=='__main__':unittest.main()
