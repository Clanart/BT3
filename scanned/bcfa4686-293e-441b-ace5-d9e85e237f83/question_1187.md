# Q1187: NEAR deploy_token callback partial deployment rollback leaves live alias at boundary values

## Question
Can an unprivileged attacker trigger `proof callback for public `deploy_token`` with boundary-controlled inputs covering decimal caps, zero values, and normalization edges and make `near/omni-bridge/src/lib.rs::deploy_token_callback` violate `proof-bound metadata must not be replayable or malleable in a way that deploys the wrong token id, wrong decimals, or conflicting mappings` in the `partial deployment rollback leaves live alias` attack class because checks the emitting factory, then calls `deploy_token_internal` using metadata from the validated message becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::deploy_token_callback`
- Entrypoint: `proof callback for public `deploy_token``
- Attacker controls: decoded `LogMetadata` result, chain kind, token address, metadata contents, and attached deposit
- Exploit idea: Look for deployment flows that cross multiple contracts or callbacks before all state is committed. Concentrate on decimal caps, zero values, and normalization edges.
- Invariant to test: proof-bound metadata must not be replayable or malleable in a way that deploys the wrong token id, wrong decimals, or conflicting mappings
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Force each subcall to fail independently and assert that the resulting state is either fully rolled back or fully usable, never half-bound. Sweep boundary values for decimal caps, zero values, and normalization edges and assert that the same invariant holds at every edge.
