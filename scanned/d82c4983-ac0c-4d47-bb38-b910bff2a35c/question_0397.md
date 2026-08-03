# Q397: Accept-And-Timeout Double Settlement After Partial State Change

## Question
Can an unprivileged attacker enter through `HandlerV2.handlePostRequests(IHost host, request) -> token onAccept` with attacker-controlled public send parameters, bridged message bodies, beneficiary bytes, callback data, and relayer-fee inputs and replaying the same public flow after one part of storage changed and another part did not, and make `gateway` make both inbound delivery and timeout refund succeed for the same bridged transfer so `the one-time settlement state for one transfer` becomes inconsistent with `a single final outcome of delivered or timed out`, breaking the invariant that a bridged transfer must end either in delivery or in refund, never in both and leading to Critical: wrongful mint, unlock, withdrawal, refund, or redirect of protocol-controlled or user escrowed assets?

## Target
- File/function: evm/src/utils/HyperFungibleTokenImpl.sol::gateway
- Entrypoint: HandlerV2.handlePostRequests(IHost host, request) -> token onAccept
- Attacker controls: public send parameters, bridged message bodies, beneficiary bytes, callback data, and relayer-fee inputs
- Exploit idea: Make both inbound delivery and timeout refund succeed for the same bridged transfer. Drive a partial success or revert path first, then replay the same user-controlled input and check whether stale state is reused.
- Invariant to test: a bridged transfer must end either in delivery or in refund, never in both
- Expected Immunefi impact: Critical: wrongful mint, unlock, withdrawal, refund, or redirect of protocol-controlled or user escrowed assets.
- Fast validation: Drive a delivery path, then a timeout path for the same commitment, and assert total supply, custody balances, and user balances reflect only one outcome. Exercise a success-then-replay or fail-then-replay sequence and assert claimed flags, receipts, escrow, and nonces stay coherent.
