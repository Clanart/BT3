# Q3177: NEAR ft_on_transfer dispatcher UTXO native-token requirement bypass

## Question
Can an unprivileged attacker craft a UTXO-facing outbound flow through `NEAR NEP-141 `ft_on_transfer` callback into bridge dispatch` that makes `near/omni-bridge/src/lib.rs::ft_on_transfer` accept a non-native token or wrong chain config because of dispatches untrusted `BridgeOnTransferMsg` into `init_transfer`, `fast_fin_transfer`, `utxo_fin_transfer`, or `swap_migrated_token` and explicitly trusts `env::signer_account_id()` instead of `sender_id` for storage payment decisions, violating `the chosen branch, payer identity, amount, and downstream transfer type must stay aligned so one callback cannot burn, lock, or settle value on the wrong path`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::ft_on_transfer`
- Entrypoint: `NEAR NEP-141 `ft_on_transfer` callback into bridge dispatch`
- Attacker controls: token contract choice, `sender_id`, `signer_id`, amount, JSON `msg` variant, recipient, fee, native fee, `external_id`, fast-transfer payloads, and UTXO payloads
- Exploit idea: Target token-origin checks and chain-specific native-token requirements in BTC/Zcash-style flows.
- Invariant to test: the chosen branch, payer identity, amount, and downstream transfer type must stay aligned so one callback cannot burn, lock, or settle value on the wrong path
- Expected Immunefi impact: Balance manipulation
- Fast validation: Fuzz chain/token combinations and assert that every accepted UTXO-facing flow uses exactly the configured native asset for that chain.
