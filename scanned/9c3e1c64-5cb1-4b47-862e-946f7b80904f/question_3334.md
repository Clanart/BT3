# Q3334: NEAR init_transfer_internal same fee collectible twice via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `internal path reached from public `ft_on_transfer` and resume logic` and then replay or reorder the later settlement leg on another chain so that `near/omni-bridge/src/lib.rs::init_transfer_internal` ends up accepting two inconsistent interpretations of the same economic event specifically around `same fee collectible twice` under stores the transfer, updates storage balances, burns deployed tokens when needed, locks tokens for non-origin chains, and emits `InitTransfer`, violating `every outbound transfer must either roll back cleanly or leave one fully-backed pending transfer record with the right lock and burn semantics`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::init_transfer_internal`
- Entrypoint: `internal path reached from public `ft_on_transfer` and resume logic`
- Attacker controls: stored transfer contents, storage owner, token id, amount, fee, and destination chain
- Exploit idea: Target pending-transfer cleanup, fast-transfer removal, and replay protection around fee-claim proofs. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: every outbound transfer must either roll back cleanly or leave one fully-backed pending transfer record with the right lock and burn semantics
- Expected Immunefi impact: Balance manipulation
- Fast validation: Claim once, then replay or mutate non-economic proof fields and assert that no second claim succeeds or blocks unrelated transfers. Then replay or reorder the later settlement leg on another chain and assert that the bridge still exposes only one valid economic outcome.
