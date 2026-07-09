# Q2301: NEAR deploy_token callback malicious metadata manufactures a bridge identity through cross-module drift

## Question
Can an unprivileged attacker use `proof callback for public `deploy_token`` with control over decoded `LogMetadata` result, chain kind, token address, metadata contents, and attached deposit and desynchronize `near/omni-bridge/src/lib.rs::deploy_token_callback` from the adjacent token-mapping and asset-identity logic that shares the same asset, nonce, proof subject, or mapping specifically in the `malicious metadata manufactures a bridge identity` attack class because checks the emitting factory, then calls `deploy_token_internal` using metadata from the validated message, violating `proof-bound metadata must not be replayable or malleable in a way that deploys the wrong token id, wrong decimals, or conflicting mappings`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::deploy_token_callback`
- Entrypoint: `proof callback for public `deploy_token``
- Attacker controls: decoded `LogMetadata` result, chain kind, token address, metadata contents, and attached deposit
- Exploit idea: Exploit arbitrary token metadata calls, old/new ABI switching, or malformed strings in metadata proofs. Focus on drift between this module and the adjacent token-mapping and asset-identity logic.
- Invariant to test: proof-bound metadata must not be replayable or malleable in a way that deploys the wrong token id, wrong decimals, or conflicting mappings
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Publish or prove pathological metadata values and assert that downstream deployment and mapping logic still binds to the right remote asset and decimals. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs::deploy_token_callback` and the adjacent token-mapping and asset-identity logic after every branch.
