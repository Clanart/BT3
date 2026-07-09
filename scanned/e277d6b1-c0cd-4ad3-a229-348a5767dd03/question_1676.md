# Q1676: NEAR deploy_token_by_deployer callback native versus wrapped registration confusion through cross-module drift

## Question
Can an unprivileged attacker use `callback after cross-contract token deployment` with control over whether the token-deployer promise succeeded, deployed token id, mapped token address, and current storage state and desynchronize `near/omni-bridge/src/lib.rs::deploy_token_by_deployer_callback` from the adjacent token-mapping and asset-identity logic that shares the same asset, nonce, proof subject, or mapping specifically in the `native versus wrapped registration confusion` attack class because registers bridge storage for the newly deployed token on success and rolls back token mappings, decimals, and deployment sets on failure, violating `partial deployment failures must never leave a token address registered without a usable token contract or vice versa`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::deploy_token_by_deployer_callback`
- Entrypoint: `callback after cross-contract token deployment`
- Attacker controls: whether the token-deployer promise succeeded, deployed token id, mapped token address, and current storage state
- Exploit idea: Target vault-existence checks, deployed-token caches, origin-chain inference, and custom-minter registration. Focus on drift between this module and the adjacent token-mapping and asset-identity logic.
- Invariant to test: partial deployment failures must never leave a token address registered without a usable token contract or vice versa
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Flip each classification predicate around existing mappings and assert that deployment and later settlement always preserve the same custody model. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs::deploy_token_by_deployer_callback` and the adjacent token-mapping and asset-identity logic after every branch.
