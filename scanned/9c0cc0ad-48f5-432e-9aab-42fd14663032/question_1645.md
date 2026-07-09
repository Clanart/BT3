# Q1645: NEAR factory map mutation assumptions shared proof response reused across entrypoints via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public proof-consuming flows after a valid source-chain event exists` and then replay or reorder later fee-claim proof submission so that `near/omni-bridge/src/lib.rs::factories usage in all proof callbacks` ends up accepting two inconsistent interpretations of the same economic event specifically around `shared proof response reused across entrypoints` under uses a single configured factory per chain to authenticate proof-derived events across init, finalize, deploy, metadata, and fee claim flows, violating `source authentication must never accept a valid event from the wrong contract instance, retired factory, or mismatched chain domain`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::factories usage in all proof callbacks`
- Entrypoint: `public proof-consuming flows after a valid source-chain event exists`
- Attacker controls: chain kind, emitter address, and any state race across token deployment and finalization
- Exploit idea: Attack systems where one verifier contract serves deploy, finalize, metadata, and fee-claim flows with a shared result type. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: source authentication must never accept a valid event from the wrong contract instance, retired factory, or mismatched chain domain
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Attempt to route accepted verifier outputs into every public proof consumer and assert that each entrypoint only accepts its intended result variant and source semantics. Then replay or reorder later fee-claim proof submission and assert that the bridge still exposes only one valid economic outcome.
