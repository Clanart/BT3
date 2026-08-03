# Q3026: Accept-And-Timeout Double Settlement With Duplicate Or Reordered Items

## Question
Can an unprivileged attacker enter through `pallet_ismp::handle_unsigned(origin=None, messages) -> pallet_hyper_fungible_token::on_accept` with attacker-controlled send parameters, remote token mappings, precision values, authenticated inbound messages, and timeout inputs and placing duplicate or reordered leaves, signatures, requests, responses, timeouts, or commitments inside one user-accessible batch, and make `HftError` settle both receive and refund for the same substrate-side token transfer lifecycle so `the one-time lifecycle state for one token transfer` becomes inconsistent with `one final outcome of receive or refund`, breaking the invariant that a substrate-side token transfer must end in one of receive or refund, never both and leading to Critical: wrongful mint, unlock, withdrawal, refund, or redirect of protocol-controlled or user escrowed assets?

## Target
- File/function: modules/pallets/hyper-fungible-token/src/error.rs::HftError
- Entrypoint: pallet_ismp::handle_unsigned(origin=None, messages) -> pallet_hyper_fungible_token::on_accept
- Attacker controls: send parameters, remote token mappings, precision values, authenticated inbound messages, and timeout inputs
- Exploit idea: Settle both receive and refund for the same substrate-side token transfer lifecycle. Use a batch with one honest item and one duplicated or reordered item to see whether unique-item assumptions collapse.
- Invariant to test: a substrate-side token transfer must end in one of receive or refund, never both
- Expected Immunefi impact: Critical: wrongful mint, unlock, withdrawal, refund, or redirect of protocol-controlled or user escrowed assets.
- Fast validation: Drive a receive path and a timeout path for the same commitment and assert balances and supply reflect one terminal outcome. Write a focused batch test with repeated indices or commitments and assert only unique authenticated items can affect state.
