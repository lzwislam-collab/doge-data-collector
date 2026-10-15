"""Transient provider errors must not discard resumable scanner progress."""
import io
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
import doge_whale_blocks as scanner


class RetryTests(unittest.TestCase):
    def response(self):
        return io.BytesIO(b'{"status":"success","data":{"height":12}}')

    def test_transient_gateway_error_recovers(self):
        error = HTTPError('https://dogechain.com', 502, 'Bad Gateway', {}, None)
        with patch.object(scanner.urllib.request, 'urlopen', side_effect=[error, self.response()]) as http, patch.object(scanner.time, 'sleep'):
            self.assertEqual(scanner.request('/network'), {'height': 12})
            self.assertEqual(http.call_count, 2)

    def test_persistent_gateway_error_is_bounded_and_raised(self):
        error = HTTPError('https://dogechain.com', 503, 'Unavailable', {}, None)
        with patch.object(scanner.urllib.request, 'urlopen', side_effect=error) as http, patch.object(scanner.time, 'sleep'):
            with self.assertRaises(HTTPError):
                scanner.request('/network')
            self.assertEqual(http.call_count, 4)

    def test_permanent_client_error_is_not_retried(self):
        error = HTTPError('https://dogechain.com', 403, 'Forbidden', {}, None)
        with patch.object(scanner.urllib.request, 'urlopen', side_effect=error) as http, patch.object(scanner.time, 'sleep'):
            with self.assertRaises(HTTPError):
                scanner.request('/network')
            self.assertEqual(http.call_count, 1)

    def test_invalid_payload_never_becomes_success(self):
        with patch.object(scanner.urllib.request, 'urlopen', return_value=io.BytesIO(b'{"status":"error"}')), patch.object(scanner.time, 'sleep'):
            with self.assertRaises(ValueError):
                scanner.request('/network')


if __name__ == '__main__':
    unittest.main()
