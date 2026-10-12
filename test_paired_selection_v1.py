import math
import unittest
import paired_selection_v1 as s


class SelectionTests(unittest.TestCase):
    def meta(self):
        return {'calibration_n':150,'validation_n':150,'validation_selected_n':40,
                'validation_brier':.20,'selected_return_lower_proxy':.01,
                'doge_return_24h':.01,'btc_return_24h':.01}

    def book(self):
        return {'spread_bps':2,'age_seconds':1,'bid_depth_notional_usdt':1000,'ask_depth_notional_usdt':1000}

    def test_selective_paper_long_and_abstention_both_possible(self):
        self.assertEqual(s.select(.75,self.meta(),self.book())['paper_action'],'PAPER_LONG')
        self.assertEqual(s.select(.52,self.meta(),self.book())['paper_action'],'NO_TRADE')

    def test_down_never_shorts(self):
        self.assertEqual(s.select(.2,self.meta(),self.book())['paper_action'],'NO_TRADE')

    def test_cost_edge_and_calibration_required(self):
        for key,value in [('validation_brier',.26),('selected_return_lower_proxy',.001),('calibration_n',50),('validation_selected_n',5)]:
            m=self.meta();m[key]=value
            self.assertEqual(s.select(.75,m,self.book())['paper_action'],'NO_TRADE')

    def test_invalid_depth_spread_and_stress_abstain(self):
        for key,value in [('spread_bps',30),('age_seconds',40),('ask_depth_notional_usdt',1)]:
            b=self.book();b[key]=value
            self.assertEqual(s.select(.75,self.meta(),b)['paper_action'],'NO_TRADE')
        m=self.meta();m['btc_return_24h']=.2
        self.assertEqual(s.select(.75,m,self.book())['paper_action'],'NO_TRADE')

    def test_missing_book_is_explicit_abstention(self):
        r=s.select(.75,self.meta(),None)
        self.assertIn('book_unverified',r['reasons'])
        self.assertFalse(r['real_orders_authorized'])

    def test_nan_probability_rejected(self):
        with self.assertRaises(ValueError): s.select(math.nan,self.meta(),self.book())

    def test_purge_separates_train_calibration_validation(self):
        split=s.split_anchors(list(range(24,1000)),24)
        self.assertLess(max(split['train'])+24,min(split['calibration']))
        self.assertLess(max(split['calibration'])+24,min(split['validation']))

    def test_zero_variance_return_is_not_accepted_as_certainty(self):
        self.assertIsNone(s.return_lower([.01]*100))

    def test_fit_is_causal_and_fixed_for_unchanged_data(self):
        times=[i*3600000 for i in range(1200)]
        prices={'DOGE-USDT':[.1*math.exp(.01*math.sin(i/9)) for i in range(1200)],
                'BTC-USDT':[100*math.exp(.01*math.sin(i/13)) for i in range(1200)]}
        a=s.fit_candidate(times,prices,1)
        b=s.fit_candidate(times,prices,1)
        self.assertEqual(a,b)
        self.assertLessEqual(a['training_last_label_ms'],times[-1]+3600000)
        self.assertTrue(0<=a['p_up']<=1)


if __name__=='__main__':unittest.main()
