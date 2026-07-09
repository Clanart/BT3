# Q3157: NEAR add_token mapping writer low-half deploy salt aliases another token id at boundary values

## Question
Can an unprivileged attacker trigger `public deploy/bind flows through internal mapping writes` with boundary-controlled inputs covering decimal caps, zero values, and normalization edges and make `near/omni-bridge/src/lib.rs::add_token` violate `mapping writes must never permit duplicate foreign addresses, duplicate Near token ids, or decimal records that disagree with the actual wrapped token` in the `low-half deploy salt aliases another token id` attack class because writes the core `token_id_to_address`, `token_address_to_id`, and `token_decimals` state that every bridge path later trusts becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::add_token`
- Entrypoint: `public deploy/bind flows through internal mapping writes`
- Attacker controls: token id, foreign token address, decimals, and origin decimals
- Exploit idea: Target Starknet deployment where the full token-id hash is the map key but only the low portion becomes the deploy salt. Concentrate on decimal caps, zero values, and normalization edges.
- Invariant to test: mapping writes must never permit duplicate foreign addresses, duplicate Near token ids, or decimal records that disagree with the actual wrapped token
- Expected Immunefi impact: Balance manipulation
- Fast validation: Search for colliding low-half salts and assert that address derivation remains unique for all deployable token ids. Sweep boundary values for decimal caps, zero values, and normalization edges and assert that the same invariant holds at every edge.
