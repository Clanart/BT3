# Q1178: NEAR fast_fin_transfer dispatcher burn or lock before irreversible state at boundary values

## Question
Can an unprivileged attacker trigger ``ft_on_transfer` branch for fast finalization` with boundary-controlled inputs covering nonce boundaries, bucket boundaries, and maximal counters and make `near/omni-bridge/src/lib.rs::fast_fin_transfer` violate `a fast path must never let relayers front-load value for an event that can later settle differently, twice, or with a different fee/recipient binding` in the `burn or lock before irreversible state` attack class because requires a trusted relayer, denormalizes amount and fee for the origin token, checks whether the referenced transfer is already finalised, and either pays a Near recipient immediately or emits a new transfer to another chain becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::fast_fin_transfer`
- Entrypoint: ``ft_on_transfer` branch for fast finalization`
- Attacker controls: token id, amount, signer identity as relayer, transfer id, recipient, fee, message, and optional storage deposit amount
- Exploit idea: Look for branches where custody changes happen before the final pending-state, mapping, or callback outcome is fixed. Concentrate on nonce boundaries, bucket boundaries, and maximal counters.
- Invariant to test: a fast path must never let relayers front-load value for an event that can later settle differently, twice, or with a different fee/recipient binding
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Model failures between custody changes and state writes, then assert that no branch both consumes user value and allows the transfer to be replayed or dropped. Sweep boundary values for nonce boundaries, bucket boundaries, and maximal counters and assert that the same invariant holds at every edge.
