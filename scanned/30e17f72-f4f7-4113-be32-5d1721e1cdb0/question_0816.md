# Q816: NEAR add_token mapping writer decimal cap creates wrong economic model

## Question
Can an unprivileged attacker reach `public deploy/bind flows through internal mapping writes` with a token whose remote decimals exceed local limits and make `near/omni-bridge/src/lib.rs::add_token` register a representation that breaks backing assumptions because of writes the core `token_id_to_address`, `token_address_to_id`, and `token_decimals` state that every bridge path later trusts, violating `mapping writes must never permit duplicate foreign addresses, duplicate Near token ids, or decimal records that disagree with the actual wrapped token`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::add_token`
- Entrypoint: `public deploy/bind flows through internal mapping writes`
- Attacker controls: token id, foreign token address, decimals, and origin decimals
- Exploit idea: Target capped decimals on EVM, Solana, and Starknet deployments and later amount conversions during sign/finalize/claim flows.
- Invariant to test: mapping writes must never permit duplicate foreign addresses, duplicate Near token ids, or decimal records that disagree with the actual wrapped token
- Expected Immunefi impact: Balance manipulation
- Fast validation: Deploy high-decimal assets and assert that every later amount conversion preserves one consistent economic relation to the source asset.
