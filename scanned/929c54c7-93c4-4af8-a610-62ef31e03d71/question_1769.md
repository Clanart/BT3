# Q1769: NEAR foreign/native token mapping lookup native versus wrapped registration confusion through cross-module drift

## Question
Can an unprivileged attacker use `public multi-hop settlement flows that map tokens across chains` with control over source address, target chain, and any mapping state created by deploy/bind flows and desynchronize `near/omni-bridge/src/lib.rs::get_bridged_token` from the adjacent token-mapping and asset-identity logic that shares the same asset, nonce, proof subject, or mapping specifically in the `native versus wrapped registration confusion` attack class because resolves a token across Near and foreign chains using token-id and address maps that span multiple bridge adapters, violating `multi-chain mapping lookup must never return a different asset than the one collateral actually backs, especially in foreign-to-foreign forwarding`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::get_bridged_token`
- Entrypoint: `public multi-hop settlement flows that map tokens across chains`
- Attacker controls: source address, target chain, and any mapping state created by deploy/bind flows
- Exploit idea: Target vault-existence checks, deployed-token caches, origin-chain inference, and custom-minter registration. Focus on drift between this module and the adjacent token-mapping and asset-identity logic.
- Invariant to test: multi-chain mapping lookup must never return a different asset than the one collateral actually backs, especially in foreign-to-foreign forwarding
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Flip each classification predicate around existing mappings and assert that deployment and later settlement always preserve the same custody model. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs::get_bridged_token` and the adjacent token-mapping and asset-identity logic after every branch.
