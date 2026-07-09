# Q3131: NEAR foreign/native token mapping lookup low-half deploy salt aliases another token id at boundary values

## Question
Can an unprivileged attacker trigger `public multi-hop settlement flows that map tokens across chains` with boundary-controlled inputs covering zero, maximal, and malformed user-controlled values and make `near/omni-bridge/src/lib.rs::get_bridged_token` violate `multi-chain mapping lookup must never return a different asset than the one collateral actually backs, especially in foreign-to-foreign forwarding` in the `low-half deploy salt aliases another token id` attack class because resolves a token across Near and foreign chains using token-id and address maps that span multiple bridge adapters becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::get_bridged_token`
- Entrypoint: `public multi-hop settlement flows that map tokens across chains`
- Attacker controls: source address, target chain, and any mapping state created by deploy/bind flows
- Exploit idea: Target Starknet deployment where the full token-id hash is the map key but only the low portion becomes the deploy salt. Concentrate on zero, maximal, and malformed user-controlled values.
- Invariant to test: multi-chain mapping lookup must never return a different asset than the one collateral actually backs, especially in foreign-to-foreign forwarding
- Expected Immunefi impact: Balance manipulation
- Fast validation: Search for colliding low-half salts and assert that address derivation remains unique for all deployable token ids. Sweep boundary values for zero, maximal, and malformed user-controlled values and assert that the same invariant holds at every edge.
