# Q461: Duplicate Fill Replay After Partial State Change

## Question
Can an unprivileged attacker enter through `IntentGatewayV2.fillOrder(order, options) -> VWAPOracle.recordSpread` with attacker-controlled fill-path inputs and outputs, source-chain bytes, token decimal updates, authenticated governance messages, and replay ordering and replaying the same public flow after one part of storage changed and another part did not, and make `recordSpread` count the same economic fill twice after a retry or neighboring path replay so `the one-time spread contribution of one fill` becomes inconsistent with `one authenticated accounting of that fill`, breaking the invariant that spread updates must not be replayable across public fill, retry, or callback paths and leading to High: duplicate settlement, duplicate claim, or double execution of a one-time flow?

## Target
- File/function: evm/src/utils/VWAPOracle.sol::recordSpread
- Entrypoint: IntentGatewayV2.fillOrder(order, options) -> VWAPOracle.recordSpread
- Attacker controls: fill-path inputs and outputs, source-chain bytes, token decimal updates, authenticated governance messages, and replay ordering
- Exploit idea: Count the same economic fill twice after a retry or neighboring path replay. Drive a partial success or revert path first, then replay the same user-controlled input and check whether stale state is reused.
- Invariant to test: spread updates must not be replayable across public fill, retry, or callback paths
- Expected Immunefi impact: High: duplicate settlement, duplicate claim, or double execution of a one-time flow.
- Fast validation: Trigger one fill and any reachable retry path and assert the spread bucket changes only once. Exercise a success-then-replay or fail-then-replay sequence and assert claimed flags, receipts, escrow, and nonces stay coherent.
