"""ファイル仕分け君 起動スクリプト（pythonw で実行するとコンソールを表示しない）"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from filesorter.main import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
