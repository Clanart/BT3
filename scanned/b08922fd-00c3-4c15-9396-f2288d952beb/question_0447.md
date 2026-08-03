# Q447: Spread Normalization Drift Across Mixed Context

## Question
Can an unprivileged attacker enter through `IntentGatewayV2.fillOrder(order, options) -> VWAPOracle.recordSpread` with attacker-controlled fill-path inputs and outputs, source-chain bytes, token decimal updates, authenticated governance messages, and replay ordering and mixing bytes that were valid in one proof, chain, module, order, or beneficiary context with metadata interpreted in another context, and make `recordSpread` normalize input and output amounts under mismatched decimal assumptions and persist a wrong spread so `the stored cumulative spread` becomes inconsistent with `the spread implied by the real token decimals and amounts filled`, breaking the invariant that spread tracking must compare like-for-like normalized amounts and must not let decimal mismatches poison later pricing decisions and leading to High: valid activity resolves to the wrong beneficiary, wrong token amount, wrong reward amount, or wrong order value?

## Target
- File/function: evm/src/utils/VWAPOracle.sol::recordSpread
- Entrypoint: IntentGatewayV2.fillOrder(order, options) -> VWAPOracle.recordSpread
- Attacker controls: fill-path inputs and outputs, source-chain bytes, token decimal updates, authenticated governance messages, and replay ordering
- Exploit idea: Normalize input and output amounts under mismatched decimal assumptions and persist a wrong spread. Try a pair of otherwise valid artifacts where one verification step authenticates the old context and a later step consumes the new context.
- Invariant to test: spread tracking must compare like-for-like normalized amounts and must not let decimal mismatches poison later pricing decisions
- Expected Immunefi impact: High: valid activity resolves to the wrong beneficiary, wrong token amount, wrong reward amount, or wrong order value.
- Fast validation: Record spreads across tokens with boundary decimals and assert the stored spread matches manual normalization exactly. Build two neighboring valid contexts and mutate only the binding field while asserting state, receipts, and balances stay unchanged.
