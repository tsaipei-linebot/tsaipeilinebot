#!/usr/bin/env python3
"""一次性遷移腳本：人員名冊裡已經選好「所屬廠商」、但「合作方式」還沒填
的人，如果這個廠商目前在「合作方式管理」剛好只對應唯一一種合作方式，就
自動幫他補上——2026-09-21 新增「所屬廠商跟合作方式連動」功能時，使用者
確認過目前這些廠商在合作方式管理裡都是唯一對應（不是「一個廠商同時掛著
多種合作方式」那種情境），要求把既有資料一次補齊，不用等同仁一筆一筆去
人員詳細頁手動點。

安全性（只補、不覆蓋，也不亂猜）：
- 只處理「合作方式目前是空的」人員，已經填過的（不管填的是不是這裡會
  自動判斷出來的值）一律跳過，不會覆蓋同仁手動修正過的資料。
- 只有「這個廠商目前剛好對應到唯一一種合作方式」才會自動補上；如果某個
  廠商還是對應到不只一種合作方式（表示合作方式管理那邊的「適用廠商」
  設定還沒調整成真的唯一對應），這些人員會被跳過、列出來，不會用猜的
  幫他選一個。
- 沒有選廠商的人員本來就不在這次處理範圍內，一併跳過。

用法（在有 GCP 憑證、能連 Firestore 的環境，例如 Cloud Shell，位於 repo
根目錄執行）：

    python -m scripts.fill_personnel_cooperation_type

會先印出即將補上的名單再詢問是否要真的寫入，輸入 yes 才會執行。
"""
import sys

from delivery.repository import cooperation_types_by_vendor, personnel_ref, update_personnel_cooperation_type


def plan_fill(personnel: list, coop_by_vendor: dict) -> dict:
    """純函式邏輯（personnel/coop_by_vendor 都是已經查好的資料，不碰
    Firestore 寫入），方便寫單元測試。回傳：
    - to_fill：可以自動補上的人員，附帶要填的合作方式 ID/名稱
    - already_set：合作方式已經有值，跳過不動
    - no_vendor：沒有選廠商，跳過
    - no_options：廠商目前沒有任何合作方式選項可對應，跳過
    - ambiguous：廠商目前對應到不只一種合作方式，沒辦法自動判斷，跳過
    """
    to_fill = []
    already_set = []
    no_vendor = []
    no_options = []
    ambiguous = []
    for p in personnel:
        name = p.get("name", "")
        vendor = p.get("vendor", "")
        if not vendor:
            no_vendor.append({"id": p["id"], "name": name})
            continue
        if p.get("cooperation_type"):
            already_set.append({"id": p["id"], "name": name})
            continue
        options = coop_by_vendor.get(vendor) or []
        if len(options) == 1:
            to_fill.append(
                {
                    "id": p["id"],
                    "name": name,
                    "vendor": vendor,
                    "cooperation_type": options[0]["id"],
                    "cooperation_type_name": options[0]["name"],
                }
            )
        elif len(options) == 0:
            no_options.append({"id": p["id"], "name": name, "vendor": vendor})
        else:
            ambiguous.append({"id": p["id"], "name": name, "vendor": vendor, "option_count": len(options)})
    return {
        "to_fill": to_fill,
        "already_set": already_set,
        "no_vendor": no_vendor,
        "no_options": no_options,
        "ambiguous": ambiguous,
    }


def _list_all_personnel() -> list:
    result = []
    for snapshot in personnel_ref().stream():
        data = snapshot.to_dict() or {}
        data["id"] = snapshot.id
        result.append(data)
    return result


def main():
    personnel = _list_all_personnel()
    plan = plan_fill(personnel, cooperation_types_by_vendor())

    print(f"人員名冊總共 {len(personnel)} 筆。")
    print(f"合作方式已經有值、這次跳過不動：{len(plan['already_set'])} 筆")
    print(f"沒有選廠商、這次跳過：{len(plan['no_vendor'])} 筆")

    if plan["no_options"]:
        print(f"\n廠商目前在合作方式管理裡沒有對應到任何選項，需要人工處理：{len(plan['no_options'])} 筆")
        for row in plan["no_options"]:
            print(f"  - {row['name']}（廠商：{row['vendor']}）")

    if plan["ambiguous"]:
        print(f"\n廠商目前對應到不只一種合作方式，無法自動判斷、需要人工處理：{len(plan['ambiguous'])} 筆")
        for row in plan["ambiguous"]:
            print(f"  - {row['name']}（廠商：{row['vendor']}，目前有 {row['option_count']} 種合作方式可選）")

    if not plan["to_fill"]:
        print("\n沒有可以自動補上合作方式的人員。")
        return

    print(f"\n=== 即將自動補上合作方式的人員（共 {len(plan['to_fill'])} 筆）===")
    for row in plan["to_fill"]:
        print(f"  - {row['name']}（廠商：{row['vendor']} → 合作方式：{row['cooperation_type_name']}）")

    answer = input(f"\n確定要幫上面 {len(plan['to_fill'])} 筆人員補上合作方式嗎？輸入 yes 才會執行：")
    if answer.strip().lower() != "yes":
        print("已取消，沒有做任何變更。")
        return

    for row in plan["to_fill"]:
        update_personnel_cooperation_type(row["id"], row["cooperation_type"])

    print(f"\n完成，共補上 {len(plan['to_fill'])} 筆人員的合作方式。")


if __name__ == "__main__":
    if len(sys.argv) != 1:
        print(__doc__)
        sys.exit(1)
    main()
