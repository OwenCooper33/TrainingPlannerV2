from datetime import date
from pathlib import Path
import tempfile
import unittest

from app.db import get_conn, init_db
from server import generate_plan, list_plan


class PlanGenerationTests(unittest.TestCase):
    def test_generate_plan_persists_rows(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            init_db(db_path)
            with get_conn(db_path) as conn:
                out = generate_plan(conn, user_id=1, start_day=date.today(), weeks=2, disciplines=["cycling", "running", "triathlon"])
                self.assertEqual(out["days_created"], 14)
                rows = list_plan(conn, user_id=1, start_day=date.today(), end_day=date.fromordinal(date.today().toordinal() + 20))
                self.assertEqual(len(rows), 14)


if __name__ == "__main__":
    unittest.main()
