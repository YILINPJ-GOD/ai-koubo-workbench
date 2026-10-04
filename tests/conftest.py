"""pytest 全局配置：在导入任何 app 模块之前重定向数据目录到临时目录。

注意：conftest 必须最先被 pytest 加载，且环境变量在模块顶层设置，
保证 app.config 在首次导入时就拿到 WORKBENCH_HOME。
"""
import pathlib
import tempfile

_TEST_HOME = pathlib.Path(tempfile.mkdtemp(prefix="wb-test-home-"))

import os  # noqa: E402

os.environ["WORKBENCH_HOME"] = str(_TEST_HOME)
