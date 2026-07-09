# Q2690: NEAR foreign/native token mapping lookup low-half deploy salt aliases another token id

## Question
Can an unprivileged attacker reach `public multi-hop settlement flows that map tokens across chains` and make `near/omni-bridge/src/lib.rs::get_bridged_token` deploy or reference another token’s address because the contract address salt uses only part of a larger hash, violating `multi-chain mapping lookup must never return a different asset than the one collateral actually backs, especially in foreign-to-foreign forwarding`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::get_bridged_token`
- Entrypoint: `public multi-hop settlement flows that map tokens across chains`
- Attacker controls: source address, target chain, and any mapping state created by deploy/bind flows
- Exploit idea: Target Starknet deployment where the full token-id hash is the map key but only the low portion becomes the deploy salt.
- Invariant to test: multi-chain mapping lookup must never return a different asset than the one collateral actually backs, especially in foreign-to-foreign forwarding
- Expected Immunefi impact: Balance manipulation
- Fast validation: Search for colliding low-half salts and assert that address derivation remains unique for all deployable token ids.
