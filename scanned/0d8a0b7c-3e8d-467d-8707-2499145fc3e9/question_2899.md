# Q2899: NEAR deploy_token callback same remote asset deployable via multiple proof paths through cross-module drift

## Question
Can an unprivileged attacker use `proof callback for public `deploy_token`` with control over decoded `LogMetadata` result, chain kind, token address, metadata contents, and attached deposit and desynchronize `near/omni-bridge/src/lib.rs::deploy_token_callback` from the adjacent token-mapping and asset-identity logic that shares the same asset, nonce, proof subject, or mapping specifically in the `same remote asset deployable via multiple proof paths` attack class because checks the emitting factory, then calls `deploy_token_internal` using metadata from the validated message, violating `proof-bound metadata must not be replayable or malleable in a way that deploys the wrong token id, wrong decimals, or conflicting mappings`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::deploy_token_callback`
- Entrypoint: `proof callback for public `deploy_token``
- Attacker controls: decoded `LogMetadata` result, chain kind, token address, metadata contents, and attached deposit
- Exploit idea: Compare metadata-based deployment, deploy-token binding, native-token deployment, and chain-specific extension paths. Focus on drift between this module and the adjacent token-mapping and asset-identity logic.
- Invariant to test: proof-bound metadata must not be replayable or malleable in a way that deploys the wrong token id, wrong decimals, or conflicting mappings
- Expected Immunefi impact: Balance manipulation
- Fast validation: Attempt the same remote asset through every supported path and assert that the bridge converges to one canonical local representation. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs::deploy_token_callback` and the adjacent token-mapping and asset-identity logic after every branch.
