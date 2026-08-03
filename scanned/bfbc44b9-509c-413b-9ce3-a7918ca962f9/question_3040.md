# Q3040: Send-Path Value Drift By Reusing Data Cross Module

## Question
Can an unprivileged attacker enter through `pallet_ismp::handle_unsigned(origin=None, messages) -> pallet_hyper_fungible_token::on_accept` with attacker-controlled send parameters, remote token mappings, precision values, authenticated inbound messages, and timeout inputs and reusing data that should belong to one chain, module, order, state machine, or payee in another publicly reachable path, and make `HftError` dispatch a remote amount that does not match what was actually locked or burned locally so `the remote amount encoded into the dispatched request` becomes inconsistent with `the exact local value removed from the sender`, breaking the invariant that the dispatch path must encode the same economic value that local custody or burn logic actually removed and leading to Critical: wrongful mint, unlock, withdrawal, refund, or redirect of protocol-controlled or user escrowed assets?

## Target
- File/function: modules/pallets/hyper-fungible-token/src/error.rs::HftError
- Entrypoint: pallet_ismp::handle_unsigned(origin=None, messages) -> pallet_hyper_fungible_token::on_accept
- Attacker controls: send parameters, remote token mappings, precision values, authenticated inbound messages, and timeout inputs
- Exploit idea: Dispatch a remote amount that does not match what was actually locked or burned locally. Craft two public flows that share one byte string or hash and check whether module, chain, or payee binding is enforced everywhere.
- Invariant to test: the dispatch path must encode the same economic value that local custody or burn logic actually removed
- Expected Immunefi impact: Critical: wrongful mint, unlock, withdrawal, refund, or redirect of protocol-controlled or user escrowed assets.
- Fast validation: Bridge edge amounts through local send and assert the encoded remote amount matches the real local debit after conversion. Feed the same bytes through two reachable entrypoints and assert the second path rejects instead of inheriting the first path's authorization.
