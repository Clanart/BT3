# Q155: NEAR factory map mutation assumptions recipient or fee-recipient rebinding

## Question
Can an unprivileged attacker submit data through `public proof-consuming flows after a valid source-chain event exists` that makes `near/omni-bridge/src/lib.rs::factories usage in all proof callbacks` settle principal to one party but authorize fee claim or callback routing for another due to uses a single configured factory per chain to authenticate proof-derived events across init, finalize, deploy, metadata, and fee claim flows, violating `source authentication must never accept a valid event from the wrong contract instance, retired factory, or mismatched chain domain`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::factories usage in all proof callbacks`
- Entrypoint: `public proof-consuming flows after a valid source-chain event exists`
- Attacker controls: chain kind, emitter address, and any state race across token deployment and finalization
- Exploit idea: Exploit optional fee-recipient fields, fast-transfer relayer substitution, or predecessor-captured identities.
- Invariant to test: source authentication must never accept a valid event from the wrong contract instance, retired factory, or mismatched chain domain
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Build pairs of proofs/messages that vary only in recipient-oriented fields and assert that settlement, fee claim, and event emission stay bound to one tuple.
