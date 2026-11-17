"""A quiz makeup's length for scheduling (ABSENCES "MAKEUP BOOKING" LENGTH, absences/makeup_length.py; requirement, 2026-10-05). The multiplier is the accommodation column's name ("Extra Time 1.50x"); its value says whether it is granted."""
import json
import sqlite3
import unittest

from absences import makeup_length as length


class LengthTests(unittest.TestCase):
    def test_the_multiplier_is_the_largest_granted_extra_time(self):
        self.assertEqual(length.multiplier({"Extra Time 1.50x": "Yes", "Day Time Exams": "Yes"}), 1.5)
        self.assertEqual(length.multiplier({"Extra Time 1.50x": "Yes", "Extra Time 1.75x": "yes"}), 1.75)
        self.assertEqual(length.multiplier({"Extra Time 1.50x": "", "Extra Time 1.75x": "No"}), 1.0)  # not granted
        self.assertEqual(length.multiplier({"extra time 2x": "Y"}), 2.0)
        self.assertEqual(length.multiplier({"Extra Time 9x": "Yes"}), 1.0)  # outside any sensible range: ignored
        self.assertEqual(length.multiplier(None), 1.0)
        self.assertEqual([length.minutes_for(m) for m in (1, 1.5, 1.75, 2)], [20, 30, 35, 40])

    def test_a_student_and_a_shared_sitting(self):
        db = sqlite3.connect(":memory:")
        db.executescript("""
            CREATE TABLE accommodation_revisions (id TEXT PRIMARY KEY, snapshot_json TEXT);
            CREATE TABLE student_accommodations (section_id TEXT, enrollment_id TEXT, revision_id TEXT);
            CREATE TABLE exam_participants (sitting_id TEXT, section_id TEXT, enrollment_id TEXT);""")
        for ident, values in (("r1", {"Extra Time 1.50x": "Yes"}), ("r2", {"Extra Time 1.75x": "Yes"})):
            db.execute("INSERT INTO accommodation_revisions VALUES (?,?)", (ident, json.dumps({"values": values, "metadata": {}})))
        db.executemany("INSERT INTO student_accommodations VALUES (?,?,?)", [("s507", "e1", "r1"), ("s508", "e2", "r2")])
        db.executemany("INSERT INTO exam_participants VALUES (?,?,?)", [("shared", "s507", "e1"), ("shared", "s508", "e3"), ("alone", "s508", "e3")])
        self.assertEqual(length.student_minutes(db, "s507", "e1"), 30)
        self.assertEqual(length.student_minutes(db, "s508", "e1"), 20)  # another section's record never counts
        self.assertEqual(length.student_minutes(db, "s508", "e3"), 20)  # no record
        self.assertEqual(length.sitting_minutes(db, "shared"), 30)  # its longest student
        self.assertEqual(length.sitting_minutes(db, "alone"), 20)
        self.assertEqual(length.sitting_minutes(db, "empty"), 20)
        self.assertEqual(length.student_minutes(sqlite3.connect(":memory:"), "s507", "e1"), 20)  # a catalog without accommodations


if __name__ == "__main__":
    unittest.main()
