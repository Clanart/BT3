# Q2542: NEAR foreign/native token mapping lookup fake bridge-controlled token accepted as canonical at boundary values

## Question
Can an unprivileged attacker trigger `public multi-hop settlement flows that map tokens across chains` with boundary-controlled inputs covering zero, maximal, and malformed user-controlled values and make `near/omni-bridge/src/lib.rs::get_bridged_token` violate `multi-chain mapping lookup must never return a different asset than the one collateral actually backs, especially in foreign-to-foreign forwarding` in the `fake bridge-controlled token accepted as canonical` attack class because resolves a token across Near and foreign chains using token-id and address maps that span multiple bridge adapters becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::get_bridged_token`
- Entrypoint: `public multi-hop settlement flows that map tokens across chains`
- Attacker controls: source address, target chain, and any mapping state created by deploy/bind flows
- Exploit idea: Target checks that only inspect mint authority, owner, or one mapping row without proving the full asset identity. Concentrate on zero, maximal, and malformed user-controlled values.
- Invariant to test: multi-chain mapping lookup must never return a different asset than the one collateral actually backs, especially in foreign-to-foreign forwarding
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Construct plausible fake bridge-controlled assets and assert that deployment, settlement, and forwarding reject them unless they are the canonical mapping for that remote asset. Sweep boundary values for zero, maximal, and malformed user-controlled values and assert that the same invariant holds at every edge.
