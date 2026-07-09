# Q3184: NEAR fast_fin_transfer dispatcher UTXO native-token requirement bypass

## Question
Can an unprivileged attacker craft a UTXO-facing outbound flow through ``ft_on_transfer` branch for fast finalization` that makes `near/omni-bridge/src/lib.rs::fast_fin_transfer` accept a non-native token or wrong chain config because of requires a trusted relayer, denormalizes amount and fee for the origin token, checks whether the referenced transfer is already finalised, and either pays a Near recipient immediately or emits a new transfer to another chain, violating `a fast path must never let relayers front-load value for an event that can later settle differently, twice, or with a different fee/recipient binding`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::fast_fin_transfer`
- Entrypoint: ``ft_on_transfer` branch for fast finalization`
- Attacker controls: token id, amount, signer identity as relayer, transfer id, recipient, fee, message, and optional storage deposit amount
- Exploit idea: Target token-origin checks and chain-specific native-token requirements in BTC/Zcash-style flows.
- Invariant to test: a fast path must never let relayers front-load value for an event that can later settle differently, twice, or with a different fee/recipient binding
- Expected Immunefi impact: Balance manipulation
- Fast validation: Fuzz chain/token combinations and assert that every accepted UTXO-facing flow uses exactly the configured native asset for that chain.
