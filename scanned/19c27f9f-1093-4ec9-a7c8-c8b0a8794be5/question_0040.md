# Q40: Partial-Fill Accounting Drift With Duplicate Or Reordered Items

## Question
Can an unprivileged attacker enter through `IntentGatewayV2.placeOrder / select / fillOrder / cancelOrder` with attacker-controlled order fields, predispatch assets and call data, output amounts, cancellation height, relayer-fee values, and session signatures and placing duplicate or reordered leaves, signatures, requests, responses, timeouts, or commitments inside one user-accessible batch, and make `fillOrder` release more escrow than corresponds to the newly supplied output or leave the order in a state that can later release the same escrow again so `the escrow released for one partial or full fill` becomes inconsistent with `the proportional amount earned by the filler for that specific fill step`, breaking the invariant that partial fills must release escrow proportionally, exactly once, and must leave the remaining order state cancelable or fillable without reuse and leading to Critical: wrongful mint, unlock, withdrawal, refund, or redirect of protocol-controlled or user escrowed assets?

## Target
- File/function: evm/src/apps/IntentGatewayV2.sol::fillOrder
- Entrypoint: IntentGatewayV2.placeOrder / select / fillOrder / cancelOrder
- Attacker controls: order fields, predispatch assets and call data, output amounts, cancellation height, relayer-fee values, and session signatures
- Exploit idea: Release more escrow than corresponds to the newly supplied output or leave the order in a state that can later release the same escrow again. Use a batch with one honest item and one duplicated or reordered item to see whether unique-item assumptions collapse.
- Invariant to test: partial fills must release escrow proportionally, exactly once, and must leave the remaining order state cancelable or fillable without reuse
- Expected Immunefi impact: Critical: wrongful mint, unlock, withdrawal, refund, or redirect of protocol-controlled or user escrowed assets.
- Fast validation: Perform partial fills at boundary amounts, then cancel or complete the order and assert total released escrow never exceeds the original escrow. Write a focused batch test with repeated indices or commitments and assert only unique authenticated items can affect state.
