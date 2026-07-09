# Q846: NEAR fast_fin_transfer dispatcher burn or lock before irreversible state via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through ``ft_on_transfer` branch for fast finalization` and then replay or reorder later fee-claim proof submission so that `near/omni-bridge/src/lib.rs::fast_fin_transfer` ends up accepting two inconsistent interpretations of the same economic event specifically around `burn or lock before irreversible state` under requires a trusted relayer, denormalizes amount and fee for the origin token, checks whether the referenced transfer is already finalised, and either pays a Near recipient immediately or emits a new transfer to another chain, violating `a fast path must never let relayers front-load value for an event that can later settle differently, twice, or with a different fee/recipient binding`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::fast_fin_transfer`
- Entrypoint: ``ft_on_transfer` branch for fast finalization`
- Attacker controls: token id, amount, signer identity as relayer, transfer id, recipient, fee, message, and optional storage deposit amount
- Exploit idea: Look for branches where custody changes happen before the final pending-state, mapping, or callback outcome is fixed. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: a fast path must never let relayers front-load value for an event that can later settle differently, twice, or with a different fee/recipient binding
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Model failures between custody changes and state writes, then assert that no branch both consumes user value and allows the transfer to be replayed or dropped. Then replay or reorder later fee-claim proof submission and assert that the bridge still exposes only one valid economic outcome.
