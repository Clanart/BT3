# Q3194: NEAR deploy_token_by_deployer callback storage withdrawal escapes live liabilities

## Question
Can an unprivileged attacker call `callback after cross-contract token deployment` and make `near/omni-bridge/src/lib.rs::deploy_token_by_deployer_callback` release storage funds that still back unresolved bridge state because of registers bridge storage for the newly deployed token on success and rolls back token mappings, decimals, and deployment sets on failure, violating `partial deployment failures must never leave a token address registered without a usable token contract or vice versa`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::deploy_token_by_deployer_callback`
- Entrypoint: `callback after cross-contract token deployment`
- Attacker controls: whether the token-deployer promise succeeded, deployed token id, mapped token address, and current storage state
- Exploit idea: Look for withdrawals and unregister paths that do not fully account for pending, finalized, or fast-transfer records.
- Invariant to test: partial deployment failures must never leave a token address registered without a usable token contract or vice versa
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Open live bridge state, withdraw aggressively, and assert that storage balances cannot fall below the reserved amount implied by that live state.
