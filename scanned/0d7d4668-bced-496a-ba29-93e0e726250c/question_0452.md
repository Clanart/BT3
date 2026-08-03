# Q452: Weighted-Volume Sign Bug With Duplicate Or Reordered Items

## Question
Can an unprivileged attacker enter through `IntentGatewayV2.fillOrder(order, options) -> VWAPOracle.recordSpread` with attacker-controlled fill-path inputs and outputs, source-chain bytes, token decimal updates, authenticated governance messages, and replay ordering and placing duplicate or reordered leaves, signatures, requests, responses, timeouts, or commitments inside one user-accessible batch, and make `recordSpread` update cumulative spread with the wrong sign or wrong weighted volume so one fill distorts later averages so `the weighted spread sum and total volume` becomes inconsistent with `the exact signed spread contribution of the authenticated fill`, breaking the invariant that each fill must contribute one correctly signed weighted spread and one correctly scaled volume to the cumulative average and leading to High: valid activity resolves to the wrong beneficiary, wrong token amount, wrong reward amount, or wrong order value?

## Target
- File/function: evm/src/utils/VWAPOracle.sol::recordSpread
- Entrypoint: IntentGatewayV2.fillOrder(order, options) -> VWAPOracle.recordSpread
- Attacker controls: fill-path inputs and outputs, source-chain bytes, token decimal updates, authenticated governance messages, and replay ordering
- Exploit idea: Update cumulative spread with the wrong sign or wrong weighted volume so one fill distorts later averages. Use a batch with one honest item and one duplicated or reordered item to see whether unique-item assumptions collapse.
- Invariant to test: each fill must contribute one correctly signed weighted spread and one correctly scaled volume to the cumulative average
- Expected Immunefi impact: High: valid activity resolves to the wrong beneficiary, wrong token amount, wrong reward amount, or wrong order value.
- Fast validation: Run fills above and below par and assert weighted sums, counts, and average spread follow manual arithmetic exactly. Write a focused batch test with repeated indices or commitments and assert only unique authenticated items can affect state.
