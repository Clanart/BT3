# Q399: Custody Versus Supply Drift Across Mixed Context

## Question
Can an unprivileged attacker enter through `HandlerV2.handlePostRequests(IHost host, request) -> token onAccept` with attacker-controlled public send parameters, bridged message bodies, beneficiary bytes, callback data, and relayer-fee inputs and mixing bytes that were valid in one proof, chain, module, order, or beneficiary context with metadata interpreted in another context, and make `gateway` release, mint, burn, or refund a value that is inconsistent with what was actually escrowed or burned so `the bridged amount credited or released` becomes inconsistent with `the exact amount locked, burned, or proven in the authenticated message`, breaking the invariant that locked or burned value and released or minted value must match exactly once per transfer lifecycle and leading to Critical: wrongful mint, unlock, withdrawal, refund, or redirect of protocol-controlled or user escrowed assets?

## Target
- File/function: evm/src/utils/HyperFungibleTokenImpl.sol::gateway
- Entrypoint: HandlerV2.handlePostRequests(IHost host, request) -> token onAccept
- Attacker controls: public send parameters, bridged message bodies, beneficiary bytes, callback data, and relayer-fee inputs
- Exploit idea: Release, mint, burn, or refund a value that is inconsistent with what was actually escrowed or burned. Try a pair of otherwise valid artifacts where one verification step authenticates the old context and a later step consumes the new context.
- Invariant to test: locked or burned value and released or minted value must match exactly once per transfer lifecycle
- Expected Immunefi impact: Critical: wrongful mint, unlock, withdrawal, refund, or redirect of protocol-controlled or user escrowed assets.
- Fast validation: Use fee-on-transfer, decimal, or replay edge cases and assert local supply plus remote custody remains conserved across send, receive, and timeout. Build two neighboring valid contexts and mutate only the binding field while asserting state, receipts, and balances stay unchanged.
