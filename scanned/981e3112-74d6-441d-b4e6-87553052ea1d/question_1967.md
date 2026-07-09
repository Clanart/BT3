# Q1967: NEAR factory map mutation assumptions shared proof response reused across entrypoints at boundary values

## Question
Can an unprivileged attacker trigger `public proof-consuming flows after a valid source-chain event exists` with boundary-controlled inputs covering zero-fee, fee-equals-amount, and near-overflow amount splits and make `near/omni-bridge/src/lib.rs::factories usage in all proof callbacks` violate `source authentication must never accept a valid event from the wrong contract instance, retired factory, or mismatched chain domain` in the `shared proof response reused across entrypoints` attack class because uses a single configured factory per chain to authenticate proof-derived events across init, finalize, deploy, metadata, and fee claim flows becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::factories usage in all proof callbacks`
- Entrypoint: `public proof-consuming flows after a valid source-chain event exists`
- Attacker controls: chain kind, emitter address, and any state race across token deployment and finalization
- Exploit idea: Attack systems where one verifier contract serves deploy, finalize, metadata, and fee-claim flows with a shared result type. Concentrate on zero-fee, fee-equals-amount, and near-overflow amount splits.
- Invariant to test: source authentication must never accept a valid event from the wrong contract instance, retired factory, or mismatched chain domain
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Attempt to route accepted verifier outputs into every public proof consumer and assert that each entrypoint only accepts its intended result variant and source semantics. Sweep boundary values for zero-fee, fee-equals-amount, and near-overflow amount splits and assert that the same invariant holds at every edge.
