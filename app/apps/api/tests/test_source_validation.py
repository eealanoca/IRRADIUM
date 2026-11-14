import unittest

from pydantic import ValidationError
from apps.api.sim_gamma_api.main import SourceMutation


class SourceValidationTests(unittest.TestCase):
    def test_rejects_degenerate_and_non_finite_cylinders(self):
        valid = dict(identifier="qa", lower_point=(0, 0, 0), upper_point=(0, 0, 450),
                     initial_activity_kci=1, initial_reading_date="2026-11-13")
        self.assertEqual(SourceMutation.model_validate(valid).upper_point, (0, 0, 450))
        for changes in [dict(upper_point=(0, 0, 0)), dict(upper_point=(0, 0, float("nan"))),
                        dict(lower_point=(float("inf"), 0, 0)),
                        dict(initial_activity_kci=float("inf")), dict(initial_activity_kci=-1)]:
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                SourceMutation.model_validate(valid | changes)


if __name__ == "__main__":
    unittest.main()
