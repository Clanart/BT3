# Q1823: NEAR init_transfer resume path storage payer or owner spoofing at boundary values

## Question
Can an unprivileged attacker trigger `yield-resume callback for a previously deferred outbound transfer` with boundary-controlled inputs covering nonce boundaries, bucket boundaries, and maximal counters and make `near/omni-bridge/src/lib.rs::init_transfer_resume` violate `resume logic must never let one deferred transfer be funded, resumed, or emitted more than once or under a different payer/value pairing` in the `storage payer or owner spoofing` attack class because removes a stored promise id, retries storage transfer from the message account, and then restarts `init_transfer_internal` after a timeout or callback result becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::init_transfer_resume`
- Entrypoint: `yield-resume callback for a previously deferred outbound transfer`
- Attacker controls: timing of storage funding, promise completion order, storage owner, message-storage account state, and the original transfer message contents
- Exploit idea: Exploit signer/predecessor splits, message-storage account ids, or promise bookkeeping to shift storage liabilities between accounts. Concentrate on nonce boundaries, bucket boundaries, and maximal counters.
- Invariant to test: resume logic must never let one deferred transfer be funded, resumed, or emitted more than once or under a different payer/value pairing
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Simulate conflicting `sender_id`, `signer_id`, and pre-funded storage accounts and assert that only the intended payer can fund, resume, or recover that transfer. Sweep boundary values for nonce boundaries, bucket boundaries, and maximal counters and assert that the same invariant holds at every edge.
