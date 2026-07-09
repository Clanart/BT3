# Q2905: NEAR init_transfer_internal native versus wrapped branch switch through cross-module drift

## Question
Can an unprivileged attacker use `internal path reached from public `ft_on_transfer` and resume logic` with control over stored transfer contents, storage owner, token id, amount, fee, and destination chain and desynchronize `near/omni-bridge/src/lib.rs::init_transfer_internal` from the adjacent replay-protection bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `native versus wrapped branch switch` attack class because stores the transfer, updates storage balances, burns deployed tokens when needed, locks tokens for non-origin chains, and emits `InitTransfer`, violating `every outbound transfer must either roll back cleanly or leave one fully-backed pending transfer record with the right lock and burn semantics`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::init_transfer_internal`
- Entrypoint: `internal path reached from public `ft_on_transfer` and resume logic`
- Attacker controls: stored transfer contents, storage owner, token id, amount, fee, and destination chain
- Exploit idea: Target zero-address, deployed-token, custom-minter, native-vault, or bridge-token branch predicates. Focus on drift between this module and the adjacent replay-protection bookkeeping.
- Invariant to test: every outbound transfer must either roll back cleanly or leave one fully-backed pending transfer record with the right lock and burn semantics
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Force each branch predicate to flip around callbacks or mapping writes and assert that the same source asset can never produce two incompatible custody models. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs::init_transfer_internal` and the adjacent replay-protection bookkeeping after every branch.
