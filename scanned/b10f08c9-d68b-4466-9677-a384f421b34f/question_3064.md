# Q3064: Send-Path Value Drift With Duplicate Or Reordered Items

## Question
Can an unprivileged attacker enter through `pallet_ismp::handle_unsigned(origin=None, messages) -> pallet_hyper_fungible_token::on_accept` with attacker-controlled send parameters, remote token mappings, precision values, authenticated inbound messages, and timeout inputs and placing duplicate or reordered leaves, signatures, requests, responses, timeouts, or commitments inside one user-accessible batch, and make `convert_to_erc20` dispatch a remote amount that does not match what was actually locked or burned locally so `the remote amount encoded into the dispatched request` becomes inconsistent with `the exact local value removed from the sender`, breaking the invariant that the dispatch path must encode the same economic value that local custody or burn logic actually removed and leading to Critical: wrongful mint, unlock, withdrawal, refund, or redirect of protocol-controlled or user escrowed assets?

## Target
- File/function: modules/pallets/hyper-fungible-token/src/impls.rs::convert_to_erc20
- Entrypoint: pallet_ismp::handle_unsigned(origin=None, messages) -> pallet_hyper_fungible_token::on_accept
- Attacker controls: send parameters, remote token mappings, precision values, authenticated inbound messages, and timeout inputs
- Exploit idea: Dispatch a remote amount that does not match what was actually locked or burned locally. Use a batch with one honest item and one duplicated or reordered item to see whether unique-item assumptions collapse.
- Invariant to test: the dispatch path must encode the same economic value that local custody or burn logic actually removed
- Expected Immunefi impact: Critical: wrongful mint, unlock, withdrawal, refund, or redirect of protocol-controlled or user escrowed assets.
- Fast validation: Bridge edge amounts through local send and assert the encoded remote amount matches the real local debit after conversion. Write a focused batch test with repeated indices or commitments and assert only unique authenticated items can affect state.
