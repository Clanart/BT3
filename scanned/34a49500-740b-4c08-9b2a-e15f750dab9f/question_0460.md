# Q460: Duplicate Fill Replay With Duplicate Or Reordered Items

## Question
Can an unprivileged attacker enter through `IntentGatewayV2.fillOrder(order, options) -> VWAPOracle.recordSpread` with attacker-controlled fill-path inputs and outputs, source-chain bytes, token decimal updates, authenticated governance messages, and replay ordering and placing duplicate or reordered leaves, signatures, requests, responses, timeouts, or commitments inside one user-accessible batch, and make `recordSpread` count the same economic fill twice after a retry or neighboring path replay so `the one-time spread contribution of one fill` becomes inconsistent with `one authenticated accounting of that fill`, breaking the invariant that spread updates must not be replayable across public fill, retry, or callback paths and leading to High: duplicate settlement, duplicate claim, or double execution of a one-time flow?

## Target
- File/function: evm/src/utils/VWAPOracle.sol::recordSpread
- Entrypoint: IntentGatewayV2.fillOrder(order, options) -> VWAPOracle.recordSpread
- Attacker controls: fill-path inputs and outputs, source-chain bytes, token decimal updates, authenticated governance messages, and replay ordering
- Exploit idea: Count the same economic fill twice after a retry or neighboring path replay. Use a batch with one honest item and one duplicated or reordered item to see whether unique-item assumptions collapse.
- Invariant to test: spread updates must not be replayable across public fill, retry, or callback paths
- Expected Immunefi impact: High: duplicate settlement, duplicate claim, or double execution of a one-time flow.
- Fast validation: Trigger one fill and any reachable retry path and assert the spread bucket changes only once. Write a focused batch test with repeated indices or commitments and assert only unique authenticated items can affect state.
