# Q3454: NEAR fast_fin_transfer dispatcher UTXO native-token requirement bypass through cross-module drift

## Question
Can an unprivileged attacker use ``ft_on_transfer` branch for fast finalization` with control over token id, amount, signer identity as relayer, transfer id, recipient, fee, message, and optional storage deposit amount and desynchronize `near/omni-bridge/src/lib.rs::fast_fin_transfer` from the adjacent replay-protection bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `UTXO native-token requirement bypass` attack class because requires a trusted relayer, denormalizes amount and fee for the origin token, checks whether the referenced transfer is already finalised, and either pays a Near recipient immediately or emits a new transfer to another chain, violating `a fast path must never let relayers front-load value for an event that can later settle differently, twice, or with a different fee/recipient binding`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::fast_fin_transfer`
- Entrypoint: ``ft_on_transfer` branch for fast finalization`
- Attacker controls: token id, amount, signer identity as relayer, transfer id, recipient, fee, message, and optional storage deposit amount
- Exploit idea: Target token-origin checks and chain-specific native-token requirements in BTC/Zcash-style flows. Focus on drift between this module and the adjacent replay-protection bookkeeping.
- Invariant to test: a fast path must never let relayers front-load value for an event that can later settle differently, twice, or with a different fee/recipient binding
- Expected Immunefi impact: Balance manipulation
- Fast validation: Fuzz chain/token combinations and assert that every accepted UTXO-facing flow uses exactly the configured native asset for that chain. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs::fast_fin_transfer` and the adjacent replay-protection bookkeeping after every branch.
