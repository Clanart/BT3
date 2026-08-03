# Q75: Dust Or Surplus Misrouting After Partial State Change

## Question
Can an unprivileged attacker enter through `IntentGatewayV2 public order lifecycle` with attacker-controlled order fields, predispatch assets and call data, output amounts, cancellation height, relayer-fee values, and session signatures and replaying the same public flow after one part of storage changed and another part did not, and make `_fillCrossChain` send surplus, dust, protocol fees, or native refunds to the wrong recipient or account them twice so `the beneficiary and protocol share of excess value` becomes inconsistent with `the split encoded by order fields and configured fee parameters`, breaking the invariant that surplus and dust must follow one deterministic split between beneficiary, protocol, and filler without leaking across order states and leading to High: valid activity resolves to the wrong beneficiary, wrong token amount, wrong reward amount, or wrong order value?

## Target
- File/function: evm/src/apps/intentsv2/ExtrinsicIntents.sol::_fillCrossChain
- Entrypoint: IntentGatewayV2 public order lifecycle
- Attacker controls: order fields, predispatch assets and call data, output amounts, cancellation height, relayer-fee values, and session signatures
- Exploit idea: Send surplus, dust, protocol fees, or native refunds to the wrong recipient or account them twice. Drive a partial success or revert path first, then replay the same user-controlled input and check whether stale state is reused.
- Invariant to test: surplus and dust must follow one deterministic split between beneficiary, protocol, and filler without leaking across order states
- Expected Immunefi impact: High: valid activity resolves to the wrong beneficiary, wrong token amount, wrong reward amount, or wrong order value.
- Fast validation: Overpay outputs, exercise native refund paths, and assert protocol share, beneficiary share, and filler refunds add up exactly once. Exercise a success-then-replay or fail-then-replay sequence and assert claimed flags, receipts, escrow, and nonces stay coherent.
