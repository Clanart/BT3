# Q125: Dust Or Surplus Misrouting Across Mixed Context

## Question
Can an unprivileged attacker enter through `IntentGatewayV2 public order lifecycle` with attacker-controlled order fields, predispatch assets and call data, output amounts, cancellation height, relayer-fee values, and session signatures and mixing bytes that were valid in one proof, chain, module, order, or beneficiary context with metadata interpreted in another context, and make `_fillSameChain` send surplus, dust, protocol fees, or native refunds to the wrong recipient or account them twice so `the beneficiary and protocol share of excess value` becomes inconsistent with `the split encoded by order fields and configured fee parameters`, breaking the invariant that surplus and dust must follow one deterministic split between beneficiary, protocol, and filler without leaking across order states and leading to High: valid activity resolves to the wrong beneficiary, wrong token amount, wrong reward amount, or wrong order value?

## Target
- File/function: evm/src/apps/intentsv2/IntrinsicIntents.sol::_fillSameChain
- Entrypoint: IntentGatewayV2 public order lifecycle
- Attacker controls: order fields, predispatch assets and call data, output amounts, cancellation height, relayer-fee values, and session signatures
- Exploit idea: Send surplus, dust, protocol fees, or native refunds to the wrong recipient or account them twice. Try a pair of otherwise valid artifacts where one verification step authenticates the old context and a later step consumes the new context.
- Invariant to test: surplus and dust must follow one deterministic split between beneficiary, protocol, and filler without leaking across order states
- Expected Immunefi impact: High: valid activity resolves to the wrong beneficiary, wrong token amount, wrong reward amount, or wrong order value.
- Fast validation: Overpay outputs, exercise native refund paths, and assert protocol share, beneficiary share, and filler refunds add up exactly once. Build two neighboring valid contexts and mutate only the binding field while asserting state, receipts, and balances stay unchanged.
