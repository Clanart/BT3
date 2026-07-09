# Q3193: NEAR deploy_token callback native versus wrapped registration confusion

## Question
Can an unprivileged attacker reach `proof callback for public `deploy_token`` and make `near/omni-bridge/src/lib.rs::deploy_token_callback` treat a wrapped asset as native or a native asset as wrapped because of checks the emitting factory, then calls `deploy_token_internal` using metadata from the validated message, violating `proof-bound metadata must not be replayable or malleable in a way that deploys the wrong token id, wrong decimals, or conflicting mappings`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::deploy_token_callback`
- Entrypoint: `proof callback for public `deploy_token``
- Attacker controls: decoded `LogMetadata` result, chain kind, token address, metadata contents, and attached deposit
- Exploit idea: Target vault-existence checks, deployed-token caches, origin-chain inference, and custom-minter registration.
- Invariant to test: proof-bound metadata must not be replayable or malleable in a way that deploys the wrong token id, wrong decimals, or conflicting mappings
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Flip each classification predicate around existing mappings and assert that deployment and later settlement always preserve the same custody model.
