import copy
import unittest
import phase2_recent as recent

H=3600000

def frame(n=4300):
    times=[i*H for i in range(n)]
    return {'times':times,**{s:{'close':[100+(i%19)*.1+i*.002 for i in range(n)],
        'volume':[10+i%7 for i in range(n)],'seen':[(i+1)*H+1000 for i in range(n)]}
        for s in ('DOGE-USDT','BTC-USDT','ETH-USDT')}}

class RecentTests(unittest.TestCase):
    def test_repeat_and_mature_labels(self):
        f=frame(); now=4300*H+2000
        a=recent.train(f,now);b=recent.train(f,now)
        self.assertEqual(a,b)
        self.assertEqual(a['training_n'],4096)
        self.assertLessEqual(a['latest_label_available_ms'],now)
        self.assertFalse(a['validated_70pct'])
        self.assertFalse(a['auto_issuance_authorized'])

    def test_future_receipt_cannot_be_used(self):
        f=frame();now=4300*H+2000
        f['DOGE-USDT']['seen'][-1]=now+1
        with self.assertRaises(ValueError):recent.train(f,now)

    def test_missing_asset_or_gap_rejected(self):
        for mutate in (lambda f:f.pop('ETH-USDT'),lambda f:f['times'].__setitem__(100,101*H)):
            f=frame();mutate(f)
            with self.assertRaises(ValueError):recent.train(f,4300*H+2000)

    def test_age_starts_from_label_cutoff_not_fit_clock(self):
        a=recent.train(frame(),4300*H+2000)
        cutoff=a['latest_label_end_ms']
        recent.check_age(a,cutoff+14*24*H)
        with self.assertRaises(ValueError):recent.check_age(a,cutoff+14*24*H+1)
        with self.assertRaises(ValueError):recent.check_age(a,a['trained_at_ms']-1)

    def test_no_false_historical_pit_claim(self):
        a=recent.train(frame(),4300*H+2000)
        self.assertFalse(a['historical_pit_validated'])
        self.assertEqual(a['columns'],list(range(5,17)))
        self.assertEqual(a['setting'],{'model':'shrinkage_lda','regularization':.3})

    def test_explicit_feature_numeric_contract(self):
        a=recent.train(frame(),4300*H+2000)
        self.assertEqual(a['feature_decimal_places'],9)

if __name__=='__main__':unittest.main()
