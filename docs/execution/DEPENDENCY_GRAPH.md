# Dependency graph

```mermaid
graph TD
  T01[T0.1 discovery] --> T02[T0.2 architecture + contracts]
  T01 --> T11[T1.1 package + CI]
  T11 --> T12[T1.2 schema/store/rules/CLI seam]
  T02 --> T12
  T12 --> T2A[T2.A evidence/beliefs/executive]
  T12 --> T2B[T2.B graphs/recall]
  T12 --> T2C[T2.C memory/governance/checkpoints]
  T11 --> T2D[T2.D Pocket review/e2e/a11y]
  T2B -. dataset cascade test .-> T2C
  T2A --> T31[T3.1 service facade]
  T2B --> T31
  T2C --> T31
  T31 --> T32[T3.2 console]
  T31 --> T33[T3.3 acceptance scenarios]
  T2A --> T34[T3.4 bench/doctor]
  T2B --> T35[T3.5 journey tests]
  T2C --> T35
  T32 --> T41[T4.1 UX/a11y pass]
  T2D --> T41
  T33 --> T51[T5.1 independent review]
  T35 --> T51
  T41 --> T51
  T51 --> T52[T5.2 remediation]
  T52 --> T61[T6.1 ops docs/units]
  T52 --> T62[T6.2 verification + completion report]
  T61 --> T62
```

**Critical path:** T0.1 → T1.1 → T1.2 → T2.A (the highest-risk inference work) → T3.1 → T3.3 → T5.1 → T5.2 → T6.2.

**Parallel windows**
- Wave 2: T2.A, T2.B, T2.C and T2.D run concurrently with disjoint files. Only T2.C's dataset-cascade test depends on T2.B's `invalidate_dependents`, and that dependency is fixed by the contract in API_CONTRACTS §3.
- Wave 3: T3.2, T3.3, T3.4 and T3.5 run concurrently once the facade exists.
