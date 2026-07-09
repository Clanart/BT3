# Q2900: NEAR deploy_token_by_deployer callback storage quote underestimates live state through cross-module drift

## Question
Can an unprivileged attacker use `callback after cross-contract token deployment` with control over whether the token-deployer promise succeeded, deployed token id, mapped token address, and current storage state and desynchronize `near/omni-bridge/src/lib.rs::deploy_token_by_deployer_callback` from the adjacent token-mapping and asset-identity logic that shares the same asset, nonce, proof subject, or mapping specifically in the `storage quote underestimates live state` attack class because registers bridge storage for the newly deployed token on success and rolls back token mappings, decimals, and deployment sets on failure, violating `partial deployment failures must never leave a token address registered without a usable token contract or vice versa`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::deploy_token_by_deployer_callback`
- Entrypoint: `callback after cross-contract token deployment`
- Attacker controls: whether the token-deployer promise succeeded, deployed token id, mapped token address, and current storage state
- Exploit idea: Target helper functions that quote storage for pending transfers, finalization records, fast transfers, binds, or deployments. Focus on drift between this module and the adjacent token-mapping and asset-identity logic.
- Invariant to test: partial deployment failures must never leave a token address registered without a usable token contract or vice versa
- Expected Immunefi impact: Permanent freezing of funds
- Fast validation: Measure storage usage across maximal inputs and assert that quoted requirements always exceed or equal the true post-state footprint. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs::deploy_token_by_deployer_callback` and the adjacent token-mapping and asset-identity logic after every branch.
