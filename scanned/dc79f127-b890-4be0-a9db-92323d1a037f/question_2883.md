# Q2883: NEAR ft_on_transfer dispatcher native versus wrapped branch switch through cross-module drift

## Question
Can an unprivileged attacker use `NEAR NEP-141 `ft_on_transfer` callback into bridge dispatch` with control over token contract choice, `sender_id`, `signer_id`, amount, JSON `msg` variant, recipient, fee, native fee, `external_id`, fast-transfer payloads, and UTXO payloads and desynchronize `near/omni-bridge/src/lib.rs::ft_on_transfer` from the adjacent storage billing and refund bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `native versus wrapped branch switch` attack class because dispatches untrusted `BridgeOnTransferMsg` into `init_transfer`, `fast_fin_transfer`, `utxo_fin_transfer`, or `swap_migrated_token` and explicitly trusts `env::signer_account_id()` instead of `sender_id` for storage payment decisions, violating `the chosen branch, payer identity, amount, and downstream transfer type must stay aligned so one callback cannot burn, lock, or settle value on the wrong path`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::ft_on_transfer`
- Entrypoint: `NEAR NEP-141 `ft_on_transfer` callback into bridge dispatch`
- Attacker controls: token contract choice, `sender_id`, `signer_id`, amount, JSON `msg` variant, recipient, fee, native fee, `external_id`, fast-transfer payloads, and UTXO payloads
- Exploit idea: Target zero-address, deployed-token, custom-minter, native-vault, or bridge-token branch predicates. Focus on drift between this module and the adjacent storage billing and refund bookkeeping.
- Invariant to test: the chosen branch, payer identity, amount, and downstream transfer type must stay aligned so one callback cannot burn, lock, or settle value on the wrong path
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Force each branch predicate to flip around callbacks or mapping writes and assert that the same source asset can never produce two incompatible custody models. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs::ft_on_transfer` and the adjacent storage billing and refund bookkeeping after every branch.
