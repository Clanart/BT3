# Q1323: NEAR factory map mutation assumptions final settlement and later fee claim can diverge at boundary values

## Question
Can an unprivileged attacker trigger `public proof-consuming flows after a valid source-chain event exists` with boundary-controlled inputs covering zero-fee, fee-equals-amount, and near-overflow amount splits and make `near/omni-bridge/src/lib.rs::factories usage in all proof callbacks` violate `source authentication must never accept a valid event from the wrong contract instance, retired factory, or mismatched chain domain` in the `final settlement and later fee claim can diverge` attack class because uses a single configured factory per chain to authenticate proof-derived events across init, finalize, deploy, metadata, and fee claim flows becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::factories usage in all proof callbacks`
- Entrypoint: `public proof-consuming flows after a valid source-chain event exists`
- Attacker controls: chain kind, emitter address, and any state race across token deployment and finalization
- Exploit idea: Target differences between settle-time denormalization and claim-time recomputation of fee, dust, or relayer substitution. Concentrate on zero-fee, fee-equals-amount, and near-overflow amount splits.
- Invariant to test: source authentication must never accept a valid event from the wrong contract instance, retired factory, or mismatched chain domain
- Expected Immunefi impact: Balance manipulation
- Fast validation: Compare settled principal, stored transfer record, and fee-claim proof under edge amounts and assert that the three always reconstruct one consistent event. Sweep boundary values for zero-fee, fee-equals-amount, and near-overflow amount splits and assert that the same invariant holds at every edge.
