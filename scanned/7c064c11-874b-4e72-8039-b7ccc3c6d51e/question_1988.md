# Q1988: NEAR fast_fin_transfer dispatcher storage payer or owner spoofing

## Question
Can an unprivileged attacker cause `near/omni-bridge/src/lib.rs::fast_fin_transfer` to bill, refund, or resume the wrong storage owner through ``ft_on_transfer` branch for fast finalization` by abusing requires a trusted relayer, denormalizes amount and fee for the origin token, checks whether the referenced transfer is already finalised, and either pays a Near recipient immediately or emits a new transfer to another chain, violating `a fast path must never let relayers front-load value for an event that can later settle differently, twice, or with a different fee/recipient binding`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::fast_fin_transfer`
- Entrypoint: ``ft_on_transfer` branch for fast finalization`
- Attacker controls: token id, amount, signer identity as relayer, transfer id, recipient, fee, message, and optional storage deposit amount
- Exploit idea: Exploit signer/predecessor splits, message-storage account ids, or promise bookkeeping to shift storage liabilities between accounts.
- Invariant to test: a fast path must never let relayers front-load value for an event that can later settle differently, twice, or with a different fee/recipient binding
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Simulate conflicting `sender_id`, `signer_id`, and pre-funded storage accounts and assert that only the intended payer can fund, resume, or recover that transfer.
