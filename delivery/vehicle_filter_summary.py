"""車輛清單「找不到符合的車輛」提示用的篩選條件文字（2026-09-23 新增）。

**為什麼要有這支**：使用者回報「車輛管理查詢不到好像不會跳任何資訊」。
查下來畫面其實**有**訊息，但那一行是 `.empty`（淺灰色、14px 小字），
緊接在一排六個篩選控制項後面，很容易整個被忽略，看起來就像按了搜尋
沒反應。而且那句話是固定的「目前沒有符合條件的車輛。」，**沒有講出
使用者剛才搜的是什麼**，所以也無從判斷是自己打錯字還是真的沒有這台車。

這裡把「這次套用了哪些條件」整理成人看得懂的短句（例如
`車號包含「ABC-123」`、`廠商「蝦皮三輪」`），交給模板顯示在提示框裡。

寫成不碰資料庫的純函式，顯示名稱的對照表由呼叫端傳進來（路由本來就
已經查好那幾份對照表要給表格用），方便直接寫單元測試。
"""


def describe_vehicle_filters(
    *,
    vehicle_no: str = "",
    vendor: str = "",
    status: str = "",
    wheel_type: str = "",
    service_area: str = "",
    cooperation_type: str = "",
    vendor_map: dict = None,
    vehicle_status_map: dict = None,
    wheel_type_map: dict = None,
    service_area_map: dict = None,
    cooperation_type_map: dict = None,
) -> list:
    """回傳一串人看得懂的條件描述，沒有套用任何條件時回傳空 list。

    找不到對應顯示名稱時就直接顯示原始值（代號），不會整個略過——條件
    確實有套用，寧可顯示得醜一點也不要讓使用者以為沒套用到。"""
    descriptions = []

    vehicle_no = (vehicle_no or "").strip()
    if vehicle_no:
        descriptions.append(f"車號包含「{vehicle_no}」")

    for value, name_map, label in (
        (vendor, vendor_map, "廠商"),
        (status, vehicle_status_map, "狀態"),
        (wheel_type, wheel_type_map, "輪別"),
        (service_area, service_area_map, "服務區域"),
        (cooperation_type, cooperation_type_map, "騎手身份"),
    ):
        value = (value or "").strip()
        if not value:
            continue
        display = (name_map or {}).get(value, value)
        descriptions.append(f"{label}「{display}」")

    return descriptions
