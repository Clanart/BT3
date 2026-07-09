# Q185: NEAR deploy_token callback canonical token identity collision via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `proof callback for public `deploy_token`` and then replay or reorder later callback or refund resolution so that `near/omni-bridge/src/lib.rs::deploy_token_callback` ends up accepting two inconsistent interpretations of the same economic event specifically around `canonical token identity collision` under checks the emitting factory, then calls `deploy_token_internal` using metadata from the validated message, violating `proof-bound metadata must not be replayable or malleable in a way that deploys the wrong token id, wrong decimals, or conflicting mappings`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::deploy_token_callback`
- Entrypoint: `proof callback for public `deploy_token``
- Attacker controls: decoded `LogMetadata` result, chain kind, token address, metadata contents, and attached deposit
- Exploit idea: Target hashed token ids, deterministic synthetic addresses, PDA seeds, and address-to-token maps. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: proof-bound metadata must not be replayable or malleable in a way that deploys the wrong token id, wrong decimals, or conflicting mappings
- Expected Immunefi impact: Balance manipulation
- Fast validation: Search for collisions and alias conditions and assert that two distinct remote assets cannot share one local token identity or mapping row. Then replay or reorder later callback or refund resolution and assert that the bridge still exposes only one valid economic outcome.
