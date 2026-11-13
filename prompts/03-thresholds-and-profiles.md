# Prompt 3: thresholds and test profiles

Use your defaults (availability ≥ 95%, TTFF ≤ 60 s, HDOP p95 ≤ 2.0, CEP95 ≤ 5 m, 0 integrity errors), each overridable by a CLI flag. Also:
- Allow loading thresholds from a small YAML "test profile" file, and include two example profiles: "standard-gnss" (these defaults) and "high-precision" with stricter values. Mark both as illustrative examples, not taken from any product datasheet.
- In the report, print which profile and thresholds were used next to each PASS/FAIL, so a reader can see the criteria.
- Don't attribute any threshold to a real company or product.
Then continue with step 6.
