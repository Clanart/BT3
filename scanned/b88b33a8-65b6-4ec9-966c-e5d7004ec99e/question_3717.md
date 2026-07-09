# Q3717: NEAR ft_on_transfer dispatcher native fee and token fee drawn from wrong asset bucket

## Question
Can an unprivileged attacker use `NEAR NEP-141 `ft_on_transfer` callback into bridge dispatch` to make `near/omni-bridge/src/lib.rs::ft_on_transfer` pay native fee and token fee from inconsistent custody pools because of dispatches untrusted `BridgeOnTransferMsg` into `init_transfer`, `fast_fin_transfer`, `utxo_fin_transfer`, or `swap_migrated_token` and explicitly trusts `env::signer_account_id()` instead of `sender_id` for storage payment decisions, violating `the chosen branch, payer identity, amount, and downstream transfer type must stay aligned so one callback cannot burn, lock, or settle value on the wrong path`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::ft_on_transfer`
- Entrypoint: `NEAR NEP-141 `ft_on_transfer` callback into bridge dispatch`
- Attacker controls: token contract choice, `sender_id`, `signer_id`, amount, JSON `msg` variant, recipient, fee, native fee, `external_id`, fast-transfer payloads, and UTXO payloads
- Exploit idea: Focus on branches that mint native-fee tokens, transfer escrowed tokens, or unwrap wrapped native assets.
- Invariant to test: the chosen branch, payer identity, amount, and downstream transfer type must stay aligned so one callback cannot burn, lock, or settle value on the wrong path
- Expected Immunefi impact: Balance manipulation
- Fast validation: Trace fee asset origin across every branch and assert that each fee component comes from the asset pool the bridge actually consumed.
