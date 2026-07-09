# Q1021: NEAR deploy_token callback partial deployment rollback leaves live alias through cross-module drift

## Question
Can an unprivileged attacker use `proof callback for public `deploy_token`` with control over decoded `LogMetadata` result, chain kind, token address, metadata contents, and attached deposit and desynchronize `near/omni-bridge/src/lib.rs::deploy_token_callback` from the adjacent token-mapping and asset-identity logic that shares the same asset, nonce, proof subject, or mapping specifically in the `partial deployment rollback leaves live alias` attack class because checks the emitting factory, then calls `deploy_token_internal` using metadata from the validated message, violating `proof-bound metadata must not be replayable or malleable in a way that deploys the wrong token id, wrong decimals, or conflicting mappings`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::deploy_token_callback`
- Entrypoint: `proof callback for public `deploy_token``
- Attacker controls: decoded `LogMetadata` result, chain kind, token address, metadata contents, and attached deposit
- Exploit idea: Look for deployment flows that cross multiple contracts or callbacks before all state is committed. Focus on drift between this module and the adjacent token-mapping and asset-identity logic.
- Invariant to test: proof-bound metadata must not be replayable or malleable in a way that deploys the wrong token id, wrong decimals, or conflicting mappings
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Force each subcall to fail independently and assert that the resulting state is either fully rolled back or fully usable, never half-bound. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs::deploy_token_callback` and the adjacent token-mapping and asset-identity logic after every branch.
