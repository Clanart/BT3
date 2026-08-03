# Q3069: Asset-Contract Mapping Misbinding Across Mixed Context

## Question
Can an unprivileged attacker enter through `pallet_hyper_fungible_token::send(origin, params)` with attacker-controlled send parameters, remote token mappings, precision values, authenticated inbound messages, and timeout inputs and mixing bytes that were valid in one proof, chain, module, order, or beneficiary context with metadata interpreted in another context, and make `send` resolve an incoming token message against the wrong local asset id or wrong remote contract mapping so `the local asset chosen for settlement` becomes inconsistent with `the exact asset configured for that remote chain and remote token contract`, breaking the invariant that remote-token-to-local-asset mappings must bind one remote contract on one chain to one local asset only and leading to Critical: wrongful mint, unlock, withdrawal, refund, or redirect of protocol-controlled or user escrowed assets?

## Target
- File/function: modules/pallets/hyper-fungible-token/src/lib.rs::send
- Entrypoint: pallet_hyper_fungible_token::send(origin, params)
- Attacker controls: send parameters, remote token mappings, precision values, authenticated inbound messages, and timeout inputs
- Exploit idea: Resolve an incoming token message against the wrong local asset id or wrong remote contract mapping. Try a pair of otherwise valid artifacts where one verification step authenticates the old context and a later step consumes the new context.
- Invariant to test: remote-token-to-local-asset mappings must bind one remote contract on one chain to one local asset only
- Expected Immunefi impact: Critical: wrongful mint, unlock, withdrawal, refund, or redirect of protocol-controlled or user escrowed assets.
- Fast validation: Register adjacent mappings and assert inbound settlement cannot mint or release under the wrong local asset id. Build two neighboring valid contexts and mutate only the binding field while asserting state, receipts, and balances stay unchanged.
