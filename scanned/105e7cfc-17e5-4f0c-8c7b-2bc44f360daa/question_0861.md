# Q861: NEAR init_transfer_internal burn or lock before irreversible state via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `internal path reached from public `ft_on_transfer` and resume logic` and then replay or reorder the later settlement leg on another chain so that `near/omni-bridge/src/lib.rs::init_transfer_internal` ends up accepting two inconsistent interpretations of the same economic event specifically around `burn or lock before irreversible state` under stores the transfer, updates storage balances, burns deployed tokens when needed, locks tokens for non-origin chains, and emits `InitTransfer`, violating `every outbound transfer must either roll back cleanly or leave one fully-backed pending transfer record with the right lock and burn semantics`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::init_transfer_internal`
- Entrypoint: `internal path reached from public `ft_on_transfer` and resume logic`
- Attacker controls: stored transfer contents, storage owner, token id, amount, fee, and destination chain
- Exploit idea: Look for branches where custody changes happen before the final pending-state, mapping, or callback outcome is fixed. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: every outbound transfer must either roll back cleanly or leave one fully-backed pending transfer record with the right lock and burn semantics
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Model failures between custody changes and state writes, then assert that no branch both consumes user value and allows the transfer to be replayed or dropped. Then replay or reorder the later settlement leg on another chain and assert that the bridge still exposes only one valid economic outcome.
