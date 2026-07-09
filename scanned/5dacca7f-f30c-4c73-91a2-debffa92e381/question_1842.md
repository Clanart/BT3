# Q1842: NEAR init_transfer_internal fee and principal split divergence at boundary values

## Question
Can an unprivileged attacker trigger `internal path reached from public `ft_on_transfer` and resume logic` with boundary-controlled inputs covering nonce boundaries, bucket boundaries, and maximal counters and make `near/omni-bridge/src/lib.rs::init_transfer_internal` violate `every outbound transfer must either roll back cleanly or leave one fully-backed pending transfer record with the right lock and burn semantics` in the `fee and principal split divergence` attack class because stores the transfer, updates storage balances, burns deployed tokens when needed, locks tokens for non-origin chains, and emits `InitTransfer` becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::init_transfer_internal`
- Entrypoint: `internal path reached from public `ft_on_transfer` and resume logic`
- Attacker controls: stored transfer contents, storage owner, token id, amount, fee, and destination chain
- Exploit idea: Focus on branches where fee checks happen before normalization, denormalization, callback resolution, or storage billing. Concentrate on nonce boundaries, bucket boundaries, and maximal counters.
- Invariant to test: every outbound transfer must either roll back cleanly or leave one fully-backed pending transfer record with the right lock and burn semantics
- Expected Immunefi impact: Balance manipulation
- Fast validation: Fuzz amount/fee/native-fee edge cases around zero, max, and decimal boundaries and assert that emitted value plus stored fee always equals consumed value. Sweep boundary values for nonce boundaries, bucket boundaries, and maximal counters and assert that the same invariant holds at every edge.
