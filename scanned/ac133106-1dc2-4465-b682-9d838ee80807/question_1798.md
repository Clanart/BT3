# Q1798: NEAR add_token mapping writer native versus wrapped registration confusion through cross-module drift

## Question
Can an unprivileged attacker use `public deploy/bind flows through internal mapping writes` with control over token id, foreign token address, decimals, and origin decimals and desynchronize `near/omni-bridge/src/lib.rs::add_token` from the adjacent token-mapping and asset-identity logic that shares the same asset, nonce, proof subject, or mapping specifically in the `native versus wrapped registration confusion` attack class because writes the core `token_id_to_address`, `token_address_to_id`, and `token_decimals` state that every bridge path later trusts, violating `mapping writes must never permit duplicate foreign addresses, duplicate Near token ids, or decimal records that disagree with the actual wrapped token`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::add_token`
- Entrypoint: `public deploy/bind flows through internal mapping writes`
- Attacker controls: token id, foreign token address, decimals, and origin decimals
- Exploit idea: Target vault-existence checks, deployed-token caches, origin-chain inference, and custom-minter registration. Focus on drift between this module and the adjacent token-mapping and asset-identity logic.
- Invariant to test: mapping writes must never permit duplicate foreign addresses, duplicate Near token ids, or decimal records that disagree with the actual wrapped token
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Flip each classification predicate around existing mappings and assert that deployment and later settlement always preserve the same custody model. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs::add_token` and the adjacent token-mapping and asset-identity logic after every branch.
