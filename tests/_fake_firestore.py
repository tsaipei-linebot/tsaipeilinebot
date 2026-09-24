"""測試用的記憶體版 Firestore（2026-09-24 新增，給 salesdev 的測試用）。

只實作 salesdev/repository.py 用到的那一小部分 API：collection/document、
get/set(merge)、where("欄位", "==", 值).stream()、stream()、get_all()、
batch()。merge=True 只做最上層欄位合併（repository 只寫最上層欄位）。
"""
import copy


class FakeSnapshot:
    def __init__(self, doc_id, data):
        self.id = doc_id
        self._data = data
        self.exists = data is not None

    def to_dict(self):
        return copy.deepcopy(self._data) if self._data is not None else None


class FakeDocRef:
    def __init__(self, store: dict, doc_id: str):
        self._store = store
        self.id = doc_id

    def get(self):
        return FakeSnapshot(self.id, self._store.get(self.id))

    def set(self, data, merge=False):
        data = copy.deepcopy(data)
        if merge and self.id in self._store:
            self._store[self.id].update(data)
        else:
            self._store[self.id] = data


class FakeQuery:
    def __init__(self, store: dict, filters: list):
        self._store = store
        self._filters = filters

    def where(self, field, op, value):
        assert op == "==", "fake 只支援 =="
        return FakeQuery(self._store, self._filters + [(field, value)])

    def stream(self):
        for doc_id, data in list(self._store.items()):
            if all(data.get(field) == value for field, value in self._filters):
                yield FakeSnapshot(doc_id, data)


class FakeCollection(FakeQuery):
    def __init__(self, store: dict):
        super().__init__(store, [])

    def document(self, doc_id):
        return FakeDocRef(self._store, doc_id)


class FakeBatch:
    def __init__(self):
        self._ops = []

    def set(self, ref, data, merge=False):
        self._ops.append((ref, data, merge))

    def commit(self):
        for ref, data, merge in self._ops:
            ref.set(data, merge=merge)
        self._ops = []


class FakeFirestore:
    def __init__(self):
        self.collections = {}

    def collection(self, name):
        return FakeCollection(self.collections.setdefault(name, {}))

    def get_all(self, refs):
        return [ref.get() for ref in refs]

    def batch(self):
        return FakeBatch()

    def docs(self, name) -> dict:
        return self.collections.get(name, {})
