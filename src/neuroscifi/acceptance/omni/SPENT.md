# Spent withheld files opened as development cases

- **Decision:** the owner's, 2026-11-27 (WAVE3, AskUserQuestion label "Open spent cases, fix (Recommended)"); recorded
  in ADR-081.
- **Families:** `unknown`, `hostile` and `cross` only.
- **Opened on 2026-11-27 by lane FX3, development cases from then on:**
  - `withheld2_unknown_hostile.py` (second split: unknown and hostile, nothing else in it);
  - `withheld2_cross.py` (second split: cross);
  - `withheld_cross.py` (first split: cross).
- **Not edited:** the three files stay byte-identical, so their SHA-256 pins in `final.py` and the tests stay valid.
- **Still sealed:** `withheld_cases.py` holds all seven of lane Z's families of the first split (intake, honest,
  unknown, compound, hostile, made, transform), so it was not opened. `withheld_forms.py`, `withheld_learn.py`,
  `tests/acceptance/test_omni_withheld_cases.py` and every `withheld3*` file stay sealed too.
- **Who imports them:** besides `final.py`, only `scripts/omnimind_spent.py` imports the three opened files
  (`tests/acceptance/test_omni_final.py` checks this). That script never runs `final` and writes no ledger line.
- **What their numbers are now:** development evidence. The fixes of ADR-081 saw these cases, so a figure on them is
  a regression run, never a look.
