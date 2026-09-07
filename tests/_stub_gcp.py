"""測試專用：在匯入間接依賴 Firestore 的模組之前，先 stub 掉
google.cloud.firestore。

matcher_service.py 會匯入 session_service.py，而 session_service.py 在
「模組層級」直接呼叫 firestore.Client(...) 建立連線，這代表只要匯入
matcher_service，就一定會嘗試連線 GCP，在沒有 Application Default
Credentials 的環境（例如本機、CI）下會直接匯入失敗，導致完全無法對
matcher_service 裡的純邏輯函式寫單元測試。

這裡不去動 session_service.py 的實作（改成延遲初始化屬於更大範圍的重構，
不在這次補單元測試的範圍內），而是在測試進程裡用假的 firestore 模組
頂替掉，讓匯入鏈可以正常跑完，純粹是測試環境的權宜之計，不影響正式環境
（Cloud Run 服務帳戶有真正的 ADC，正式環境的 import 不會經過這個 stub）。

刻意先 `import google.cloud` 拿到「真正的」google/google.cloud 套件物件，
只在上面覆蓋/新增 firestore 這個子模組，而不是整個 `google` 頂層套件都
換成假的——後者會導致這個檔案如果在其他測試（例如需要 `from google import
genai` 的 ai_service.py）已經真正匯入過 google.genai 之前先被呼叫，
google.genai 就會憑空消失，導致該測試檔案「單獨執行」會失敗，卻只在
「整個 tests/ 目錄一起跑」時因為匯入順序湊巧正確而測不出來——這是一種
隱藏的測試間耦合，跟 _env.py 在解決的問題是同一類型。
"""
import sys
import types

import google.cloud  # noqa: E402  匯入真正的 google/google.cloud 套件物件


def install() -> None:
    if getattr(getattr(google.cloud, "firestore", None), "_is_test_stub", False):
        return

    fake_firestore_mod = types.ModuleType("google.cloud.firestore")
    fake_firestore_mod.Client = lambda *a, **k: None
    fake_firestore_mod._is_test_stub = True

    sys.modules["google.cloud.firestore"] = fake_firestore_mod
    google.cloud.firestore = fake_firestore_mod
