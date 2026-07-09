# Q781: NEAR UTXO fast resolver final settlement and later fee claim can diverge

## Question
Can an unprivileged attacker drive `public UTXO fast path reached through `ft_on_transfer`` so that `near/omni-bridge/src/lib.rs::utxo_fin_transfer_fast` settles principal under one interpretation of amount or transfer id while fee claim later uses another because of finalizes or removes a UTXO fast transfer depending on whether the destination is Near or another chain, violating `the same UTXO fast-transfer id must not both pay a relayer immediately and remain claimable on a second leg`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::utxo_fin_transfer_fast`
- Entrypoint: `public UTXO fast path reached through `ft_on_transfer``
- Attacker controls: fast-transfer id, stored fast-transfer status, destination chain, amount, and relayer recipient
- Exploit idea: Target differences between settle-time denormalization and claim-time recomputation of fee, dust, or relayer substitution.
- Invariant to test: the same UTXO fast-transfer id must not both pay a relayer immediately and remain claimable on a second leg
- Expected Immunefi impact: Balance manipulation
- Fast validation: Compare settled principal, stored transfer record, and fee-claim proof under edge amounts and assert that the three always reconstruct one consistent event.
