# Q3674: NEAR foreign/native token mapping lookup asset mapping drifts away from actual token semantics at boundary values

## Question
Can an unprivileged attacker trigger `public multi-hop settlement flows that map tokens across chains` with boundary-controlled inputs covering zero, maximal, and malformed user-controlled values and make `near/omni-bridge/src/lib.rs::get_bridged_token` violate `multi-chain mapping lookup must never return a different asset than the one collateral actually backs, especially in foreign-to-foreign forwarding` in the `asset mapping drifts away from actual token semantics` attack class because resolves a token across Near and foreign chains using token-id and address maps that span multiple bridge adapters becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::get_bridged_token`
- Entrypoint: `public multi-hop settlement flows that map tokens across chains`
- Attacker controls: source address, target chain, and any mapping state created by deploy/bind flows
- Exploit idea: Target upgrades, migration swaps, fake bridge-controlled tokens, and deploy callbacks. Concentrate on zero, maximal, and malformed user-controlled values.
- Invariant to test: multi-chain mapping lookup must never return a different asset than the one collateral actually backs, especially in foreign-to-foreign forwarding
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Change token semantics around an existing mapping and assert that the bridge does not keep treating the token as a valid canonical representation. Sweep boundary values for zero, maximal, and malformed user-controlled values and assert that the same invariant holds at every edge.
