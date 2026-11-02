import sys,unittest
from pathlib import Path
sys.path.insert(0,'/opt/astrbot-mouth')
sys.path.insert(1,'/opt/astrbot-mouth/tests')
sys.path.append('/opt/qq-mouth')
import behavior,wakeups
behavior.CONFIG=Path('/tmp/nonexistent-astrbot-test-config.json')
wakeups.MOUTH=Path('/tmp/nonexistent-astrbot-test-mouth.db')
r=unittest.TextTestRunner(verbosity=1).run(unittest.defaultTestLoader.loadTestsFromNames(['test_store','test_relay']))
sys.exit(not r.wasSuccessful())
