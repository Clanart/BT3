# Q448: Spread Normalization Drift With Duplicate Or Reordered Items

## Question
Can an unprivileged attacker enter through `IntentGatewayV2.fillOrder(order, options) -> VWAPOracle.recordSpread` with attacker-controlled fill-path inputs and outputs, source-chain bytes, token decimal updates, authenticated governance messages, and replay ordering and placing duplicate or reordered leaves, signatures, requests, responses, timeouts, or commitments inside one user-accessible batch, and make `recordSpread` normalize input and output amounts under mismatched decimal assumptions and persist a wrong spread so `the stored cumulative spread` becomes inconsistent with `the spread implied by the real token decimals and amounts filled`, breaking the invariant that spread tracking must compare like-for-like normalized amounts and must not let decimal mismatches poison later pricing decisions and leading to High: valid activity resolves to the wrong beneficiary, wrong token amount, wrong reward amount, or wrong order value?

## Target
- File/function: evm/src/utils/VWAPOracle.sol::recordSpread
- Entrypoint: IntentGatewayV2.fillOrder(order, options) -> VWAPOracle.recordSpread
- Attacker controls: fill-path inputs and outputs, source-chain bytes, token decimal updates, authenticated governance messages, and replay ordering
- Exploit idea: Normalize input and output amounts under mismatched decimal assumptions and persist a wrong spread. Use a batch with one honest item and one duplicated or reordered item to see whether unique-item assumptions collapse.
- Invariant to test: spread tracking must compare like-for-like normalized amounts and must not let decimal mismatches poison later pricing decisions
- Expected Immunefi impact: High: valid activity resolves to the wrong beneficiary, wrong token amount, wrong reward amount, or wrong order value.
- Fast validation: Record spreads across tokens with boundary decimals and assert the stored spread matches manual normalization exactly. Write a focused batch test with repeated indices or commitments and assert only unique authenticated items can affect state.
