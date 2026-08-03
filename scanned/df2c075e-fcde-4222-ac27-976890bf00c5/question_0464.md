# Q464: Cross-Token Bucket Collision With Duplicate Or Reordered Items

## Question
Can an unprivileged attacker enter through `IntentGatewayV2.fillOrder(order, options) -> VWAPOracle.recordSpread` with attacker-controlled fill-path inputs and outputs, source-chain bytes, token decimal updates, authenticated governance messages, and replay ordering and placing duplicate or reordered leaves, signatures, requests, responses, timeouts, or commitments inside one user-accessible batch, and make `recordSpread` make one token's fill update the spread bucket of another token so `the token key used for spread accounting` becomes inconsistent with `the token actually involved in the filled order leg`, breaking the invariant that spread accounting must remain injective per `(sourceChain, token)` and never alias adjacent tokens and leading to High: valid activity resolves to the wrong beneficiary, wrong token amount, wrong reward amount, or wrong order value?

## Target
- File/function: evm/src/utils/VWAPOracle.sol::recordSpread
- Entrypoint: IntentGatewayV2.fillOrder(order, options) -> VWAPOracle.recordSpread
- Attacker controls: fill-path inputs and outputs, source-chain bytes, token decimal updates, authenticated governance messages, and replay ordering
- Exploit idea: Make one token's fill update the spread bucket of another token. Use a batch with one honest item and one duplicated or reordered item to see whether unique-item assumptions collapse.
- Invariant to test: spread accounting must remain injective per `(sourceChain, token)` and never alias adjacent tokens
- Expected Immunefi impact: High: valid activity resolves to the wrong beneficiary, wrong token amount, wrong reward amount, or wrong order value.
- Fast validation: Use adjacent token addresses and assert each bucket mutates only for its own fills. Write a focused batch test with repeated indices or commitments and assert only unique authenticated items can affect state.
