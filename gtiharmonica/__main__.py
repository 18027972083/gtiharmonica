"""python -m gtiharmonica 入口。

走 app.main：带子命令时进 CLI、不带时开图形界面，
两条路都过卡密激活检查（与打包版 exe 的入口一致）。
"""
import sys

from .app import main

if __name__ == '__main__':
    sys.exit(main())
