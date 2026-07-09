# Q1012: NEAR fast_fin_transfer dispatcher burn or lock before irreversible state through cross-module drift

## Question
Can an unprivileged attacker use ``ft_on_transfer` branch for fast finalization` with control over token id, amount, signer identity as relayer, transfer id, recipient, fee, message, and optional storage deposit amount and desynchronize `near/omni-bridge/src/lib.rs::fast_fin_transfer` from the adjacent replay-protection bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `burn or lock before irreversible state` attack class because requires a trusted relayer, denormalizes amount and fee for the origin token, checks whether the referenced transfer is already finalised, and either pays a Near recipient immediately or emits a new transfer to another chain, violating `a fast path must never let relayers front-load value for an event that can later settle differently, twice, or with a different fee/recipient binding`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::fast_fin_transfer`
- Entrypoint: ``ft_on_transfer` branch for fast finalization`
- Attacker controls: token id, amount, signer identity as relayer, transfer id, recipient, fee, message, and optional storage deposit amount
- Exploit idea: Look for branches where custody changes happen before the final pending-state, mapping, or callback outcome is fixed. Focus on drift between this module and the adjacent replay-protection bookkeeping.
- Invariant to test: a fast path must never let relayers front-load value for an event that can later settle differently, twice, or with a different fee/recipient binding
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Model failures between custody changes and state writes, then assert that no branch both consumes user value and allows the transfer to be replayed or dropped. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs::fast_fin_transfer` and the adjacent replay-protection bookkeeping after every branch.
