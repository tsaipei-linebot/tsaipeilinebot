import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401
from tests import _stub_gcp
_stub_gcp.install()

import platform_departments


def _fake_doc(doc_id, data):
    doc = mock.Mock()
    doc.id = doc_id
    doc.to_dict.return_value = data
    return doc


class ToDepartmentTests(unittest.TestCase):
    def test_defaults_missing_fields(self):
        department = platform_departments._to_department("d1", {})
        self.assertEqual(department["id"], "d1")
        self.assertEqual(department["name"], "")
        self.assertEqual(department["sort_index"], 0)

    def test_preserves_present_fields(self):
        department = platform_departments._to_department("d1", {"name": "管理部", "sort_index": 3})
        self.assertEqual(department["name"], "管理部")
        self.assertEqual(department["sort_index"], 3)


class ListDepartmentsTests(unittest.TestCase):
    def test_sorted_by_sort_index_then_name(self):
        docs = [
            _fake_doc("d1", {"name": "財務部", "sort_index": 8}),
            _fake_doc("d2", {"name": "台北所(派遣組)", "sort_index": 0}),
            _fake_doc("d3", {"name": "管理部", "sort_index": 7}),
        ]
        fake_collection = mock.Mock()
        fake_collection.stream.return_value = docs
        with mock.patch.object(platform_departments, "departments_ref", return_value=fake_collection):
            result = platform_departments.list_departments()
        self.assertEqual([d["name"] for d in result], ["台北所(派遣組)", "管理部", "財務部"])

    def test_list_department_names_returns_just_names_in_order(self):
        docs = [
            _fake_doc("d1", {"name": "桃園所", "sort_index": 1}),
            _fake_doc("d2", {"name": "台中所", "sort_index": 0}),
        ]
        fake_collection = mock.Mock()
        fake_collection.stream.return_value = docs
        with mock.patch.object(platform_departments, "departments_ref", return_value=fake_collection):
            result = platform_departments.list_department_names()
        self.assertEqual(result, ["台中所", "桃園所"])


class DepartmentNameExistsTests(unittest.TestCase):
    def test_existing_name_returns_true(self):
        docs = [_fake_doc("d1", {"name": "管理部", "sort_index": 0})]
        fake_collection = mock.Mock()
        fake_collection.stream.return_value = docs
        with mock.patch.object(platform_departments, "departments_ref", return_value=fake_collection):
            self.assertTrue(platform_departments.department_name_exists("管理部"))
            self.assertFalse(platform_departments.department_name_exists("不存在的部門"))


class ValidateDepartmentNameTests(unittest.TestCase):
    def test_blank_name_fails(self):
        with mock.patch.object(platform_departments, "list_departments", return_value=[]):
            error = platform_departments.validate_department_name("   ")
        self.assertIn("不能空白", error)

    def test_duplicate_name_fails(self):
        existing = [{"id": "d1", "name": "管理部", "sort_index": 0}]
        with mock.patch.object(platform_departments, "list_departments", return_value=existing):
            error = platform_departments.validate_department_name("管理部")
        self.assertIn("同名", error)

    def test_duplicate_name_excludes_self_when_editing(self):
        existing = [{"id": "d1", "name": "管理部", "sort_index": 0}]
        with mock.patch.object(platform_departments, "list_departments", return_value=existing):
            error = platform_departments.validate_department_name("管理部", editing_id="d1")
        self.assertEqual(error, "")

    def test_valid_new_name_passes(self):
        existing = [{"id": "d1", "name": "管理部", "sort_index": 0}]
        with mock.patch.object(platform_departments, "list_departments", return_value=existing):
            error = platform_departments.validate_department_name("財務部")
        self.assertEqual(error, "")


class CreateDepartmentTests(unittest.TestCase):
    def test_new_department_gets_next_sort_index(self):
        existing = [{"id": "d1", "name": "管理部", "sort_index": 3}, {"id": "d2", "name": "財務部", "sort_index": 5}]
        fake_ref = mock.Mock()
        fake_ref.id = "new-id"
        fake_collection = mock.Mock()
        fake_collection.document.return_value = fake_ref
        with mock.patch.object(platform_departments, "list_departments", return_value=existing):
            with mock.patch.object(platform_departments, "departments_ref", return_value=fake_collection):
                result_id = platform_departments.create_department("新部門")
        fake_ref.set.assert_called_once_with({"name": "新部門", "sort_index": 6})
        self.assertEqual(result_id, "new-id")

    def test_first_department_gets_sort_index_zero(self):
        fake_ref = mock.Mock()
        fake_ref.id = "new-id"
        fake_collection = mock.Mock()
        fake_collection.document.return_value = fake_ref
        with mock.patch.object(platform_departments, "list_departments", return_value=[]):
            with mock.patch.object(platform_departments, "departments_ref", return_value=fake_collection):
                platform_departments.create_department("第一個部門")
        fake_ref.set.assert_called_once_with({"name": "第一個部門", "sort_index": 0})


class ReorderDepartmentsTests(unittest.TestCase):
    def test_updates_sort_index_and_ignores_unknown_ids(self):
        existing = [{"id": "d1", "name": "管理部", "sort_index": 0}, {"id": "d2", "name": "財務部", "sort_index": 1}]
        fake_batch = mock.Mock()
        fake_db = mock.Mock()
        fake_db.batch.return_value = fake_batch
        doc_refs = {"d1": mock.Mock(name="doc_d1"), "d2": mock.Mock(name="doc_d2")}
        fake_collection = mock.Mock()
        fake_collection.document.side_effect = lambda i: doc_refs[i]

        with mock.patch.object(platform_departments, "list_departments", return_value=existing):
            with mock.patch.object(platform_departments, "departments_ref", return_value=fake_collection):
                with mock.patch("platform_db.get_db", return_value=fake_db):
                    platform_departments.reorder_departments(["d2", "d1", "unknown-id"])

        fake_batch.update.assert_any_call(doc_refs["d2"], {"sort_index": 0})
        fake_batch.update.assert_any_call(doc_refs["d1"], {"sort_index": 1})
        self.assertEqual(fake_batch.update.call_count, 2)
        fake_batch.commit.assert_called_once()


class CountAccountsUsingDepartmentTests(unittest.TestCase):
    def test_counts_only_matching_department(self):
        accounts = [
            {"department": "管理部"},
            {"department": "財務部"},
            {"department": "管理部"},
        ]
        with mock.patch("platform_accounts.list_accounts", return_value=accounts):
            self.assertEqual(platform_departments.count_accounts_using_department("管理部"), 2)
            self.assertEqual(platform_departments.count_accounts_using_department("台中所"), 0)


if __name__ == "__main__":
    unittest.main()
