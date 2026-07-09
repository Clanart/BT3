# Q1930: NEAR foreign/native token mapping lookup native versus wrapped registration confusion at boundary values

## Question
Can an unprivileged attacker trigger `public multi-hop settlement flows that map tokens across chains` with boundary-controlled inputs covering zero, maximal, and malformed user-controlled values and make `near/omni-bridge/src/lib.rs::get_bridged_token` violate `multi-chain mapping lookup must never return a different asset than the one collateral actually backs, especially in foreign-to-foreign forwarding` in the `native versus wrapped registration confusion` attack class because resolves a token across Near and foreign chains using token-id and address maps that span multiple bridge adapters becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::get_bridged_token`
- Entrypoint: `public multi-hop settlement flows that map tokens across chains`
- Attacker controls: source address, target chain, and any mapping state created by deploy/bind flows
- Exploit idea: Target vault-existence checks, deployed-token caches, origin-chain inference, and custom-minter registration. Concentrate on zero, maximal, and malformed user-controlled values.
- Invariant to test: multi-chain mapping lookup must never return a different asset than the one collateral actually backs, especially in foreign-to-foreign forwarding
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Flip each classification predicate around existing mappings and assert that deployment and later settlement always preserve the same custody model. Sweep boundary values for zero, maximal, and malformed user-controlled values and assert that the same invariant holds at every edge.
