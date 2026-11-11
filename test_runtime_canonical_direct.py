"""Do not pay cache-key/walk cost for the observed miss-only one-hour fits."""
import hashlib,json,math,unittest
from unittest.mock import Mock,patch
import runtime_audit_cli as cli

def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',', ':'),allow_nan=False).encode()).hexdigest()

def examples(hours=1):
    return {key:[{'anchor_ms':1000,'label_close_ms':1000+(hours+1)*3600000,
                 'features':[1.0,-0.001], 'label_up':1,'return_fraction':0.01}]
            for key in ('calibration','train','validation')}

class DirectDigestTests(unittest.TestCase):
    def test_one_hour_fit_uses_original_fresh_hash_without_cache_work(self):
        original=Mock(side_effect=digest); cache=cli._CanonicalBytes(original); value=examples()
        expected=digest(value)
        with patch.object(cli.marshal,'dumps',side_effect=AssertionError('cache key must not run')):
            self.assertEqual(cache.digest(value),expected)
            value['train'][0]['features'][1]=-0.002
            self.assertEqual(cache.digest(value),digest(value))
        self.assertEqual(original.call_count,2)
        self.assertEqual(cache.stats['direct_calls'],2)
        self.assertEqual(cache.stats['sha256_calls'],2)
        self.assertEqual(cache.stats['hits']+cache.stats['misses'],0)
        self.assertEqual(cache.stats['fallback_calls'],0)
        self.assertFalse(cache.entries)

    def test_twenty_four_hour_and_prepared_cache_hits_are_preserved(self):
        for value in (examples(24),{'schema_version':1,'candidates':{'1':{'fit_examples':examples()}}}):
            with self.subTest(value=value):
                cache=cli._CanonicalBytes(digest)
                self.assertEqual(cache.digest(value),digest(value))
                self.assertEqual(cache.digest(value),digest(value))
                self.assertEqual(cache.stats['hits'],1)
                self.assertEqual(cache.stats['direct_calls'],0)
                self.assertEqual(cache.stats['sha256_calls'],2)

    def test_direct_path_keeps_original_cycle_nan_and_exception_semantics(self):
        for kind in ('cycle','nan','custom_failure'):
            value=examples()
            if kind=='cycle':value['train'][0]['features'].append(value)
            if kind=='nan':value['train'][0]['features'].append(float('nan'))
            error=RuntimeError('original digest failure')
            fn=digest if kind!='custom_failure' else Mock(side_effect=error)
            cache=cli._CanonicalBytes(fn)
            with self.subTest(kind=kind):
                with self.assertRaises(Exception) as reference:fn(value)
                with self.assertRaises(type(reference.exception)) as actual:cache.digest(value)
                self.assertEqual(str(actual.exception),str(reference.exception))
                self.assertFalse(cache.entries)

    def test_unknown_or_incomplete_shapes_keep_original_cache_route(self):
        values=[{}, {'train':[]}, examples(2),examples(24)]
        empty=examples();empty['calibration']=[];values.append(empty)
        boolean=examples();boolean['train'][0]['anchor_ms']=True;values.append(boolean)
        mixed=examples();mixed['validation'][0]['label_close_ms']+=3600000;values.append(mixed)
        for value in values:
            with self.subTest(value=value):
                cache=cli._CanonicalBytes(digest)
                self.assertEqual(cache.digest(value),digest(value))
                self.assertEqual(cache.stats['direct_calls'],0)

    def test_cost_classifier_exception_keeps_original_digest_behavior(self):
        value=examples();original=Mock(side_effect=digest);cache=cli._CanonicalBytes(original)
        with patch.object(cli,'_one_hour_fit',side_effect=KeyError('train')):
            self.assertEqual(cache.digest(value),digest(value))
        original.assert_called_once_with(value)
        self.assertEqual(cache.stats['fallback_calls'],1)
        self.assertEqual(cache.stats['sha256_calls'],1)

class LargeMissDigestTests(unittest.TestCase):
    def test_large_nested_change_uses_current_content_without_losing_repeated_hits(self):
        original=Mock(side_effect=digest);cache=cli._CanonicalBytes(original)
        value={'receipt':'A'*(600*1024),'nested':[{'x':1}]}
        expected=digest(value)
        with patch.object(cli,'_json_closed',wraps=cli._json_closed) as walked:
            self.assertEqual(cache.digest(value),expected)
            value['nested'][0]['x']=-0.0
            self.assertEqual(cache.digest(value),digest(value))
            self.assertNotEqual(cache.digest(value),expected)
            self.assertEqual(walked.call_count,2)
        self.assertEqual(original.call_count,0)
        self.assertEqual(cache.stats['sha256_calls'],3)
        self.assertEqual(cache.stats['direct_calls'],0)
        self.assertEqual(cache.stats['hits'],1)
        self.assertEqual(cache.stats['misses'],2)
        self.assertEqual(cache.stats['fallback_calls'],0)
        self.assertTrue(cache.entries)
        self.assertEqual(cache.max_bytes,64*1024*1024)
        self.assertEqual(cache.max_entries,128)

    def test_large_miss_original_errors_are_preserved(self):
        cycle=[];cycle.append(cycle)
        for bad in (float('nan'),float('inf'),cycle,{'mixed':'keys',2:3}):
            value={'receipt':'A'*(600*1024),'bad':bad}
            with self.subTest(bad=type(bad).__name__):
                with self.assertRaises(Exception) as expected:digest(value)
                cache=cli._CanonicalBytes(digest)
                with self.assertRaises(type(expected.exception)) as actual:cache.digest(value)
                self.assertEqual(str(actual.exception),str(expected.exception))
                self.assertFalse(cache.entries)

    def test_large_prepared_reuses_one_canonical_encoding_with_two_fresh_hashes(self):
        value={'schema_version':1,'candidates':{'1':{'fit_examples':examples(),
            'receipt':'A'*(600*1024)},'24':{'fit_examples':examples(24)}}}
        self.assertGreater(len(cli.marshal.dumps(value,2)),512*1024)
        expected=digest(value);sha=hashlib.sha256;encode=json.dumps
        cache=cli._CanonicalBytes(digest)
        with patch.object(cli.hashlib,'sha256',wraps=sha) as hashes,patch.object(cli.json,'dumps',wraps=encode) as encoded:
            self.assertEqual(cache.digest(value),expected)
            self.assertEqual(cache.digest(value),expected)
        self.assertEqual(hashes.call_count,2)
        self.assertEqual(encoded.call_count,1)
        self.assertEqual(cache.stats['hits'],1)
        self.assertEqual(cache.stats['misses'],1)
        self.assertEqual(cache.stats['sha256_calls'],2)
        self.assertEqual(cache.stats['direct_calls'],0)
        self.assertEqual(len(cache.entries),1)
        self.assertTrue(all(type(k) is bytes and type(v) is bytes for k,v in cache.entries.items()))

    def test_large_value_keeps_small_hits_fresh_hashes_and_original_byte_bounds(self):
        for limits in ({},{'max_bytes':4096,'max_entries':2}):
            with self.subTest(limits=limits):
                cache=cli._CanonicalBytes(digest,**limits)
                large={'receipt':'A'*(600*1024)};small={'receipt':[1,False,None]}
                for value in (small,large,small):self.assertEqual(cache.digest(value),digest(value))
                self.assertEqual(cache.stats['hits'],1)
                self.assertEqual(cache.stats['misses'],1 if limits else 2)
                self.assertEqual(cache.stats['direct_calls'],0)
                self.assertEqual(cache.stats['sha256_calls'],3)
                self.assertEqual(cache.stats['fallback_calls'],1 if limits else 0)
                self.assertLessEqual(cache.stats['cached_bytes'],cache.max_bytes)
                self.assertLessEqual(cache.stats['entries'],cache.max_entries)

if __name__=='__main__':unittest.main()
