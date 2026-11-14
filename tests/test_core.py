"""Source-only checks: no water-meter photographs, annotations or weights needed."""
import unittest
import numpy as np
import torch

from watermeter.data import value_digits,rectify,pack_regions,DATA
from watermeter.model import ReadingNetwork
from watermeter.evaluation import summarize
from watermeter.external import validate_collection


class CoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        torch.set_num_threads(2)

    def test_decimal_precision(self):
        self.assertEqual(value_digits('05617.812'),[0,0,5,6,1,7,8,1,2,0])
        self.assertEqual(value_digits('0.0001'),[0,0,0,0,0,0,0,0,0,1])

    def test_invalid_readings(self):
        for text in ('-1','1000000','1.00001'):
            with self.subTest(text=text),self.assertRaises(ValueError):value_digits(text)

    def test_rectification_preserves_order(self):
        grid=np.arange(64*64,dtype=np.uint8).reshape(64,64)
        image=np.repeat(grid[:,:,None],3,axis=2)
        quad=[[63,0],[63,63],[0,63],[0,0]]
        np.testing.assert_array_equal(rectify(image,quad),grid)

    def test_degenerate_region_is_rejected(self):
        with self.assertRaises(ValueError):rectify(np.zeros((64,64,3),np.uint8),[[0,0]]*4)

    def test_pointer_slots_and_wheel_exclusion(self):
        image=np.full((64,64,3),120,np.uint8);quad=[[63,0],[63,63],[0,63],[0,0]]
        patches,mask,states=pack_regions(image,[{'cls':5,'quad':quad,'state':12},{'cls':10,'quad':quad}])
        self.assertEqual(np.flatnonzero(mask).tolist(),[5]);self.assertEqual(states[5],12)
        self.assertEqual(patches.shape,(10,64,64));self.assertTrue((patches[5]==120).all())

    def test_model_architecture(self):
        model=ReadingNetwork()
        self.assertEqual(sum(p.numel() for p in model.parameters()),953194)
        self.assertFalse(any('wheel' in key for key in model.state_dict()))
        with self.assertRaises(ValueError):ReadingNetwork(pointer_only=False)

    def test_masked_pixels_cannot_change_predictions(self):
        torch.manual_seed(42);model=ReadingNetwork().eval()
        mask=torch.zeros((1,10),dtype=torch.bool);mask[0,5]=True
        first=torch.zeros((1,10,64,64));second=first.clone();second[0,0]=1
        with torch.inference_mode():
            a=model(first,mask)['logits'];b=model(second,mask)['logits']
        self.assertEqual(tuple(a.shape),(1,10,10));self.assertTrue(torch.isfinite(a).all())
        torch.testing.assert_close(a,b,rtol=0,atol=0)

    def test_metrics_include_failed_images(self):
        metrics=summarize([{'target_scaled':123456,'p':{'scaled_value':123456}},{'target_scaled':123456}],'p')
        self.assertEqual(metrics['images'],2);self.assertEqual(metrics['integer_exact'],.5)
        self.assertEqual(metrics['full_exact'],.5);self.assertEqual(metrics['coverage'],.5)

    def test_fractional_error_keeps_integer_correct(self):
        metrics=summarize([{'target_scaled':123456,'p':{'scaled_value':123457}}],'p')
        self.assertEqual(metrics['integer_exact'],1);self.assertEqual(metrics['full_exact'],0)
        self.assertAlmostEqual(metrics['mae_m3_on_returned'],.0001)

    def test_empty_collection_has_no_independent_accuracy(self):
        with self.assertRaisesRegex(ValueError,'No independent photographs'):
            validate_collection(DATA/'collection/independent.json')


if __name__=='__main__':unittest.main()
