# Q456: Commitment-Source Misassociation With Duplicate Or Reordered Items

## Question
Can an unprivileged attacker enter through `IntentGatewayV2.fillOrder(order, options) -> VWAPOracle.recordSpread` with attacker-controlled fill-path inputs and outputs, source-chain bytes, token decimal updates, authenticated governance messages, and replay ordering and placing duplicate or reordered leaves, signatures, requests, responses, timeouts, or commitments inside one user-accessible batch, and make `recordSpread` record a spread for one commitment under another sourceChain or token key so `the `(sourceChain, token)` bucket that receives the spread update` becomes inconsistent with `the exact chain and token pair implied by the filled order`, breaking the invariant that spread buckets must key updates to the exact chain and token that the fill authenticated and leading to High: valid activity resolves to the wrong beneficiary, wrong token amount, wrong reward amount, or wrong order value?

## Target
- File/function: evm/src/utils/VWAPOracle.sol::recordSpread
- Entrypoint: IntentGatewayV2.fillOrder(order, options) -> VWAPOracle.recordSpread
- Attacker controls: fill-path inputs and outputs, source-chain bytes, token decimal updates, authenticated governance messages, and replay ordering
- Exploit idea: Record a spread for one commitment under another sourcechain or token key. Use a batch with one honest item and one duplicated or reordered item to see whether unique-item assumptions collapse.
- Invariant to test: spread buckets must key updates to the exact chain and token that the fill authenticated
- Expected Immunefi impact: High: valid activity resolves to the wrong beneficiary, wrong token amount, wrong reward amount, or wrong order value.
- Fast validation: Feed two fills sharing one token on different chains and assert the two buckets remain isolated. Write a focused batch test with repeated indices or commitments and assert only unique authenticated items can affect state.
