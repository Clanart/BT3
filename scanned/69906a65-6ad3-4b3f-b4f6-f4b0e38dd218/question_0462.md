# Q462: Duplicate Fill Replay By Reusing Data Cross Module

## Question
Can an unprivileged attacker enter through `IntentGatewayV2.fillOrder(order, options) -> VWAPOracle.recordSpread` with attacker-controlled fill-path inputs and outputs, source-chain bytes, token decimal updates, authenticated governance messages, and replay ordering and reusing data that should belong to one chain, module, order, state machine, or payee in another publicly reachable path, and make `recordSpread` count the same economic fill twice after a retry or neighboring path replay so `the one-time spread contribution of one fill` becomes inconsistent with `one authenticated accounting of that fill`, breaking the invariant that spread updates must not be replayable across public fill, retry, or callback paths and leading to High: duplicate settlement, duplicate claim, or double execution of a one-time flow?

## Target
- File/function: evm/src/utils/VWAPOracle.sol::recordSpread
- Entrypoint: IntentGatewayV2.fillOrder(order, options) -> VWAPOracle.recordSpread
- Attacker controls: fill-path inputs and outputs, source-chain bytes, token decimal updates, authenticated governance messages, and replay ordering
- Exploit idea: Count the same economic fill twice after a retry or neighboring path replay. Craft two public flows that share one byte string or hash and check whether module, chain, or payee binding is enforced everywhere.
- Invariant to test: spread updates must not be replayable across public fill, retry, or callback paths
- Expected Immunefi impact: High: duplicate settlement, duplicate claim, or double execution of a one-time flow.
- Fast validation: Trigger one fill and any reachable retry path and assert the spread bucket changes only once. Feed the same bytes through two reachable entrypoints and assert the second path rejects instead of inheriting the first path's authorization.
