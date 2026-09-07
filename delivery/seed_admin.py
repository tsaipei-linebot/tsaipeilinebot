"""建立平台第一組帳號（或補建之後的帳號）用的命令列工具。

系統本身沒有開放自行註冊（同仁帳號是平台管理的內部資料，透過 /accounts
網頁指派各部門權限），所以帳號的「建立」動作、以及全平台管理權限
（is_platform_admin）的授予，都只能透過這支腳本、在有 GCP 憑證的環境
（例如 Cloud Shell）手動執行：

    python -m delivery.seed_admin <username> <password> <name> [--platform-admin] \\
        [--module 模組代碼:角色 ...]

範例：
    # 建立老闆本人的帳號，擁有全平台管理權限（自動視同所有模組的管理員，
    # 不需要另外用 --module 指定）：
    python -m delivery.seed_admin boss - "老闆" --platform-admin

    # 建立一組只有配送部管理員權限的帳號：
    python -m delivery.seed_admin alice - "Alice" --module delivery:admin

    # 建立同時橫跨配送部（專員）跟管理部（主管）的帳號：
    python -m delivery.seed_admin bob - "Bob" --module delivery:staff --module management:admin

密碼帶 "-" 代表現場用 getpass 輸入，不會留在 shell history 裡。
"""
import getpass
import sys

from platform_accounts import create_account, set_platform_admin


def _parse_module_args(argv) -> dict:
    modules = {}
    i = 0
    while i < len(argv):
        if argv[i] == "--module":
            pair = argv[i + 1]
            code, _, role = pair.partition(":")
            if not code or role not in ("admin", "staff"):
                print(f"--module 格式錯誤：{pair}（要是「模組代碼:admin」或「模組代碼:staff」）")
                sys.exit(1)
            modules[code] = role
            i += 2
        else:
            i += 1
    return modules


def main():
    if len(sys.argv) < 4:
        print(__doc__)
        sys.exit(1)

    username = sys.argv[1]
    password = sys.argv[2] if sys.argv[2] != "-" else getpass.getpass("密碼：")
    name = sys.argv[3]
    rest = sys.argv[4:]

    is_platform_admin = "--platform-admin" in rest
    modules = _parse_module_args(rest)

    create_account(username, password, name, modules)
    if is_platform_admin:
        set_platform_admin(username, True)

    print(f"已建立帳號：{username}（{name}），modules={modules}，platform_admin={is_platform_admin}")


if __name__ == "__main__":
    main()
