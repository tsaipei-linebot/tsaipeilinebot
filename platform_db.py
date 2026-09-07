"""所有部門模組共用的使用者帳號資料表。

獨立成這支檔案（不放在 delivery/db.py 底下），是因為使用者帳號從這次多
模組改版開始就是「全平台共用」的概念——一個帳號可能同時對應配送部、管理部
…等好幾個模組，不屬於任何單一模組，之後每加一個新部門也不需要另外開一份
帳號資料。

Collection 名稱沿用歷史上的 delivery_users（這個系統最早只有配送部一個
模組時取的名字），刻意不为了改名而搬移既有正式環境資料，純粹是命名上的
歷史包袱，不影響實際功能。
"""
from google.cloud import firestore

from config import GCP_PROJECT_ID

USERS_COLLECTION = "delivery_users"

_client = None


def get_db():
    global _client
    if _client is None:
        _client = firestore.Client(project=GCP_PROJECT_ID, database="(default)")
    return _client


def users_ref():
    return get_db().collection(USERS_COLLECTION)
