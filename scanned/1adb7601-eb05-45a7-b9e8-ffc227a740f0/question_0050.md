# Q50: Dust Or Surplus Misrouting By Reusing Data Cross Module

## Question
Can an unprivileged attacker enter through `IntentGatewayV2.placeOrder / select / fillOrder / cancelOrder` with attacker-controlled order fields, predispatch assets and call data, output amounts, cancellation height, relayer-fee values, and session signatures and reusing data that should belong to one chain, module, order, state machine, or payee in another publicly reachable path, and make `fillOrder` send surplus, dust, protocol fees, or native refunds to the wrong recipient or account them twice so `the beneficiary and protocol share of excess value` becomes inconsistent with `the split encoded by order fields and configured fee parameters`, breaking the invariant that surplus and dust must follow one deterministic split between beneficiary, protocol, and filler without leaking across order states and leading to High: valid activity resolves to the wrong beneficiary, wrong token amount, wrong reward amount, or wrong order value?

## Target
- File/function: evm/src/apps/IntentGatewayV2.sol::fillOrder
- Entrypoint: IntentGatewayV2.placeOrder / select / fillOrder / cancelOrder
- Attacker controls: order fields, predispatch assets and call data, output amounts, cancellation height, relayer-fee values, and session signatures
- Exploit idea: Send surplus, dust, protocol fees, or native refunds to the wrong recipient or account them twice. Craft two public flows that share one byte string or hash and check whether module, chain, or payee binding is enforced everywhere.
- Invariant to test: surplus and dust must follow one deterministic split between beneficiary, protocol, and filler without leaking across order states
- Expected Immunefi impact: High: valid activity resolves to the wrong beneficiary, wrong token amount, wrong reward amount, or wrong order value.
- Fast validation: Overpay outputs, exercise native refund paths, and assert protocol share, beneficiary share, and filler refunds add up exactly once. Feed the same bytes through two reachable entrypoints and assert the second path rejects instead of inheriting the first path's authorization.
