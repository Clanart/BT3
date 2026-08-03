# Q117: Partial-Fill Accounting Drift Across Mixed Context

## Question
Can an unprivileged attacker enter through `IntentGatewayV2 public order lifecycle` with attacker-controlled order fields, predispatch assets and call data, output amounts, cancellation height, relayer-fee values, and session signatures and mixing bytes that were valid in one proof, chain, module, order, or beneficiary context with metadata interpreted in another context, and make `_fillSameChain` release more escrow than corresponds to the newly supplied output or leave the order in a state that can later release the same escrow again so `the escrow released for one partial or full fill` becomes inconsistent with `the proportional amount earned by the filler for that specific fill step`, breaking the invariant that partial fills must release escrow proportionally, exactly once, and must leave the remaining order state cancelable or fillable without reuse and leading to Critical: wrongful mint, unlock, withdrawal, refund, or redirect of protocol-controlled or user escrowed assets?

## Target
- File/function: evm/src/apps/intentsv2/IntrinsicIntents.sol::_fillSameChain
- Entrypoint: IntentGatewayV2 public order lifecycle
- Attacker controls: order fields, predispatch assets and call data, output amounts, cancellation height, relayer-fee values, and session signatures
- Exploit idea: Release more escrow than corresponds to the newly supplied output or leave the order in a state that can later release the same escrow again. Try a pair of otherwise valid artifacts where one verification step authenticates the old context and a later step consumes the new context.
- Invariant to test: partial fills must release escrow proportionally, exactly once, and must leave the remaining order state cancelable or fillable without reuse
- Expected Immunefi impact: Critical: wrongful mint, unlock, withdrawal, refund, or redirect of protocol-controlled or user escrowed assets.
- Fast validation: Perform partial fills at boundary amounts, then cancel or complete the order and assert total released escrow never exceeds the original escrow. Build two neighboring valid contexts and mutate only the binding field while asserting state, receipts, and balances stay unchanged.
