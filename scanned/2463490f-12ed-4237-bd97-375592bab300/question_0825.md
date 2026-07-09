# Q825: NEAR factory map mutation assumptions final settlement and later fee claim can diverge

## Question
Can an unprivileged attacker drive `public proof-consuming flows after a valid source-chain event exists` so that `near/omni-bridge/src/lib.rs::factories usage in all proof callbacks` settles principal under one interpretation of amount or transfer id while fee claim later uses another because of uses a single configured factory per chain to authenticate proof-derived events across init, finalize, deploy, metadata, and fee claim flows, violating `source authentication must never accept a valid event from the wrong contract instance, retired factory, or mismatched chain domain`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::factories usage in all proof callbacks`
- Entrypoint: `public proof-consuming flows after a valid source-chain event exists`
- Attacker controls: chain kind, emitter address, and any state race across token deployment and finalization
- Exploit idea: Target differences between settle-time denormalization and claim-time recomputation of fee, dust, or relayer substitution.
- Invariant to test: source authentication must never accept a valid event from the wrong contract instance, retired factory, or mismatched chain domain
- Expected Immunefi impact: Balance manipulation
- Fast validation: Compare settled principal, stored transfer record, and fee-claim proof under edge amounts and assert that the three always reconstruct one consistent event.
