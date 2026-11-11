"""Incomplete blocks retain durable progress and never advance partial coverage."""
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import doge_whale_blocks as scanner
from test_whale_block_integrity import Clock, Provider, fixture_directory, transaction


class ResumeTests(unittest.TestCase):
    def test_first_block_keeps_original_boundary_when_tip_advances(self):
        txids = ['a' * 64, 'b' * 64]
        with fixture_directory() as root:
            (root/'checkpoint.json').write_text('{"last_completed_height":10}')
            first = Provider(txids=txids)
            first.tx_overrides[txids[1]] = TimeoutError('stop')
            with patch.object(scanner, 'OUT', root), patch.object(scanner, 'MAX_BLOCKS', 1):
                with patch.object(scanner, 'request', side_effect=first):
                    self.assertEqual(scanner.main(), 1)
                resumed = Provider(txids=txids, tips=[18, 18])
                resumed.tx_overrides[txids[1]] = transaction(txids[1], resumed.parent, '1')
                with patch.object(scanner, 'request', side_effect=resumed):
                    self.assertEqual(scanner.main(), 0)
                self.assertEqual(json.loads((root/'checkpoint.json').read_text())['last_completed_height'], 11)
                self.assertEqual(list(scanner.read_events()), [txids[0]])

    def test_time_budget_resumes_without_publishing_incomplete_block(self):
        txids = ['d' * 64, 'e' * 64]
        with fixture_directory() as root:
            (root/'checkpoint.json').write_text('{"last_completed_height":10}')
            clock = Clock()
            provider = Provider(txids=txids)

            def consume_budget(path, **kwargs):
                data = provider(path, **kwargs)
                if path == '/tx/' + txids[0]:
                    clock.now += 481
                return data

            with patch.object(scanner, 'OUT', root), patch.object(scanner, 'MAX_BLOCKS', 1), \
                 patch.object(scanner, 'RUN_SECONDS', 600), \
                 patch.object(scanner.time, 'monotonic', clock.monotonic):
                with patch.object(scanner, 'request', side_effect=consume_budget):
                    self.assertEqual(scanner.main(), 0)
                self.assertEqual(json.loads((root/'checkpoint.json').read_text())['last_completed_height'], 10)
                self.assertEqual(scanner.read_events(), {})
                resumed = Provider(txids=txids)
                resumed.tx_overrides[txids[1]] = transaction(txids[1], resumed.parent, '1')
                with patch.object(scanner, 'request', side_effect=resumed):
                    self.assertEqual(scanner.main(), 0)
                self.assertEqual([path for path, _ in resumed.calls if path.startswith('/tx/')], ['/tx/' + txids[1]])
                self.assertEqual(list(scanner.read_events()), [txids[0]])
                self.assertFalse((root/'pending_block.json').exists())

    def test_api_failure_preserves_verified_transactions_until_block_complete(self):
        txids = ['a' * 64, 'b' * 64, 'c' * 64]
        with fixture_directory() as root:
            checkpoint = {'last_completed_height': 10}
            (root/'checkpoint.json').write_text(json.dumps(checkpoint))
            provider = Provider(txids=txids)
            provider.tx_overrides[txids[1]] = TimeoutError('provider interrupted')
            with patch.object(scanner, 'OUT', root), patch.object(scanner, 'MAX_BLOCKS', 1):
                with patch.object(scanner, 'request', side_effect=provider):
                    self.assertEqual(scanner.main(), 1)
                self.assertEqual(json.loads((root/'checkpoint.json').read_text()), checkpoint)
                self.assertEqual(scanner.read_events(), {})
                resumed = Provider(txids=txids)
                for txid in txids[1:]:
                    resumed.tx_overrides[txid] = transaction(txid, resumed.parent, '1')
                with patch.object(scanner, 'request', side_effect=resumed):
                    self.assertEqual(scanner.main(), 0)
                self.assertEqual([path for path, _ in resumed.calls if path.startswith('/tx/')],
                                 ['/tx/' + txid for txid in txids[1:]])
                self.assertEqual(json.loads((root/'checkpoint.json').read_text())['last_completed_height'], 11)
                self.assertEqual(list(scanner.read_events()), [txids[0]])

    def test_changed_block_transaction_list_preserves_pending_and_blocks_coverage(self):
        # The old silent cache reset is deliberately replaced by the user's
        # stricter preservation rule; its previous RED evidence remains saved.
        first, replacement = 'a' * 64, 'b' * 64
        with fixture_directory() as root:
            checkpoint = {'last_completed_height': 10}
            (root/'checkpoint.json').write_text(json.dumps(checkpoint))
            provider = Provider(txids=[first, replacement])
            provider.tx_overrides[replacement] = TimeoutError('stop')
            with patch.object(scanner, 'OUT', root), patch.object(scanner, 'MAX_BLOCKS', 1):
                with patch.object(scanner, 'request', side_effect=provider):
                    self.assertEqual(scanner.main(), 1)
                saved = (root/'pending_block.json').read_bytes()
                with patch.object(scanner, 'request', side_effect=Provider(txids=[replacement])):
                    self.assertEqual(scanner.main(), 1)
                self.assertEqual((root/'pending_block.json').read_bytes(), saved)
                self.assertEqual(json.loads((root/'checkpoint.json').read_text()), checkpoint)
                self.assertEqual(scanner.read_events(), {})


if __name__ == '__main__':
    unittest.main()

