# Q2155: NEAR init_transfer_internal storage payer or owner spoofing via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `internal path reached from public `ft_on_transfer` and resume logic` and then replay or reorder the later settlement leg on another chain so that `near/omni-bridge/src/lib.rs::init_transfer_internal` ends up accepting two inconsistent interpretations of the same economic event specifically around `storage payer or owner spoofing` under stores the transfer, updates storage balances, burns deployed tokens when needed, locks tokens for non-origin chains, and emits `InitTransfer`, violating `every outbound transfer must either roll back cleanly or leave one fully-backed pending transfer record with the right lock and burn semantics`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::init_transfer_internal`
- Entrypoint: `internal path reached from public `ft_on_transfer` and resume logic`
- Attacker controls: stored transfer contents, storage owner, token id, amount, fee, and destination chain
- Exploit idea: Exploit signer/predecessor splits, message-storage account ids, or promise bookkeeping to shift storage liabilities between accounts. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: every outbound transfer must either roll back cleanly or leave one fully-backed pending transfer record with the right lock and burn semantics
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Simulate conflicting `sender_id`, `signer_id`, and pre-funded storage accounts and assert that only the intended payer can fund, resume, or recover that transfer. Then replay or reorder the later settlement leg on another chain and assert that the bridge still exposes only one valid economic outcome.
