# Q3081: Custody-Mode Confusion Across Mixed Context

## Question
Can an unprivileged attacker enter through `pallet_hyper_fungible_token::send(origin, params)` with attacker-controlled send parameters, remote token mappings, precision values, authenticated inbound messages, and timeout inputs and mixing bytes that were valid in one proof, chain, module, order, or beneficiary context with metadata interpreted in another context, and make `send` cross native-asset custody mode and non-native mint/burn mode so one path releases value under the wrong accounting model so `the asset mode used for settlement` becomes inconsistent with `the configured native-versus-non-native mode for that asset`, breaking the invariant that each asset must settle under exactly one configured custody model and public flows must not cross those models implicitly and leading to Critical: wrongful mint, unlock, withdrawal, refund, or redirect of protocol-controlled or user escrowed assets?

## Target
- File/function: modules/pallets/hyper-fungible-token/src/lib.rs::send
- Entrypoint: pallet_hyper_fungible_token::send(origin, params)
- Attacker controls: send parameters, remote token mappings, precision values, authenticated inbound messages, and timeout inputs
- Exploit idea: Cross native-asset custody mode and non-native mint/burn mode so one path releases value under the wrong accounting model. Try a pair of otherwise valid artifacts where one verification step authenticates the old context and a later step consumes the new context.
- Invariant to test: each asset must settle under exactly one configured custody model and public flows must not cross those models implicitly
- Expected Immunefi impact: Critical: wrongful mint, unlock, withdrawal, refund, or redirect of protocol-controlled or user escrowed assets.
- Fast validation: Bridge both native and non-native assets around mode boundaries and assert release, burn, and refund stay in the configured model. Build two neighboring valid contexts and mutate only the binding field while asserting state, receipts, and balances stay unchanged.
