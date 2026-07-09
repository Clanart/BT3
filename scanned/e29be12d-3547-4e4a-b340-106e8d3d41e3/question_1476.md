# Q1476: NEAR add_token mapping writer native versus wrapped registration confusion

## Question
Can an unprivileged attacker reach `public deploy/bind flows through internal mapping writes` and make `near/omni-bridge/src/lib.rs::add_token` treat a wrapped asset as native or a native asset as wrapped because of writes the core `token_id_to_address`, `token_address_to_id`, and `token_decimals` state that every bridge path later trusts, violating `mapping writes must never permit duplicate foreign addresses, duplicate Near token ids, or decimal records that disagree with the actual wrapped token`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::add_token`
- Entrypoint: `public deploy/bind flows through internal mapping writes`
- Attacker controls: token id, foreign token address, decimals, and origin decimals
- Exploit idea: Target vault-existence checks, deployed-token caches, origin-chain inference, and custom-minter registration.
- Invariant to test: mapping writes must never permit duplicate foreign addresses, duplicate Near token ids, or decimal records that disagree with the actual wrapped token
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Flip each classification predicate around existing mappings and assert that deployment and later settlement always preserve the same custody model.
