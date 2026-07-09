# Q2752: NEAR deploy_token callback same remote asset deployable via multiple proof paths via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `proof callback for public `deploy_token`` and then replay or reorder later callback or refund resolution so that `near/omni-bridge/src/lib.rs::deploy_token_callback` ends up accepting two inconsistent interpretations of the same economic event specifically around `same remote asset deployable via multiple proof paths` under checks the emitting factory, then calls `deploy_token_internal` using metadata from the validated message, violating `proof-bound metadata must not be replayable or malleable in a way that deploys the wrong token id, wrong decimals, or conflicting mappings`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::deploy_token_callback`
- Entrypoint: `proof callback for public `deploy_token``
- Attacker controls: decoded `LogMetadata` result, chain kind, token address, metadata contents, and attached deposit
- Exploit idea: Compare metadata-based deployment, deploy-token binding, native-token deployment, and chain-specific extension paths. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: proof-bound metadata must not be replayable or malleable in a way that deploys the wrong token id, wrong decimals, or conflicting mappings
- Expected Immunefi impact: Balance manipulation
- Fast validation: Attempt the same remote asset through every supported path and assert that the bridge converges to one canonical local representation. Then replay or reorder later callback or refund resolution and assert that the bridge still exposes only one valid economic outcome.
