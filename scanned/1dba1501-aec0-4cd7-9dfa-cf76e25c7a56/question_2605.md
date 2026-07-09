# Q2605: NEAR deploy_token callback same remote asset deployable via multiple proof paths

## Question
Can an unprivileged attacker use `proof callback for public `deploy_token`` to deploy or bind the same remote asset through a second path because `near/omni-bridge/src/lib.rs::deploy_token_callback` authenticates checks the emitting factory, then calls `deploy_token_internal` using metadata from the validated message differently than another deploy path, violating `proof-bound metadata must not be replayable or malleable in a way that deploys the wrong token id, wrong decimals, or conflicting mappings`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::deploy_token_callback`
- Entrypoint: `proof callback for public `deploy_token``
- Attacker controls: decoded `LogMetadata` result, chain kind, token address, metadata contents, and attached deposit
- Exploit idea: Compare metadata-based deployment, deploy-token binding, native-token deployment, and chain-specific extension paths.
- Invariant to test: proof-bound metadata must not be replayable or malleable in a way that deploys the wrong token id, wrong decimals, or conflicting mappings
- Expected Immunefi impact: Balance manipulation
- Fast validation: Attempt the same remote asset through every supported path and assert that the bridge converges to one canonical local representation.
