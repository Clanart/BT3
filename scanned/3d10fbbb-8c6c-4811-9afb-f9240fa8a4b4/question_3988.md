# Q3988: NEAR bind_token callback storage quote underestimates live state through cross-module drift

## Question
Can an unprivileged attacker use `proof callback for public `bind_token`` with control over decoded deploy-token message, chain kind, foreign token address, decimals, origin decimals, and attached deposit and desynchronize `near/omni-bridge/src/lib.rs::bind_token_callback` from the adjacent token-mapping and asset-identity logic that shares the same asset, nonce, proof subject, or mapping specifically in the `storage quote underestimates live state` attack class because adds the token mapping, initializes `locked_tokens` for the foreign asset, measures storage delta, and requires sufficient attached deposit before emitting the bind event, violating `a token binding must not be replayable or partially writable in a way that creates duplicate mappings, inconsistent lock rows, or underfunded state`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::bind_token_callback`
- Entrypoint: `proof callback for public `bind_token``
- Attacker controls: decoded deploy-token message, chain kind, foreign token address, decimals, origin decimals, and attached deposit
- Exploit idea: Target helper functions that quote storage for pending transfers, finalization records, fast transfers, binds, or deployments. Focus on drift between this module and the adjacent token-mapping and asset-identity logic.
- Invariant to test: a token binding must not be replayable or partially writable in a way that creates duplicate mappings, inconsistent lock rows, or underfunded state
- Expected Immunefi impact: Permanent freezing of funds
- Fast validation: Measure storage usage across maximal inputs and assert that quoted requirements always exceed or equal the true post-state footprint. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs::bind_token_callback` and the adjacent token-mapping and asset-identity logic after every branch.
