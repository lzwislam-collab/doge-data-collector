import unittest
from unittest.mock import patch

class Publication(unittest.TestCase):
    def module(self):
        import collector_publication_v2 as m
        return m

    def test_nonfastforward_rebases_without_force_then_retries(self):
        m=self.module();calls=[];pushes=0
        def call(*args):
            nonlocal pushes
            calls.append(args)
            if args[0]=='push':pushes+=1;return 1 if pushes==1 else 0
            return 0
        self.assertTrue(m.publish(call));self.assertIn(('pull','--rebase','origin','main'),calls)
        self.assertFalse(any('--force' in args for args in calls))

    def test_conflict_aborts_and_preserves_original_commit(self):
        m=self.module();calls=[]
        def call(*args):
            calls.append(args);return 1 if args[0] in ('push','pull') else 0
        self.assertFalse(m.publish(call));self.assertIn(('rebase','--abort'),calls)
        self.assertFalse(any(args[0] in ('reset','clean') for args in calls))

    def test_protected_paths_cannot_be_staged_by_collector(self):
        m=self.module()
        self.assertTrue(m.allowed('data/derivatives_pit_v1/raw/'+'a'*64+'.json'))
        self.assertFalse(m.allowed('learned_shadow_v1.py'))
        self.assertFalse(m.allowed('data/stage5/forward_validation_protocol_v2.json'))
