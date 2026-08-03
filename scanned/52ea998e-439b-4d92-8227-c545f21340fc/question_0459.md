# Q459: Duplicate Fill Replay Across Mixed Context

## Question
Can an unprivileged attacker enter through `IntentGatewayV2.fillOrder(order, options) -> VWAPOracle.recordSpread` with attacker-controlled fill-path inputs and outputs, source-chain bytes, token decimal updates, authenticated governance messages, and replay ordering and mixing bytes that were valid in one proof, chain, module, order, or beneficiary context with metadata interpreted in another context, and make `recordSpread` count the same economic fill twice after a retry or neighboring path replay so `the one-time spread contribution of one fill` becomes inconsistent with `one authenticated accounting of that fill`, breaking the invariant that spread updates must not be replayable across public fill, retry, or callback paths and leading to High: duplicate settlement, duplicate claim, or double execution of a one-time flow?

## Target
- File/function: evm/src/utils/VWAPOracle.sol::recordSpread
- Entrypoint: IntentGatewayV2.fillOrder(order, options) -> VWAPOracle.recordSpread
- Attacker controls: fill-path inputs and outputs, source-chain bytes, token decimal updates, authenticated governance messages, and replay ordering
- Exploit idea: Count the same economic fill twice after a retry or neighboring path replay. Try a pair of otherwise valid artifacts where one verification step authenticates the old context and a later step consumes the new context.
- Invariant to test: spread updates must not be replayable across public fill, retry, or callback paths
- Expected Immunefi impact: High: duplicate settlement, duplicate claim, or double execution of a one-time flow.
- Fast validation: Trigger one fill and any reachable retry path and assert the spread bucket changes only once. Build two neighboring valid contexts and mutate only the binding field while asserting state, receipts, and balances stay unchanged.
