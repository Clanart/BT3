# Q3932: Accept-And-Timeout Double Settlement With Duplicate Or Reordered Items

## Question
Can an unprivileged attacker enter through `HandlerV2.handlePostRequestTimeouts(IHost host, message) -> token onPostRequestTimeout` with attacker-controlled public send parameters, bridged message bodies, beneficiary bytes, callback data, and relayer-fee inputs and placing duplicate or reordered leaves, signatures, requests, responses, timeouts, or commitments inside one user-accessible batch, and make `onPostRequestTimeout` make both inbound delivery and timeout refund succeed for the same bridged transfer so `the one-time settlement state for one transfer` becomes inconsistent with `a single final outcome of delivered or timed out`, breaking the invariant that a bridged transfer must end either in delivery or in refund, never in both and leading to Critical: wrongful mint, unlock, withdrawal, refund, or redirect of protocol-controlled or user escrowed assets?

## Target
- File/function: sdk/packages/core/contracts/apps/HyperFungibleToken.sol::onPostRequestTimeout
- Entrypoint: HandlerV2.handlePostRequestTimeouts(IHost host, message) -> token onPostRequestTimeout
- Attacker controls: public send parameters, bridged message bodies, beneficiary bytes, callback data, and relayer-fee inputs
- Exploit idea: Make both inbound delivery and timeout refund succeed for the same bridged transfer. Use a batch with one honest item and one duplicated or reordered item to see whether unique-item assumptions collapse.
- Invariant to test: a bridged transfer must end either in delivery or in refund, never in both
- Expected Immunefi impact: Critical: wrongful mint, unlock, withdrawal, refund, or redirect of protocol-controlled or user escrowed assets.
- Fast validation: Drive a delivery path, then a timeout path for the same commitment, and assert total supply, custody balances, and user balances reflect only one outcome. Write a focused batch test with repeated indices or commitments and assert only unique authenticated items can affect state.
