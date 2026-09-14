"""「一鍵整理車輛狀況」報告產生邏輯：把車輛清單依廠商/服務區域彙整成
方便直接貼到 LINE 群組的文字報告。格式範例（欄位順序、符號都是使用者
指定的固定格式，不要隨意調整）：

    📊【蝦皮三輪 車輛現況】2026/9/14

    總車輛數：126
    使用中：85
    空車：27
    待維修：14

    地區分布：
    ・台北：共37台／空車4／使用中30／待維修3
    ・新北：共18台／空車8／使用中9／待維修1

全部廠商一次產生（見 build_fleet_status_report()），每個廠商各自一段，
完全沒有車輛的廠商直接跳過，不產生空段落。

只有這裡的函式是純函式（不碰 Firestore、today 由呼叫端傳入方便測試），
方便寫單元測試；呼叫端（delivery/routes/vehicle_routes.py）負責先用
repository.list_vehicles() 撈出全部車輛再傳進來。
"""
from datetime import date

from delivery.config import SERVICE_AREAS, VENDORS

_UNASSIGNED_AREA_LABEL = "未分區"

EMPTY_REPORT_MESSAGE = "目前系統裡沒有任何車輛資料。"


def _status_counts(vehicles: list) -> dict:
    counts = {"available": 0, "in_use": 0, "maintenance": 0}
    for vehicle in vehicles:
        status = vehicle.get("status", "available")
        if status in counts:
            counts[status] += 1
    return counts


def _region_breakdown(vehicles: list) -> list:
    """回傳 [(地區名稱, counts_dict), ...]，依 SERVICE_AREAS 宣告順序排列；
    完全沒有車輛的地區直接跳過。如果有車輛沒設定服務區域（例如這個欄位
    新增之前建立的舊資料，還沒被管理員補上），額外補一行「未分區」放在
    最後，確保總數對得起來、不會有車輛悄悄從報告裡消失。"""
    by_area_code = {}
    for vehicle in vehicles:
        area_code = vehicle.get("service_area", "")
        by_area_code.setdefault(area_code, []).append(vehicle)

    result = []
    for area in SERVICE_AREAS:
        area_vehicles = by_area_code.get(area["code"], [])
        if area_vehicles:
            result.append((area["name"], _status_counts(area_vehicles)))

    unassigned = by_area_code.get("", [])
    if unassigned:
        result.append((_UNASSIGNED_AREA_LABEL, _status_counts(unassigned)))
    return result


def build_vendor_fleet_report(vendor_name: str, vehicles: list, today: date = None) -> str:
    """單一廠商的車輛現況報告文字。vehicles 需已經是篩選過、只含這個廠商
    的車輛清單（呼叫端負責分好，這裡不再依廠商過濾）。"""
    today = today or date.today()
    counts = _status_counts(vehicles)
    lines = [
        f"📊【{vendor_name} 車輛現況】{today.year}/{today.month}/{today.day}",
        "",
        f"總車輛數：{len(vehicles)}",
        f"使用中：{counts['in_use']}",
        f"空車：{counts['available']}",
        f"待維修：{counts['maintenance']}",
        "",
        "地區分布：",
    ]
    for area_name, area_counts in _region_breakdown(vehicles):
        area_total = area_counts["available"] + area_counts["in_use"] + area_counts["maintenance"]
        lines.append(
            f"・{area_name}：共{area_total}台／空車{area_counts['available']}／"
            f"使用中{area_counts['in_use']}／待維修{area_counts['maintenance']}"
        )
    return "\n".join(lines)


def build_fleet_status_report(all_vehicles: list, today: date = None) -> str:
    """全部廠商的車輛現況報告，依 VENDORS 宣告順序，每個廠商各自一段、
    中間空一行分隔；完全沒有車輛的廠商直接跳過，不產生空段落。整個系統
    目前沒有任何車輛資料時回傳提示文字，不回傳空字串（避免呼叫端誤判成
    程式出錯）。"""
    if not all_vehicles:
        return EMPTY_REPORT_MESSAGE

    by_vendor_code = {}
    for vehicle in all_vehicles:
        by_vendor_code.setdefault(vehicle.get("vendor", ""), []).append(vehicle)

    blocks = []
    for vendor in VENDORS:
        vendor_vehicles = by_vendor_code.get(vendor["code"], [])
        if vendor_vehicles:
            blocks.append(build_vendor_fleet_report(vendor["name"], vendor_vehicles, today=today))

    if not blocks:
        return EMPTY_REPORT_MESSAGE
    return "\n\n".join(blocks)
