# Q3073: Decimal Conversion Drift Across Mixed Context

## Question
Can an unprivileged attacker enter through `pallet_hyper_fungible_token::send(origin, params)` with attacker-controlled send parameters, remote token mappings, precision values, authenticated inbound messages, and timeout inputs and mixing bytes that were valid in one proof, chain, module, order, or beneficiary context with metadata interpreted in another context, and make `send` convert between local decimals and remote decimals in a way that lets users receive or refund the wrong amount so `the local amount minted, burned, or released` becomes inconsistent with `the exact economic value implied by the remote amount and configured precisions`, breaking the invariant that precision conversion must preserve economic value across send, receive, and timeout without creating or destroying bridged value and leading to Critical: wrongful mint, unlock, withdrawal, refund, or redirect of protocol-controlled or user escrowed assets?

## Target
- File/function: modules/pallets/hyper-fungible-token/src/lib.rs::send
- Entrypoint: pallet_hyper_fungible_token::send(origin, params)
- Attacker controls: send parameters, remote token mappings, precision values, authenticated inbound messages, and timeout inputs
- Exploit idea: Convert between local decimals and remote decimals in a way that lets users receive or refund the wrong amount. Try a pair of otherwise valid artifacts where one verification step authenticates the old context and a later step consumes the new context.
- Invariant to test: precision conversion must preserve economic value across send, receive, and timeout without creating or destroying bridged value
- Expected Immunefi impact: Critical: wrongful mint, unlock, withdrawal, refund, or redirect of protocol-controlled or user escrowed assets.
- Fast validation: Fuzz remote and local decimal pairs and assert send, receive, and refund round-trip to one conserved value envelope. Build two neighboring valid contexts and mutate only the binding field while asserting state, receipts, and balances stay unchanged.
