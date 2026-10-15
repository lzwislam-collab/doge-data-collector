"""Incomplete blocks must make durable progress without advancing coverage."""
import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import doge_whale_blocks as scanner


class ResumeTests(unittest.TestCase):
    def test_first_block_keeps_original_boundary_when_tip_advances(self):
        txids = ['a' * 64, 'b' * 64]
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            with patch.object(scanner,'OUT',root), patch.object(scanner,'MAX_BLOCKS',1), patch.object(scanner,'parse_block',return_value=txids):
                with patch.object(scanner,'get_tip',return_value=17), patch.object(scanner,'explain',side_effect=[(Decimal('2000000'),'large'),TimeoutError('stop')]):
                    self.assertEqual(scanner.main(),1)
                with patch.object(scanner,'get_tip',return_value=18), patch.object(scanner,'explain',return_value=(Decimal('1'),'small')):
                    self.assertEqual(scanner.main(),0)
                self.assertEqual(json.loads((root/'checkpoint.json').read_text())['last_completed_height'],11)
                self.assertEqual(list(scanner.read_events()),[txids[0]])

    def test_time_budget_resumes_without_publishing_incomplete_block(self):
        txids = ['d' * 64, 'e' * 64]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'checkpoint.json').write_text('{"last_completed_height":10}')
            with patch.object(scanner, 'OUT', root), patch.object(scanner, 'MAX_BLOCKS', 1), patch.object(scanner, 'RUN_SECONDS', 600), patch.object(scanner, 'get_tip', return_value=17), patch.object(scanner, 'parse_block', return_value=txids):
                with patch.object(scanner.time, 'monotonic', side_effect=[0, 0, 0, 481, 481]), patch.object(scanner, 'explain', return_value=(Decimal('2000000'), 'large')):
                    self.assertEqual(scanner.main(), 0)
                self.assertEqual(json.loads((root / 'checkpoint.json').read_text())['last_completed_height'], 10)
                self.assertEqual(scanner.read_events(), {})
                with patch.object(scanner, 'explain', return_value=(Decimal('1'), 'small')) as provider:
                    self.assertEqual(scanner.main(), 0)
                self.assertEqual(provider.call_args_list[0].args, (txids[1],))
                self.assertEqual(provider.call_count, 1)
                self.assertEqual(list(scanner.read_events()), [txids[0]])
                self.assertFalse((root / 'pending_block.json').exists())

    def test_api_failure_preserves_verified_transactions_until_block_complete(self):
        txids = ['a' * 64, 'b' * 64, 'c' * 64]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            checkpoint = {'last_completed_height': 10}
            (root / 'checkpoint.json').write_text(json.dumps(checkpoint))
            calls = []

            def interrupted(txid):
                calls.append(txid)
                if txid == txids[1]:
                    raise TimeoutError('provider interrupted')
                return Decimal('2000000'), 'observed transfer'

            with patch.object(scanner, 'OUT', root), patch.object(scanner, 'MAX_BLOCKS', 1), patch.object(scanner, 'get_tip', return_value=17), patch.object(scanner, 'parse_block', return_value=txids):
                with patch.object(scanner, 'explain', side_effect=interrupted):
                    self.assertEqual(scanner.main(), 1)
                self.assertEqual(json.loads((root / 'checkpoint.json').read_text()), checkpoint)
                self.assertEqual(scanner.read_events(), {})
                calls.clear()

                def resume(txid):
                    calls.append(txid)
                    return Decimal('1'), 'small transfer'

                with patch.object(scanner, 'explain', side_effect=resume):
                    self.assertEqual(scanner.main(), 0)
                self.assertEqual(calls, txids[1:])
                self.assertEqual(json.loads((root / 'checkpoint.json').read_text())['last_completed_height'], 11)
                self.assertEqual(list(scanner.read_events()), [txids[0]])

    def test_changed_block_transaction_list_invalidates_pending_progress(self):
        first, replacement = 'a' * 64, 'b' * 64
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'checkpoint.json').write_text('{"last_completed_height":10}')
            with patch.object(scanner, 'OUT', root), patch.object(scanner, 'MAX_BLOCKS', 1), patch.object(scanner, 'get_tip', return_value=17):
                with patch.object(scanner, 'parse_block', return_value=[first, replacement]), patch.object(scanner, 'explain', side_effect=[(Decimal('2000000'), 'old'), TimeoutError('stop')]):
                    self.assertEqual(scanner.main(), 1)
                with patch.object(scanner, 'parse_block', return_value=[replacement]), patch.object(scanner, 'explain', return_value=(Decimal('3000000'), 'new')):
                    self.assertEqual(scanner.main(), 0)
                self.assertEqual(list(scanner.read_events()), [replacement])


if __name__ == '__main__':
    unittest.main()

