# Q2437: NEAR ft_on_transfer dispatcher storage payer or owner spoofing at boundary values

## Question
Can an unprivileged attacker trigger `NEAR NEP-141 `ft_on_transfer` callback into bridge dispatch` with boundary-controlled inputs covering empty strings, maximal lengths, and malformed encodings and make `near/omni-bridge/src/lib.rs::ft_on_transfer` violate `the chosen branch, payer identity, amount, and downstream transfer type must stay aligned so one callback cannot burn, lock, or settle value on the wrong path` in the `storage payer or owner spoofing` attack class because dispatches untrusted `BridgeOnTransferMsg` into `init_transfer`, `fast_fin_transfer`, `utxo_fin_transfer`, or `swap_migrated_token` and explicitly trusts `env::signer_account_id()` instead of `sender_id` for storage payment decisions becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::ft_on_transfer`
- Entrypoint: `NEAR NEP-141 `ft_on_transfer` callback into bridge dispatch`
- Attacker controls: token contract choice, `sender_id`, `signer_id`, amount, JSON `msg` variant, recipient, fee, native fee, `external_id`, fast-transfer payloads, and UTXO payloads
- Exploit idea: Exploit signer/predecessor splits, message-storage account ids, or promise bookkeeping to shift storage liabilities between accounts. Concentrate on empty strings, maximal lengths, and malformed encodings.
- Invariant to test: the chosen branch, payer identity, amount, and downstream transfer type must stay aligned so one callback cannot burn, lock, or settle value on the wrong path
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Simulate conflicting `sender_id`, `signer_id`, and pre-funded storage accounts and assert that only the intended payer can fund, resume, or recover that transfer. Sweep boundary values for empty strings, maximal lengths, and malformed encodings and assert that the same invariant holds at every edge.
