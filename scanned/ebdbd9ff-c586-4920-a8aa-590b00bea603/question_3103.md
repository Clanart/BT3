# Q3103: Accept-And-Timeout Double Settlement Across Mixed Context

## Question
Can an unprivileged attacker enter through `pallet_ismp::handle_unsigned(origin=None, messages) -> pallet_hyper_fungible_token::on_timeout` with attacker-controlled send parameters, remote token mappings, precision values, authenticated inbound messages, and timeout inputs and mixing bytes that were valid in one proof, chain, module, order, or beneficiary context with metadata interpreted in another context, and make `on_timeout` settle both receive and refund for the same substrate-side token transfer lifecycle so `the one-time lifecycle state for one token transfer` becomes inconsistent with `one final outcome of receive or refund`, breaking the invariant that a substrate-side token transfer must end in one of receive or refund, never both and leading to Critical: wrongful mint, unlock, withdrawal, refund, or redirect of protocol-controlled or user escrowed assets?

## Target
- File/function: modules/pallets/hyper-fungible-token/src/module.rs::on_timeout
- Entrypoint: pallet_ismp::handle_unsigned(origin=None, messages) -> pallet_hyper_fungible_token::on_timeout
- Attacker controls: send parameters, remote token mappings, precision values, authenticated inbound messages, and timeout inputs
- Exploit idea: Settle both receive and refund for the same substrate-side token transfer lifecycle. Try a pair of otherwise valid artifacts where one verification step authenticates the old context and a later step consumes the new context.
- Invariant to test: a substrate-side token transfer must end in one of receive or refund, never both
- Expected Immunefi impact: Critical: wrongful mint, unlock, withdrawal, refund, or redirect of protocol-controlled or user escrowed assets.
- Fast validation: Drive a receive path and a timeout path for the same commitment and assert balances and supply reflect one terminal outcome. Build two neighboring valid contexts and mutate only the binding field while asserting state, receipts, and balances stay unchanged.
