# Q3562: NEAR add_token mapping writer asset mapping drifts away from actual token semantics through cross-module drift

## Question
Can an unprivileged attacker use `public deploy/bind flows through internal mapping writes` with control over token id, foreign token address, decimals, and origin decimals and desynchronize `near/omni-bridge/src/lib.rs::add_token` from the adjacent token-mapping and asset-identity logic that shares the same asset, nonce, proof subject, or mapping specifically in the `asset mapping drifts away from actual token semantics` attack class because writes the core `token_id_to_address`, `token_address_to_id`, and `token_decimals` state that every bridge path later trusts, violating `mapping writes must never permit duplicate foreign addresses, duplicate Near token ids, or decimal records that disagree with the actual wrapped token`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::add_token`
- Entrypoint: `public deploy/bind flows through internal mapping writes`
- Attacker controls: token id, foreign token address, decimals, and origin decimals
- Exploit idea: Target upgrades, migration swaps, fake bridge-controlled tokens, and deploy callbacks. Focus on drift between this module and the adjacent token-mapping and asset-identity logic.
- Invariant to test: mapping writes must never permit duplicate foreign addresses, duplicate Near token ids, or decimal records that disagree with the actual wrapped token
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Change token semantics around an existing mapping and assert that the bridge does not keep treating the token as a valid canonical representation. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs::add_token` and the adjacent token-mapping and asset-identity logic after every branch.
