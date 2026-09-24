import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401
from tests import _stub_gcp

_stub_gcp.install()

from salesdev import repository
from tests._fake_firestore import FakeFirestore


def lead(job_id, company="悅盛人力資源有限公司", title="日領5200", address="台灣桃園市桃園區桃鶯路XX號", source="chickpt"):
    return {
        "source": source,
        "job_id": job_id,
        "job_title": title,
        "company_name": company,
        "job_url": f"https://example.com/{job_id}",
        "work_address": address,
    }


class RepositoryTestCase(unittest.TestCase):
    def setUp(self):
        self.db = FakeFirestore()
        patcher = mock.patch.object(repository, "get_db", return_value=self.db)
        patcher.start()
        self.addCleanup(patcher.stop)

    def groups(self):
        return repository.list_groups(include_empty=True)


class UpsertJobsTests(RepositoryTestCase):
    def test_same_road_different_agencies_become_one_group(self):
        stats = repository.upsert_jobs(
            [
                lead("a", title="日薪5940先別滑"),
                lead("b", title="黃仁勳沒發的，我們先發了", address="台灣桃園市桃園區桃鶯路437號"),
                lead("c", company="天泰人力銀行_鼎利國際事業有限公司", title="(R)桃鶯路 簡單組包", address="桃園市桃園區桃鶯路", source="104"),
            ],
            seen_date="2026-09-24",
        )
        self.assertEqual(stats["new_jobs"], 3)
        self.assertEqual(stats["new_groups"], 1)
        [group] = self.groups()
        self.assertEqual(group["label"], "桃園市桃園區桃鶯路")
        self.assertEqual(group["job_count"], 3)
        self.assertEqual(group["agency_names"], ["天泰人力銀行_鼎利國際事業有限公司", "悅盛人力資源有限公司"])
        self.assertEqual(group["review_status"], repository.STATUS_PENDING)

    def test_rerun_is_idempotent_and_updates_last_seen(self):
        repository.upsert_jobs([lead("a")], seen_date="2026-09-24")
        stats = repository.upsert_jobs([lead("a")], seen_date="2026-09-25")
        self.assertEqual(stats["new_jobs"], 0)
        self.assertEqual(stats["updated_jobs"], 1)
        self.assertEqual(stats["new_groups"], 0)
        job = repository.get_job("chickpt_a")
        self.assertEqual(job["first_seen"], "2026-09-24")
        self.assertEqual(job["last_seen"], "2026-09-25")
        self.assertEqual(len(self.db.docs(repository.JOBS_COLLECTION)), 1)

    def test_review_status_survives_new_jobs_arriving(self):
        repository.upsert_jobs([lead("a")], seen_date="2026-09-24")
        [group] = self.groups()
        repository.update_group_review(group["id"], repository.STATUS_DONE, {"client_company": "某工廠"}, "少凱")
        repository.upsert_jobs([lead("b", title="新的標題")], seen_date="2026-09-25")
        [group] = self.groups()
        self.assertEqual(group["review_status"], repository.STATUS_DONE)
        self.assertEqual(group["client_company"], "某工廠")
        self.assertEqual(group["job_count"], 2)
        self.assertEqual(group["last_seen"], "2026-09-25")

    def test_internal_job_does_not_join_a_group(self):
        stats = repository.upsert_jobs([lead("x", company="智邦人力資源管理顧問有限公司", title="人力仲介行政人員", address="")])
        self.assertEqual(stats["internal_jobs"], 1)
        self.assertEqual(self.groups(), [])
        self.assertEqual([j["id"] for j in repository.list_internal_jobs()], ["chickpt_x"])

    def test_mojibake_title_is_repaired_on_save(self):
        garbled = "🔥".encode("utf-8").decode("latin-1") + "日領"
        repository.upsert_jobs([lead("a", title=garbled)])
        self.assertEqual(repository.get_job("chickpt_a")["job_title"], "🔥日領")


class InternalToggleTests(RepositoryTestCase):
    def test_restore_internal_job_puts_it_back_into_its_group(self):
        repository.upsert_jobs([lead("x", title="人力仲介行政人員")])
        new_group = repository.set_job_internal("chickpt_x", False, "少凱")
        self.assertTrue(new_group)
        [group] = self.groups()
        self.assertEqual(group["id"], new_group)
        self.assertEqual(group["job_count"], 1)

    def test_manual_override_survives_the_next_scrape(self):
        repository.upsert_jobs([lead("x", title="人力仲介行政人員")])
        repository.set_job_internal("chickpt_x", False, "少凱")
        repository.upsert_jobs([lead("x", title="人力仲介行政人員")])
        self.assertTrue(repository.get_job("chickpt_x")["group_id"])

    def test_marking_internal_leaves_empty_group_behind_but_hidden(self):
        repository.upsert_jobs([lead("a")])
        [group] = self.groups()
        repository.update_group_note(group["id"], "重要備註", "少凱")
        repository.set_job_internal("chickpt_a", True, "少凱")
        self.assertEqual(repository.list_groups(), [])
        [kept] = self.groups()
        self.assertEqual(kept["job_count"], 0)
        self.assertEqual(kept["note"], "重要備註")

    def test_unknown_job(self):
        self.assertIsNone(repository.set_job_internal("nope", True))


class GroupEditTests(RepositoryTestCase):
    def setUp(self):
        super().setUp()
        repository.upsert_jobs([lead("a"), lead("b", address="桃園市大園區航翔路7號")])
        self.group_ids = sorted(g["id"] for g in self.groups())

    def test_select_only_changes_pending_groups(self):
        repository.update_group_review(self.group_ids[0], repository.STATUS_SKIPPED, {}, "少凱")
        count = repository.select_groups_for_lookup(self.group_ids + ["missing"], "少凱")
        self.assertEqual(count, 1)
        statuses = {g["id"]: g["review_status"] for g in self.groups()}
        self.assertEqual(statuses[self.group_ids[0]], repository.STATUS_SKIPPED)
        self.assertEqual(statuses[self.group_ids[1]], repository.STATUS_SELECTED)

    def test_invalid_status_is_rejected(self):
        self.assertFalse(repository.update_group_review(self.group_ids[0], "亂打", {}, "少凱"))

    def test_contact_logs_append_with_author(self):
        repository.add_contact_log(self.group_ids[0], "打給王小姐", "少凱")
        repository.add_contact_log(self.group_ids[0], "下週回電", "阿明")
        logs = repository.get_group(self.group_ids[0])["contact_logs"]
        self.assertEqual([(l["by"], l["text"]) for l in logs], [("少凱", "打給王小姐"), ("阿明", "下週回電")])

    def test_empty_contact_log_is_rejected(self):
        self.assertFalse(repository.add_contact_log(self.group_ids[0], "   ", "少凱"))


class FactoryTests(RepositoryTestCase):
    def test_found_date_is_kept_on_rescan(self):
        record = {"dedup_key": "tax:12345678", "name": "新工廠", "address": "台中市"}
        self.assertEqual(repository.upsert_factories([record], "2026-09-19"), 1)
        self.assertEqual(repository.upsert_factories([dict(record, name="新工廠(改名)")], "2026-09-26"), 0)
        [factory] = repository.list_factories()
        self.assertEqual(factory["found_date"], "2026-09-19")
        self.assertEqual(factory["name"], "新工廠(改名)")


if __name__ == "__main__":
    unittest.main()
