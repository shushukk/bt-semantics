"""让 ``pytest`` 直接以项目根目录为导入根，无需安装包。"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
