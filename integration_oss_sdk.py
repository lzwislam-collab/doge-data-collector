"""Mandatory real-provider SDK check in oss_inventory_retention.yml.

Kept outside unittest's test*.py discovery so frozen prediction workflows do
not acquire an unrelated cloud-provider dependency. No skips or SDK mocks.
"""
import unittest
import oss_inventory_retention as m


class Tests(unittest.TestCase):
    def test_real_sdk_constructs_short_lived_sts_client_without_network(self):
        client=m.identity_client('unit-test-ak','unit-test-secret','unit-test-token')
        self.assertEqual(client.get_region_id(),'cn-hangzhou')


if __name__=='__main__':unittest.main()
