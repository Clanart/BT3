# Q3599: NEAR deploy_token_by_deployer callback storage withdrawal escapes live liabilities at boundary values

## Question
Can an unprivileged attacker trigger `callback after cross-contract token deployment` with boundary-controlled inputs covering minimal deposits, maximal storage payloads, and withdrawal edges and make `near/omni-bridge/src/lib.rs::deploy_token_by_deployer_callback` violate `partial deployment failures must never leave a token address registered without a usable token contract or vice versa` in the `storage withdrawal escapes live liabilities` attack class because registers bridge storage for the newly deployed token on success and rolls back token mappings, decimals, and deployment sets on failure becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::deploy_token_by_deployer_callback`
- Entrypoint: `callback after cross-contract token deployment`
- Attacker controls: whether the token-deployer promise succeeded, deployed token id, mapped token address, and current storage state
- Exploit idea: Look for withdrawals and unregister paths that do not fully account for pending, finalized, or fast-transfer records. Concentrate on minimal deposits, maximal storage payloads, and withdrawal edges.
- Invariant to test: partial deployment failures must never leave a token address registered without a usable token contract or vice versa
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Open live bridge state, withdraw aggressively, and assert that storage balances cannot fall below the reserved amount implied by that live state. Sweep boundary values for minimal deposits, maximal storage payloads, and withdrawal edges and assert that the same invariant holds at every edge.
