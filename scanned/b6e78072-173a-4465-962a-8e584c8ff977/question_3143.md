# Q3143: Send-Path Value Drift After Partial State Change

## Question
Can an unprivileged attacker enter through `pallet_ismp::handle_unsigned(origin=None, messages) -> pallet_hyper_fungible_token::on_accept` with attacker-controlled send parameters, remote token mappings, precision values, authenticated inbound messages, and timeout inputs and replaying the same public flow after one part of storage changed and another part did not, and make `create_asset` dispatch a remote amount that does not match what was actually locked or burned locally so `the remote amount encoded into the dispatched request` becomes inconsistent with `the exact local value removed from the sender`, breaking the invariant that the dispatch path must encode the same economic value that local custody or burn logic actually removed and leading to Critical: wrongful mint, unlock, withdrawal, refund, or redirect of protocol-controlled or user escrowed assets?

## Target
- File/function: modules/pallets/hyper-fungible-token/src/types.rs::create_asset
- Entrypoint: pallet_ismp::handle_unsigned(origin=None, messages) -> pallet_hyper_fungible_token::on_accept
- Attacker controls: send parameters, remote token mappings, precision values, authenticated inbound messages, and timeout inputs
- Exploit idea: Dispatch a remote amount that does not match what was actually locked or burned locally. Drive a partial success or revert path first, then replay the same user-controlled input and check whether stale state is reused.
- Invariant to test: the dispatch path must encode the same economic value that local custody or burn logic actually removed
- Expected Immunefi impact: Critical: wrongful mint, unlock, withdrawal, refund, or redirect of protocol-controlled or user escrowed assets.
- Fast validation: Bridge edge amounts through local send and assert the encoded remote amount matches the real local debit after conversion. Exercise a success-then-replay or fail-then-replay sequence and assert claimed flags, receipts, escrow, and nonces stay coherent.
