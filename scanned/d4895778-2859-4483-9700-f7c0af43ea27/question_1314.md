# Q1314: NEAR add_token mapping writer decimal cap creates wrong economic model at boundary values

## Question
Can an unprivileged attacker trigger `public deploy/bind flows through internal mapping writes` with boundary-controlled inputs covering decimal caps, zero values, and normalization edges and make `near/omni-bridge/src/lib.rs::add_token` violate `mapping writes must never permit duplicate foreign addresses, duplicate Near token ids, or decimal records that disagree with the actual wrapped token` in the `decimal cap creates wrong economic model` attack class because writes the core `token_id_to_address`, `token_address_to_id`, and `token_decimals` state that every bridge path later trusts becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::add_token`
- Entrypoint: `public deploy/bind flows through internal mapping writes`
- Attacker controls: token id, foreign token address, decimals, and origin decimals
- Exploit idea: Target capped decimals on EVM, Solana, and Starknet deployments and later amount conversions during sign/finalize/claim flows. Concentrate on decimal caps, zero values, and normalization edges.
- Invariant to test: mapping writes must never permit duplicate foreign addresses, duplicate Near token ids, or decimal records that disagree with the actual wrapped token
- Expected Immunefi impact: Balance manipulation
- Fast validation: Deploy high-decimal assets and assert that every later amount conversion preserves one consistent economic relation to the source asset. Sweep boundary values for decimal caps, zero values, and normalization edges and assert that the same invariant holds at every edge.
