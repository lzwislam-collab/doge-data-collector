"""Strict offline Linux-only checks. A missing Linux environment is never a pass."""
import argparse,json,platform,socket,sys,unittest
from unittest.mock import patch
from pathlib import Path

def main():
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
 if not sys.platform.startswith('linux'):raise SystemExit('Linux required; this host cannot close the Linux verification gap')
 names=['test_pit_retention_preparation.PreparationTests.test_output_directory_symlink_cannot_redirect_package_writes',
        'test_pit_retention_preparation.PreparationTests.test_latest_symlink_cannot_overwrite_outside_file',
        'test_inventory_capacity.CapacityTests.test_destination_ancestor_link_cannot_write_outside',
        'test_inventory_capacity.CapacityTests.test_stage_root_link_is_rejected_before_download']
 with patch.object(socket.socket,'connect',side_effect=AssertionError('offline: network forbidden')),patch.object(socket.socket,'connect_ex',side_effect=AssertionError('offline: network forbidden')):
  result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromNames(names))
 report={'schema':'OFFLINE_LINUX_INVENTORY_CHECKS_V1','platform':sys.platform,'kernel':platform.release(),
         'tests_run':result.testsRun,'skipped':len(result.skipped),'failures':len(result.failures),'errors':len(result.errors),
         'linux_verified':result.wasSuccessful() and not result.skipped and result.testsRun==len(names)}
 args.output.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
 if not report['linux_verified']:raise SystemExit('Linux offline verification incomplete')
 print(json.dumps(report))

if __name__=='__main__':main()
