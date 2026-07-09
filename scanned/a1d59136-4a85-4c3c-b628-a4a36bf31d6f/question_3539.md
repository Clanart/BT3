# Q3539: NEAR foreign/native token mapping lookup asset mapping drifts away from actual token semantics through cross-module drift

## Question
Can an unprivileged attacker use `public multi-hop settlement flows that map tokens across chains` with control over source address, target chain, and any mapping state created by deploy/bind flows and desynchronize `near/omni-bridge/src/lib.rs::get_bridged_token` from the adjacent token-mapping and asset-identity logic that shares the same asset, nonce, proof subject, or mapping specifically in the `asset mapping drifts away from actual token semantics` attack class because resolves a token across Near and foreign chains using token-id and address maps that span multiple bridge adapters, violating `multi-chain mapping lookup must never return a different asset than the one collateral actually backs, especially in foreign-to-foreign forwarding`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::get_bridged_token`
- Entrypoint: `public multi-hop settlement flows that map tokens across chains`
- Attacker controls: source address, target chain, and any mapping state created by deploy/bind flows
- Exploit idea: Target upgrades, migration swaps, fake bridge-controlled tokens, and deploy callbacks. Focus on drift between this module and the adjacent token-mapping and asset-identity logic.
- Invariant to test: multi-chain mapping lookup must never return a different asset than the one collateral actually backs, especially in foreign-to-foreign forwarding
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Change token semantics around an existing mapping and assert that the bridge does not keep treating the token as a valid canonical representation. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs::get_bridged_token` and the adjacent token-mapping and asset-identity logic after every branch.
