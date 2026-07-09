# Q2304: NEAR bind_token callback same remote asset deployable via multiple proof paths through cross-module drift

## Question
Can an unprivileged attacker use `proof callback for public `bind_token`` with control over decoded deploy-token message, chain kind, foreign token address, decimals, origin decimals, and attached deposit and desynchronize `near/omni-bridge/src/lib.rs::bind_token_callback` from the adjacent token-mapping and asset-identity logic that shares the same asset, nonce, proof subject, or mapping specifically in the `same remote asset deployable via multiple proof paths` attack class because adds the token mapping, initializes `locked_tokens` for the foreign asset, measures storage delta, and requires sufficient attached deposit before emitting the bind event, violating `a token binding must not be replayable or partially writable in a way that creates duplicate mappings, inconsistent lock rows, or underfunded state`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::bind_token_callback`
- Entrypoint: `proof callback for public `bind_token``
- Attacker controls: decoded deploy-token message, chain kind, foreign token address, decimals, origin decimals, and attached deposit
- Exploit idea: Compare metadata-based deployment, deploy-token binding, native-token deployment, and chain-specific extension paths. Focus on drift between this module and the adjacent token-mapping and asset-identity logic.
- Invariant to test: a token binding must not be replayable or partially writable in a way that creates duplicate mappings, inconsistent lock rows, or underfunded state
- Expected Immunefi impact: Balance manipulation
- Fast validation: Attempt the same remote asset through every supported path and assert that the bridge converges to one canonical local representation. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs::bind_token_callback` and the adjacent token-mapping and asset-identity logic after every branch.
