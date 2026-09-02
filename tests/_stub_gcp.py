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
"""
import sys
import types


def install() -> None:
    if isinstance(sys.modules.get("google.cloud.firestore"), types.ModuleType) and getattr(
        sys.modules["google.cloud.firestore"], "_is_test_stub", False
    ):
        return

    fake_firestore_mod = types.ModuleType("google.cloud.firestore")
    fake_firestore_mod.Client = lambda *a, **k: None
    fake_firestore_mod._is_test_stub = True

    fake_google_cloud = types.ModuleType("google.cloud")
    fake_google_cloud.firestore = fake_firestore_mod

    fake_google = types.ModuleType("google")
    fake_google.cloud = fake_google_cloud

    sys.modules["google"] = fake_google
    sys.modules["google.cloud"] = fake_google_cloud
    sys.modules["google.cloud.firestore"] = fake_firestore_mod
