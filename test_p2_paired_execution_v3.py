import unittest
from datetime import datetime,timedelta,timezone
import p2_paired_execution_v3 as p


class PaperTests(unittest.TestCase):
    def setUp(self):self.t=datetime(2026,10,13,tzinfo=timezone.utc)

    def item(self,fid='a',action='PAPER_LONG',offset=0,h=1):
        issued=self.t+timedelta(hours=offset)
        return {'forecast_id':fid,'horizon_hours':h,'issued_at_utc':issued.isoformat(),
            'target_utc':(issued+timedelta(hours=h)).isoformat(),
            'observed_public_utc':(issued+timedelta(seconds=5)).isoformat(),
            'paper_action':action,'source_verified':True,'pair_sha256':'a'*64}

    def book(self,minutes,price=100,bid=None,depth=100000):
        at=self.t+timedelta(minutes=minutes)
        return {'requested_at_utc':(at-timedelta(seconds=1)).isoformat(),'captured_at_utc':at.isoformat(),
            'quote':{'quote_utc':at.isoformat(),'spread_bps':0 if bid is None else (price-bid)/((price+bid)/2)*10000},
            'asks':[[price,depth]],'bids':[[price if bid is None else bid,depth]],'book_sha256':str(minutes)}

    def replay(self,items,books,minutes):return p.replay(items,books,self.t+timedelta(minutes=minutes))

    def test_fee_slippage_and_spread_included_not_last_price(self):
        result=p.buy([[101,1000]],1000,.001,.0005,0)
        expected=1000/(101*1.001*1.0005)
        self.assertAlmostEqual(result['quantity'],expected)
        self.assertAlmostEqual(result['gross_usdt']+sum(result['costs'].values()),1000)
        out=p.sell([[99,1000]],result['quantity'],.001,.0005,0)
        self.assertAlmostEqual(out['cash_usdt'],expected*99*.999*.9995)
        self.assertLess(out['cash_usdt'],1000)

    def test_long_interval_benchmarks_and_stress(self):
        r=self.replay([self.item()],[self.book(1),self.book(61,110)],86)['1h']
        self.assertEqual(r['completed_prefix_intervals'],1)
        self.assertGreater(r['strategy']['prefix_net_return_pct'],0)
        self.assertAlmostEqual(r['strategy']['prefix_net_return_pct'],r['hold']['prefix_net_return_pct'])
        self.assertLess(r['double_cost']['prefix_net_return_pct'],r['strategy']['prefix_net_return_pct'])
        self.assertFalse(r['real_profit_validated'])

    def test_no_trade_keeps_cash_but_hold_still_compared(self):
        r=self.replay([self.item(action='NO_TRADE')],[self.book(1),self.book(61,90)],86)['1h']
        self.assertEqual(r['completed_prefix_intervals'],1)
        self.assertEqual(r['strategy']['prefix_net_return_pct'],0)
        self.assertLess(r['hold']['prefix_net_return_pct'],0)
        self.assertEqual(r['cash_net_return_pct'],0)

    def test_missing_first_exit_blocks_later_cherry_picking(self):
        items=[self.item('a'),self.item('b',offset=2)]
        r=self.replay(items,[self.book(1),self.book(121),self.book(181,120)],206)['1h']
        self.assertEqual(r['completed_prefix_intervals'],0)
        self.assertEqual(r['blocked_forecast_id'],'a')
        self.assertIsNone(r['strategy']['prefix_net_return_pct'])

    def test_late_entry_cannot_be_reconstructed(self):
        r=self.replay([self.item()],[self.book(12)],86)['1h']
        self.assertEqual(r['blocked_forecast_id'],'a')
        self.assertIn('ENTRY',r['blocked_reason'])

    def test_quote_before_publication_cannot_be_entry(self):
        b=self.book(0)
        r=self.replay([self.item()],[b],1)['1h']
        self.assertEqual(r['completed_prefix_intervals'],0)
        self.assertFalse(r['hold']['started'])

    def test_insufficient_depth_not_invented_fill(self):
        with self.assertRaises(ValueError):p.buy([[100,1]],1000,.001,.0005,0)
        r=self.replay([self.item()],[self.book(1,depth=1)],12)['1h']
        self.assertEqual(r['blocked_forecast_id'],'a')

    def test_reservation_independent_of_decision_and_missing_source(self):
        x=self.item('a','NO_TRADE');x['source_verified']=False
        y=self.item('b',offset=.5);z=self.item('c',offset=2)
        self.assertEqual([i['forecast_id'] for i in p.reserve([z,y,x])],['a','c'])

    def test_hold_continues_through_cash_gap_no_reentry_fees(self):
        items=[self.item('a','NO_TRADE'),self.item('b','NO_TRADE',offset=2)]
        r=self.replay(items,[self.book(1),self.book(61,90),self.book(121,100),self.book(181,110)],206)['1h']
        self.assertEqual(r['completed_prefix_intervals'],2)
        self.assertEqual(r['hold']['buy_legs'],1)
        self.assertEqual(r['strategy']['buy_legs'],0)
        self.assertAlmostEqual(r['hold']['prefix_net_return_pct'],(110*.999*.9995/(100*1.001*1.0005)-1)*100)

    def test_path_drawdown_is_sampled_and_gap_explicit(self):
        r=self.replay([self.item()],[self.book(1),self.book(20,80),self.book(61,110)],86)['1h']
        self.assertGreater(r['strategy']['sampled_drawdown_pct'],19)
        self.assertGreater(r['path_gap_count'],0)
        self.assertFalse(r['continuous_drawdown_verified'])

    def test_horizons_never_compound_together(self):
        r=self.replay([self.item(),self.item('daily',h=24)],[self.book(1),self.book(61,110)],86)
        self.assertEqual(r['1h']['completed_prefix_intervals'],1)
        self.assertEqual(r['24h']['completed_prefix_intervals'],0)

    def test_later_verification_cannot_backfill_an_earlier_entry_book(self):
        first=self.book(1);first['eligible_sources']={}
        later=self.book(12);later['eligible_sources']={'a':self.item()}
        r=self.replay([self.item()],[first,later],86)['1h']
        self.assertFalse(r['hold']['started']);self.assertEqual(r['blocked_forecast_id'],'a')

    def test_negative_or_nonfinite_inputs_are_rejected(self):
        for value in (-1,float('nan'),float('inf')):
            with self.assertRaises(ValueError):p.buy([[100,1000]],value,.001,.0005,0)
        with self.assertRaises(ValueError):p.buy([[100,-1],[101,1000]],1000,.001,.0005,0)

    def test_exit_after_grace_is_missing_even_if_price_is_profitable(self):
        r=self.replay([self.item()],[self.book(1),self.book(86,200)],86)['1h']
        self.assertEqual(r['completed_prefix_intervals'],0);self.assertEqual(r['blocked_reason'],'MISSING_EXIT_BOOK_IN_WINDOW')

    def test_wide_spread_and_missing_source_do_not_execute(self):
        b=self.book(1,price=100,bid=99)
        r=self.replay([self.item()],[b],12)['1h']
        self.assertFalse(r['hold']['started'])
        i=self.item();i['source_verified']=False
        r=self.replay([i],[self.book(1)],12)['1h']
        self.assertFalse(r['hold']['started'])

    def test_nonmonotonic_book_replay_is_blocked(self):
        with self.assertRaisesRegex(ValueError,'nonmonotonic'):self.replay([self.item()],[self.book(1),self.book(1)],2)

    def test_live_lot_rounding_leaves_cash_and_no_fake_dust_exit(self):
        entry=p.buy([[101,1000]],1000,.001,.0005,0,'1','1')
        self.assertEqual(entry['quantity'],9)
        self.assertLess(entry['cash_spent_usdt'],1000)
        with self.assertRaisesRegex(ValueError,'lot'):p.sell([[99,1000]],9.5,.001,.0005,0,'1','1')

    def test_multilevel_lot_quantity_remains_sellable(self):
        entry=p.buy([[.1,642.6520685695132],[.1001,100000]],1000,.001,.0005,0,'0.1','1')
        exit_=p.sell([[.1,100000]],entry['quantity'],.001,.0005,0,'0.1','1')
        self.assertGreater(exit_['cash_usdt'],0)
        self.assertEqual(entry['quantity'],9975.6)

    def test_hold_drawdown_is_marked_while_waiting_for_next_interval(self):
        items=[self.item('a','NO_TRADE'),self.item('b','NO_TRADE',offset=2)]
        books=[self.book(m,50 if m==80 else 100) for m in (1,20,40,61,80,100,121,140,160,181)]
        r=self.replay(items,books,206)['1h']
        self.assertGreater(r['hold']['sampled_drawdown_pct'],49)
        self.assertEqual(r['missing_mark_count'],0)


if __name__=='__main__':unittest.main()
