# Q3860: NEAR deploy_token_by_deployer callback refund goes to wrong logical owner via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `callback after cross-contract token deployment` and then replay or reorder later callback or refund resolution so that `near/omni-bridge/src/lib.rs::deploy_token_by_deployer_callback` ends up accepting two inconsistent interpretations of the same economic event specifically around `refund goes to wrong logical owner` under registers bridge storage for the newly deployed token on success and rolls back token mappings, decimals, and deployment sets on failure, violating `partial deployment failures must never leave a token address registered without a usable token contract or vice versa`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::deploy_token_by_deployer_callback`
- Entrypoint: `callback after cross-contract token deployment`
- Attacker controls: whether the token-deployer promise succeeded, deployed token id, mapped token address, and current storage state
- Exploit idea: Target asynchronous state removal, carried predecessor identities, and stored owner fields. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: partial deployment failures must never leave a token address registered without a usable token contract or vice versa
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Trace refund ownership across success and failure branches and assert that only the original funder can recover the reserved storage. Then replay or reorder later callback or refund resolution and assert that the bridge still exposes only one valid economic outcome.
