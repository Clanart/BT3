# Q3589: NEAR fast_fin_transfer dispatcher UTXO native-token requirement bypass at boundary values

## Question
Can an unprivileged attacker trigger ``ft_on_transfer` branch for fast finalization` with boundary-controlled inputs covering nonce boundaries, bucket boundaries, and maximal counters and make `near/omni-bridge/src/lib.rs::fast_fin_transfer` violate `a fast path must never let relayers front-load value for an event that can later settle differently, twice, or with a different fee/recipient binding` in the `UTXO native-token requirement bypass` attack class because requires a trusted relayer, denormalizes amount and fee for the origin token, checks whether the referenced transfer is already finalised, and either pays a Near recipient immediately or emits a new transfer to another chain becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::fast_fin_transfer`
- Entrypoint: ``ft_on_transfer` branch for fast finalization`
- Attacker controls: token id, amount, signer identity as relayer, transfer id, recipient, fee, message, and optional storage deposit amount
- Exploit idea: Target token-origin checks and chain-specific native-token requirements in BTC/Zcash-style flows. Concentrate on nonce boundaries, bucket boundaries, and maximal counters.
- Invariant to test: a fast path must never let relayers front-load value for an event that can later settle differently, twice, or with a different fee/recipient binding
- Expected Immunefi impact: Balance manipulation
- Fast validation: Fuzz chain/token combinations and assert that every accepted UTXO-facing flow uses exactly the configured native asset for that chain. Sweep boundary values for nonce boundaries, bucket boundaries, and maximal counters and assert that the same invariant holds at every edge.
