# Q3052: NEAR init_transfer_internal native versus wrapped branch switch at boundary values

## Question
Can an unprivileged attacker trigger `internal path reached from public `ft_on_transfer` and resume logic` with boundary-controlled inputs covering nonce boundaries, bucket boundaries, and maximal counters and make `near/omni-bridge/src/lib.rs::init_transfer_internal` violate `every outbound transfer must either roll back cleanly or leave one fully-backed pending transfer record with the right lock and burn semantics` in the `native versus wrapped branch switch` attack class because stores the transfer, updates storage balances, burns deployed tokens when needed, locks tokens for non-origin chains, and emits `InitTransfer` becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::init_transfer_internal`
- Entrypoint: `internal path reached from public `ft_on_transfer` and resume logic`
- Attacker controls: stored transfer contents, storage owner, token id, amount, fee, and destination chain
- Exploit idea: Target zero-address, deployed-token, custom-minter, native-vault, or bridge-token branch predicates. Concentrate on nonce boundaries, bucket boundaries, and maximal counters.
- Invariant to test: every outbound transfer must either roll back cleanly or leave one fully-backed pending transfer record with the right lock and burn semantics
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Force each branch predicate to flip around callbacks or mapping writes and assert that the same source asset can never produce two incompatible custody models. Sweep boundary values for nonce boundaries, bucket boundaries, and maximal counters and assert that the same invariant holds at every edge.
