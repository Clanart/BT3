# Q3991: NEAR init_transfer_internal native fee and token fee drawn from wrong asset bucket through cross-module drift

## Question
Can an unprivileged attacker use `internal path reached from public `ft_on_transfer` and resume logic` with control over stored transfer contents, storage owner, token id, amount, fee, and destination chain and desynchronize `near/omni-bridge/src/lib.rs::init_transfer_internal` from the adjacent replay-protection bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `native fee and token fee drawn from wrong asset bucket` attack class because stores the transfer, updates storage balances, burns deployed tokens when needed, locks tokens for non-origin chains, and emits `InitTransfer`, violating `every outbound transfer must either roll back cleanly or leave one fully-backed pending transfer record with the right lock and burn semantics`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::init_transfer_internal`
- Entrypoint: `internal path reached from public `ft_on_transfer` and resume logic`
- Attacker controls: stored transfer contents, storage owner, token id, amount, fee, and destination chain
- Exploit idea: Focus on branches that mint native-fee tokens, transfer escrowed tokens, or unwrap wrapped native assets. Focus on drift between this module and the adjacent replay-protection bookkeeping.
- Invariant to test: every outbound transfer must either roll back cleanly or leave one fully-backed pending transfer record with the right lock and burn semantics
- Expected Immunefi impact: Balance manipulation
- Fast validation: Trace fee asset origin across every branch and assert that each fee component comes from the asset pool the bridge actually consumed. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs::init_transfer_internal` and the adjacent replay-protection bookkeeping after every branch.
