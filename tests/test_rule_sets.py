import unittest

import dcab_client
from dsit_parser import DSITBug, DSITFileStats, DSITReport


class RuleSetTests(unittest.TestCase):
    def test_load_rule_set_rule_ids_counts_and_filters_documents(self):
        cases = {
            "GJB-8114": (204, 204, 0),
            "GJB-5369": (138, 138, 0),
            "CWE-C": (130, 130, 0),
            "MISRA-2012": (156, 156, 0),
            "MISRA-2008": (228, 218, 10),
        }

        for rule_set, (raw_count, engine_count, document_count) in cases.items():
            with self.subTest(rule_set=rule_set):
                info = dcab_client.load_rule_set_rule_ids(rule_set)

                self.assertEqual(info["raw_count"], raw_count)
                self.assertEqual(info["selected_count"], engine_count)
                self.assertEqual(info["filtered_document_count"], document_count)
                self.assertEqual(len(info["rule_ids"]), engine_count)
                self.assertTrue(all(rule_id.endswith(":0") for rule_id in info["rule_ids"]))

    def test_format_engine_rule_id_adds_suffix_once(self):
        self.assertEqual(
            dcab_client.format_engine_rule_id("GJB-8114:R-1-1-1"),
            "GJB-8114:R-1-1-1:0",
        )
        self.assertEqual(
            dcab_client.format_engine_rule_id("GJB-8114:R-1-1-1:0"),
            "GJB-8114:R-1-1-1:0",
        )

    def test_filter_report_by_rule_set_keeps_checker_prefix_and_rebuilds_summary(self):
        report = DSITReport(
            report_id="request-id",
            project_name="MEMS",
            project_path="/tmp/project",
            files_stats=[
                DSITFileStats(
                    file_path="src/control.c",
                    bugs=[
                        DSITBug(
                            checker="GJB-8114:R-1-8-1:0",
                            file_path="src/control.c",
                            line=10,
                            column=1,
                            message="unreachable",
                            rule_id="GJB-R-1-8-1",
                            force="",
                            type_code="",
                            status="",
                            raw_severity="Required",
                        ),
                        DSITBug(
                            checker="MISRA-2012:R-2-1:0",
                            file_path="src/control.c",
                            line=30,
                            column=1,
                            message="unreachable",
                            rule_id="MISRA-2012:R-2-1:0",
                            force="",
                            type_code="outbreak_point",
                            status="",
                        ),
                    ],
                )
            ],
        )

        filtered, stats = dcab_client.filter_report_by_rule_set(report, "GJB-8114")

        self.assertEqual(stats["raw_result_rule_sets"], ["GJB-8114", "MISRA-2012"])
        self.assertEqual(stats["result_rule_sets"], ["GJB-8114"])
        self.assertEqual(stats["filtered_bug_count"], 1)
        self.assertEqual(filtered.total_bugs, 1)
        kept_bug = filtered.to_dict()["files_stats"][0]["bugs"][0]
        self.assertEqual(kept_bug["checker"], "GJB-8114:R-1-8-1:0")
        self.assertEqual(filtered.summary()["by_level"], {"Error": 1})

    def test_checker_prefix_drives_level_mapping_for_all_rule_kinds(self):
        cases = [
            ("CWE-C:R-563:0", "Error", "required", "Required"),
            ("MISRA-2012:R-2-1:0", "Error", "required", "Required"),
            ("MISRA-2008:A-1-0-1:0", "Warning", "advisory", "Advisory"),
            ("MISRA-2008:M-0-3-1:0", "Error", "required", "Required"),
            ("MISRA-2008:D-0-4-1:0", "Unknown", "unknown", "Unknown"),
        ]

        defects = []
        for index, (checker, _level, _severity, _label) in enumerate(cases, start=1):
            defects.append(
                {
                    "checker": checker,
                    "rule_id": checker,
                    "tracking_path_list": [
                        {
                            "file_path": f"/tmp/project/file{index}.c",
                            "location_start": {"line": index, "column": 1},
                            "type": "outbreak_point",
                        }
                    ],
                }
            )

        report = dcab_client.report_from_defect_list(
            defects,
            report_id="request-id",
            project_name="MEMS",
            project_path="/tmp/project",
        )
        bugs = report.summary()["bugs"]

        for bug, (checker, level, severity, label) in zip(bugs, cases):
            with self.subTest(checker=checker):
                self.assertEqual(bug["checker"], checker)
                self.assertEqual(bug["level"], level)
                self.assertEqual(bug["severity"], severity)
                self.assertEqual(bug["severity_label"], label)

        self.assertEqual(report.summary()["by_level"], {"Error": 3, "Warning": 1, "Unknown": 1})


if __name__ == "__main__":
    unittest.main()
