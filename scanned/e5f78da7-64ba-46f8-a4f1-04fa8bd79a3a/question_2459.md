# Q2459: NEAR init_transfer_internal storage payer or owner spoofing at boundary values

## Question
Can an unprivileged attacker trigger `internal path reached from public `ft_on_transfer` and resume logic` with boundary-controlled inputs covering nonce boundaries, bucket boundaries, and maximal counters and make `near/omni-bridge/src/lib.rs::init_transfer_internal` violate `every outbound transfer must either roll back cleanly or leave one fully-backed pending transfer record with the right lock and burn semantics` in the `storage payer or owner spoofing` attack class because stores the transfer, updates storage balances, burns deployed tokens when needed, locks tokens for non-origin chains, and emits `InitTransfer` becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::init_transfer_internal`
- Entrypoint: `internal path reached from public `ft_on_transfer` and resume logic`
- Attacker controls: stored transfer contents, storage owner, token id, amount, fee, and destination chain
- Exploit idea: Exploit signer/predecessor splits, message-storage account ids, or promise bookkeeping to shift storage liabilities between accounts. Concentrate on nonce boundaries, bucket boundaries, and maximal counters.
- Invariant to test: every outbound transfer must either roll back cleanly or leave one fully-backed pending transfer record with the right lock and burn semantics
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Simulate conflicting `sender_id`, `signer_id`, and pre-funded storage accounts and assert that only the intended payer can fund, resume, or recover that transfer. Sweep boundary values for nonce boundaries, bucket boundaries, and maximal counters and assert that the same invariant holds at every edge.
