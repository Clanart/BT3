# Q689: NEAR deploy_token callback partial deployment rollback leaves live alias

## Question
Can an unprivileged attacker trigger a partial failure through `proof callback for public `deploy_token`` such that `near/omni-bridge/src/lib.rs::deploy_token_callback` leaves behind either a live token without mappings or mappings without a usable token because of checks the emitting factory, then calls `deploy_token_internal` using metadata from the validated message, violating `proof-bound metadata must not be replayable or malleable in a way that deploys the wrong token id, wrong decimals, or conflicting mappings`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::deploy_token_callback`
- Entrypoint: `proof callback for public `deploy_token``
- Attacker controls: decoded `LogMetadata` result, chain kind, token address, metadata contents, and attached deposit
- Exploit idea: Look for deployment flows that cross multiple contracts or callbacks before all state is committed.
- Invariant to test: proof-bound metadata must not be replayable or malleable in a way that deploys the wrong token id, wrong decimals, or conflicting mappings
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Force each subcall to fail independently and assert that the resulting state is either fully rolled back or fully usable, never half-bound.
