# Q3119: Pending-State Replay Across Mixed Context

## Question
Can an unprivileged attacker enter through `pallet_ismp::handle_unsigned(origin=None, messages) -> pallet_hyper_fungible_token::on_timeout` with attacker-controlled send parameters, remote token mappings, precision values, authenticated inbound messages, and timeout inputs and mixing bytes that were valid in one proof, chain, module, order, or beneficiary context with metadata interpreted in another context, and make `on_timeout` replay one pending substrate-side token lifecycle after only part of its state was consumed so `the pending token-transfer state` becomes inconsistent with `one consistent pending record across send, receive, and timeout`, breaking the invariant that pending-state cleanup and restoration must prevent any replay that could release more than one lifecycle allows and leading to High: duplicate settlement, duplicate claim, or double execution of a one-time flow?

## Target
- File/function: modules/pallets/hyper-fungible-token/src/module.rs::on_timeout
- Entrypoint: pallet_ismp::handle_unsigned(origin=None, messages) -> pallet_hyper_fungible_token::on_timeout
- Attacker controls: send parameters, remote token mappings, precision values, authenticated inbound messages, and timeout inputs
- Exploit idea: Replay one pending substrate-side token lifecycle after only part of its state was consumed. Try a pair of otherwise valid artifacts where one verification step authenticates the old context and a later step consumes the new context.
- Invariant to test: pending-state cleanup and restoration must prevent any replay that could release more than one lifecycle allows
- Expected Immunefi impact: High: duplicate settlement, duplicate claim, or double execution of a one-time flow.
- Fast validation: Force an error mid-callback, replay the same lifecycle input, and assert no duplicate refund or receive becomes available. Build two neighboring valid contexts and mutate only the binding field while asserting state, receipts, and balances stay unchanged.
