# Q354: NEAR deploy_token_by_deployer callback canonical token identity collision through cross-module drift

## Question
Can an unprivileged attacker use `callback after cross-contract token deployment` with control over whether the token-deployer promise succeeded, deployed token id, mapped token address, and current storage state and desynchronize `near/omni-bridge/src/lib.rs::deploy_token_by_deployer_callback` from the adjacent token-mapping and asset-identity logic that shares the same asset, nonce, proof subject, or mapping specifically in the `canonical token identity collision` attack class because registers bridge storage for the newly deployed token on success and rolls back token mappings, decimals, and deployment sets on failure, violating `partial deployment failures must never leave a token address registered without a usable token contract or vice versa`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::deploy_token_by_deployer_callback`
- Entrypoint: `callback after cross-contract token deployment`
- Attacker controls: whether the token-deployer promise succeeded, deployed token id, mapped token address, and current storage state
- Exploit idea: Target hashed token ids, deterministic synthetic addresses, PDA seeds, and address-to-token maps. Focus on drift between this module and the adjacent token-mapping and asset-identity logic.
- Invariant to test: partial deployment failures must never leave a token address registered without a usable token contract or vice versa
- Expected Immunefi impact: Balance manipulation
- Fast validation: Search for collisions and alias conditions and assert that two distinct remote assets cannot share one local token identity or mapping row. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs::deploy_token_by_deployer_callback` and the adjacent token-mapping and asset-identity logic after every branch.
