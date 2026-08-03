# Q3141: Send-Path Value Drift Across Mixed Context

## Question
Can an unprivileged attacker enter through `pallet_ismp::handle_unsigned(origin=None, messages) -> pallet_hyper_fungible_token::on_accept` with attacker-controlled send parameters, remote token mappings, precision values, authenticated inbound messages, and timeout inputs and mixing bytes that were valid in one proof, chain, module, order, or beneficiary context with metadata interpreted in another context, and make `create_asset` dispatch a remote amount that does not match what was actually locked or burned locally so `the remote amount encoded into the dispatched request` becomes inconsistent with `the exact local value removed from the sender`, breaking the invariant that the dispatch path must encode the same economic value that local custody or burn logic actually removed and leading to Critical: wrongful mint, unlock, withdrawal, refund, or redirect of protocol-controlled or user escrowed assets?

## Target
- File/function: modules/pallets/hyper-fungible-token/src/types.rs::create_asset
- Entrypoint: pallet_ismp::handle_unsigned(origin=None, messages) -> pallet_hyper_fungible_token::on_accept
- Attacker controls: send parameters, remote token mappings, precision values, authenticated inbound messages, and timeout inputs
- Exploit idea: Dispatch a remote amount that does not match what was actually locked or burned locally. Try a pair of otherwise valid artifacts where one verification step authenticates the old context and a later step consumes the new context.
- Invariant to test: the dispatch path must encode the same economic value that local custody or burn logic actually removed
- Expected Immunefi impact: Critical: wrongful mint, unlock, withdrawal, refund, or redirect of protocol-controlled or user escrowed assets.
- Fast validation: Bridge edge amounts through local send and assert the encoded remote amount matches the real local debit after conversion. Build two neighboring valid contexts and mutate only the binding field while asserting state, receipts, and balances stay unchanged.
