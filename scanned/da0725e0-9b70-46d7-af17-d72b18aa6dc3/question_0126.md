# Q126: Dust Or Surplus Misrouting With Duplicate Or Reordered Items

## Question
Can an unprivileged attacker enter through `IntentGatewayV2 public order lifecycle` with attacker-controlled order fields, predispatch assets and call data, output amounts, cancellation height, relayer-fee values, and session signatures and placing duplicate or reordered leaves, signatures, requests, responses, timeouts, or commitments inside one user-accessible batch, and make `_fillSameChain` send surplus, dust, protocol fees, or native refunds to the wrong recipient or account them twice so `the beneficiary and protocol share of excess value` becomes inconsistent with `the split encoded by order fields and configured fee parameters`, breaking the invariant that surplus and dust must follow one deterministic split between beneficiary, protocol, and filler without leaking across order states and leading to High: valid activity resolves to the wrong beneficiary, wrong token amount, wrong reward amount, or wrong order value?

## Target
- File/function: evm/src/apps/intentsv2/IntrinsicIntents.sol::_fillSameChain
- Entrypoint: IntentGatewayV2 public order lifecycle
- Attacker controls: order fields, predispatch assets and call data, output amounts, cancellation height, relayer-fee values, and session signatures
- Exploit idea: Send surplus, dust, protocol fees, or native refunds to the wrong recipient or account them twice. Use a batch with one honest item and one duplicated or reordered item to see whether unique-item assumptions collapse.
- Invariant to test: surplus and dust must follow one deterministic split between beneficiary, protocol, and filler without leaking across order states
- Expected Immunefi impact: High: valid activity resolves to the wrong beneficiary, wrong token amount, wrong reward amount, or wrong order value.
- Fast validation: Overpay outputs, exercise native refund paths, and assert protocol share, beneficiary share, and filler refunds add up exactly once. Write a focused batch test with repeated indices or commitments and assert only unique authenticated items can affect state.
