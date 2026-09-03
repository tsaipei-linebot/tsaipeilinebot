"""測試進程共用的環境變數初始化。

`python -m unittest discover` 會把 tests/ 底下所有測試檔案匯入到同一個
Python 進程裡執行，而 config.py 是在「匯入當下」讀取一次 os.environ 就固定
下來，之後任何測試檔案再修改 os.environ 都不會讓已經匯入過的 config 模組
（以及所有已經 `from config import ...` 過的模組，例如 main.py）重新讀取。

如果每個測試檔案各自在自己的檔案頂端 `os.environ.setdefault(...)`，先被
unittest discover 匯入的那個檔案會「贏」、把值固定死，後面的檔案設定的
值完全不會生效——這是一種隱藏的測試間耦合。所以改成所有測試檔案的第一個
本地匯入都先 `from tests import _env`，統一在這裡 setdefault 好全部測試共用
的假環境變數，不管 unittest discover 用什麼順序執行都會拿到一致的值。
"""
import os

os.environ.setdefault("GEMINI_API_KEY", "dummy")
os.environ.setdefault("LINE_CHANNEL_SECRET", "dummy_secret")
os.environ.setdefault("LINE_CHANNEL_ACCESS_TOKEN", "dummy_token")
os.environ.setdefault("LOAD_TEST_SECRET", "test-secret-for-unittest")
