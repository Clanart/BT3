# Q3046: NEAR deploy_token callback same remote asset deployable via multiple proof paths at boundary values

## Question
Can an unprivileged attacker trigger `proof callback for public `deploy_token`` with boundary-controlled inputs covering decimal caps, zero values, and normalization edges and make `near/omni-bridge/src/lib.rs::deploy_token_callback` violate `proof-bound metadata must not be replayable or malleable in a way that deploys the wrong token id, wrong decimals, or conflicting mappings` in the `same remote asset deployable via multiple proof paths` attack class because checks the emitting factory, then calls `deploy_token_internal` using metadata from the validated message becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::deploy_token_callback`
- Entrypoint: `proof callback for public `deploy_token``
- Attacker controls: decoded `LogMetadata` result, chain kind, token address, metadata contents, and attached deposit
- Exploit idea: Compare metadata-based deployment, deploy-token binding, native-token deployment, and chain-specific extension paths. Concentrate on decimal caps, zero values, and normalization edges.
- Invariant to test: proof-bound metadata must not be replayable or malleable in a way that deploys the wrong token id, wrong decimals, or conflicting mappings
- Expected Immunefi impact: Balance manipulation
- Fast validation: Attempt the same remote asset through every supported path and assert that the bridge converges to one canonical local representation. Sweep boundary values for decimal caps, zero values, and normalization edges and assert that the same invariant holds at every edge.
