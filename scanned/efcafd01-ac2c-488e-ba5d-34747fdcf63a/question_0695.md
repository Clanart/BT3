# Q695: NEAR init_transfer_internal burn or lock before irreversible state

## Question
Can an unprivileged attacker use `internal path reached from public `ft_on_transfer` and resume logic` to force `near/omni-bridge/src/lib.rs::init_transfer_internal` to burn or lock assets before the transfer record becomes safely irreversible, and then recover or redirect the bridge flow via stores the transfer, updates storage balances, burns deployed tokens when needed, locks tokens for non-origin chains, and emits `InitTransfer`, violating `every outbound transfer must either roll back cleanly or leave one fully-backed pending transfer record with the right lock and burn semantics`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::init_transfer_internal`
- Entrypoint: `internal path reached from public `ft_on_transfer` and resume logic`
- Attacker controls: stored transfer contents, storage owner, token id, amount, fee, and destination chain
- Exploit idea: Look for branches where custody changes happen before the final pending-state, mapping, or callback outcome is fixed.
- Invariant to test: every outbound transfer must either roll back cleanly or leave one fully-backed pending transfer record with the right lock and burn semantics
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Model failures between custody changes and state writes, then assert that no branch both consumes user value and allows the transfer to be replayed or dropped.
