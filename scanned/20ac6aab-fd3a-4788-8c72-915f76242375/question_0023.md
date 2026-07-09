# Q23: NEAR init_transfer_internal origin and destination nonce desynchronization

## Question
Can an unprivileged attacker enter through `internal path reached from public `ft_on_transfer` and resume logic` with control over stored transfer contents, storage owner, token id, amount, fee, and destination chain and make `near/omni-bridge/src/lib.rs::init_transfer_internal` advance or reuse bridge nonces inconsistently with stores the transfer, updates storage balances, burns deployed tokens when needed, locks tokens for non-origin chains, and emits `InitTransfer`, so that one economic transfer can be emitted, resumed, or signed under multiple identifiers, violating `every outbound transfer must either roll back cleanly or leave one fully-backed pending transfer record with the right lock and burn semantics`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::init_transfer_internal`
- Entrypoint: `internal path reached from public `ft_on_transfer` and resume logic`
- Attacker controls: stored transfer contents, storage owner, token id, amount, fee, and destination chain
- Exploit idea: Drive retries, resume paths, or recursive bridge legs until one deposit appears under more than one transfer identity.
- Invariant to test: every outbound transfer must either roll back cleanly or leave one fully-backed pending transfer record with the right lock and burn semantics
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Trace every nonce mutation site and fuzz repeated calls plus reordered callbacks to prove that one deposit cannot create two valid transfer ids or destination messages.
