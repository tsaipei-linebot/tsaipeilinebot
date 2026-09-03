"""建立配送部系統第一組登入帳號用的命令列工具。

系統本身沒有開放自行註冊（同仁帳號是配送部管理的內部資料），所以第一組
帳號（以及之後新增帳號）都透過這支腳本手動建立，直接在有 GCP 憑證的環境
（例如透過 Cloud Run 的一次性 job，或本機用有權限的 ADC）執行：

    python -m delivery.seed_admin <username> <password> <name> [role]

role 預設為 admin；一般同仁帳號可傳 staff。
"""
import getpass
import sys

from delivery.auth import create_user


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)

    username = sys.argv[1]
    name = sys.argv[3] if len(sys.argv) > 3 else username
    role = sys.argv[4] if len(sys.argv) > 4 else "admin"

    password = sys.argv[2] if len(sys.argv) > 2 and sys.argv[2] != "-" else getpass.getpass("密碼：")

    create_user(username, password, name, role)
    print(f"已建立帳號：{username}（{name}，role={role}）")


if __name__ == "__main__":
    main()
