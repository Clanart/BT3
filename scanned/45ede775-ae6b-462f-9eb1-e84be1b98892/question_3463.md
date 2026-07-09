# Q3463: NEAR deploy_token callback native versus wrapped registration confusion through cross-module drift

## Question
Can an unprivileged attacker use `proof callback for public `deploy_token`` with control over decoded `LogMetadata` result, chain kind, token address, metadata contents, and attached deposit and desynchronize `near/omni-bridge/src/lib.rs::deploy_token_callback` from the adjacent token-mapping and asset-identity logic that shares the same asset, nonce, proof subject, or mapping specifically in the `native versus wrapped registration confusion` attack class because checks the emitting factory, then calls `deploy_token_internal` using metadata from the validated message, violating `proof-bound metadata must not be replayable or malleable in a way that deploys the wrong token id, wrong decimals, or conflicting mappings`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::deploy_token_callback`
- Entrypoint: `proof callback for public `deploy_token``
- Attacker controls: decoded `LogMetadata` result, chain kind, token address, metadata contents, and attached deposit
- Exploit idea: Target vault-existence checks, deployed-token caches, origin-chain inference, and custom-minter registration. Focus on drift between this module and the adjacent token-mapping and asset-identity logic.
- Invariant to test: proof-bound metadata must not be replayable or malleable in a way that deploys the wrong token id, wrong decimals, or conflicting mappings
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Flip each classification predicate around existing mappings and assert that deployment and later settlement always preserve the same custody model. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs::deploy_token_callback` and the adjacent token-mapping and asset-identity logic after every branch.
