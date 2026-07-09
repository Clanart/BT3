# Q2716: NEAR add_token mapping writer low-half deploy salt aliases another token id

## Question
Can an unprivileged attacker reach `public deploy/bind flows through internal mapping writes` and make `near/omni-bridge/src/lib.rs::add_token` deploy or reference another token’s address because the contract address salt uses only part of a larger hash, violating `mapping writes must never permit duplicate foreign addresses, duplicate Near token ids, or decimal records that disagree with the actual wrapped token`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::add_token`
- Entrypoint: `public deploy/bind flows through internal mapping writes`
- Attacker controls: token id, foreign token address, decimals, and origin decimals
- Exploit idea: Target Starknet deployment where the full token-id hash is the map key but only the low portion becomes the deploy salt.
- Invariant to test: mapping writes must never permit duplicate foreign addresses, duplicate Near token ids, or decimal records that disagree with the actual wrapped token
- Expected Immunefi impact: Balance manipulation
- Fast validation: Search for colliding low-half salts and assert that address derivation remains unique for all deployable token ids.
