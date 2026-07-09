# Q1806: NEAR factory map mutation assumptions shared proof response reused across entrypoints through cross-module drift

## Question
Can an unprivileged attacker use `public proof-consuming flows after a valid source-chain event exists` with control over chain kind, emitter address, and any state race across token deployment and finalization and desynchronize `near/omni-bridge/src/lib.rs::factories usage in all proof callbacks` from the adjacent token-mapping and asset-identity logic that shares the same asset, nonce, proof subject, or mapping specifically in the `shared proof response reused across entrypoints` attack class because uses a single configured factory per chain to authenticate proof-derived events across init, finalize, deploy, metadata, and fee claim flows, violating `source authentication must never accept a valid event from the wrong contract instance, retired factory, or mismatched chain domain`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::factories usage in all proof callbacks`
- Entrypoint: `public proof-consuming flows after a valid source-chain event exists`
- Attacker controls: chain kind, emitter address, and any state race across token deployment and finalization
- Exploit idea: Attack systems where one verifier contract serves deploy, finalize, metadata, and fee-claim flows with a shared result type. Focus on drift between this module and the adjacent token-mapping and asset-identity logic.
- Invariant to test: source authentication must never accept a valid event from the wrong contract instance, retired factory, or mismatched chain domain
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Attempt to route accepted verifier outputs into every public proof consumer and assert that each entrypoint only accepts its intended result variant and source semantics. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs::factories usage in all proof callbacks` and the adjacent token-mapping and asset-identity logic after every branch.
