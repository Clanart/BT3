# Q690: NEAR deploy_token_by_deployer callback partial deployment rollback leaves live alias

## Question
Can an unprivileged attacker trigger a partial failure through `callback after cross-contract token deployment` such that `near/omni-bridge/src/lib.rs::deploy_token_by_deployer_callback` leaves behind either a live token without mappings or mappings without a usable token because of registers bridge storage for the newly deployed token on success and rolls back token mappings, decimals, and deployment sets on failure, violating `partial deployment failures must never leave a token address registered without a usable token contract or vice versa`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::deploy_token_by_deployer_callback`
- Entrypoint: `callback after cross-contract token deployment`
- Attacker controls: whether the token-deployer promise succeeded, deployed token id, mapped token address, and current storage state
- Exploit idea: Look for deployment flows that cross multiple contracts or callbacks before all state is committed.
- Invariant to test: partial deployment failures must never leave a token address registered without a usable token contract or vice versa
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Force each subcall to fail independently and assert that the resulting state is either fully rolled back or fully usable, never half-bound.
