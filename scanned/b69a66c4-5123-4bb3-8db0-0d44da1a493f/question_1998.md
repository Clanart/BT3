# Q1998: NEAR deploy_token_by_deployer callback fake bridge-controlled token accepted as canonical

## Question
Can an unprivileged attacker use `callback after cross-contract token deployment` to register or settle against a token that only looks bridge-controlled because `near/omni-bridge/src/lib.rs::deploy_token_by_deployer_callback` relies on registers bridge storage for the newly deployed token on success and rolls back token mappings, decimals, and deployment sets on failure, violating `partial deployment failures must never leave a token address registered without a usable token contract or vice versa`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::deploy_token_by_deployer_callback`
- Entrypoint: `callback after cross-contract token deployment`
- Attacker controls: whether the token-deployer promise succeeded, deployed token id, mapped token address, and current storage state
- Exploit idea: Target checks that only inspect mint authority, owner, or one mapping row without proving the full asset identity.
- Invariant to test: partial deployment failures must never leave a token address registered without a usable token contract or vice versa
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Construct plausible fake bridge-controlled assets and assert that deployment, settlement, and forwarding reject them unless they are the canonical mapping for that remote asset.
