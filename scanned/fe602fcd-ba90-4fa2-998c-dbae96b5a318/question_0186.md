# Q186: NEAR deploy_token_by_deployer callback canonical token identity collision via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `callback after cross-contract token deployment` and then replay or reorder later callback or refund resolution so that `near/omni-bridge/src/lib.rs::deploy_token_by_deployer_callback` ends up accepting two inconsistent interpretations of the same economic event specifically around `canonical token identity collision` under registers bridge storage for the newly deployed token on success and rolls back token mappings, decimals, and deployment sets on failure, violating `partial deployment failures must never leave a token address registered without a usable token contract or vice versa`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::deploy_token_by_deployer_callback`
- Entrypoint: `callback after cross-contract token deployment`
- Attacker controls: whether the token-deployer promise succeeded, deployed token id, mapped token address, and current storage state
- Exploit idea: Target hashed token ids, deterministic synthetic addresses, PDA seeds, and address-to-token maps. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: partial deployment failures must never leave a token address registered without a usable token contract or vice versa
- Expected Immunefi impact: Balance manipulation
- Fast validation: Search for collisions and alias conditions and assert that two distinct remote assets cannot share one local token identity or mapping row. Then replay or reorder later callback or refund resolution and assert that the bridge still exposes only one valid economic outcome.
