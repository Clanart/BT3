# Q3989: Custody Versus Supply Drift After Partial State Change

## Question
Can an unprivileged attacker enter through `HyperFungibleToken.send / WrappedHyperFungibleToken.send` with attacker-controlled public send parameters, bridged message bodies, beneficiary bytes, callback data, and relayer-fee inputs and replaying the same public flow after one part of storage changed and another part did not, and make `send` release, mint, burn, or refund a value that is inconsistent with what was actually escrowed or burned so `the bridged amount credited or released` becomes inconsistent with `the exact amount locked, burned, or proven in the authenticated message`, breaking the invariant that locked or burned value and released or minted value must match exactly once per transfer lifecycle and leading to Critical: wrongful mint, unlock, withdrawal, refund, or redirect of protocol-controlled or user escrowed assets?

## Target
- File/function: sdk/packages/core/contracts/apps/WrappedHyperFungibleToken.sol::send
- Entrypoint: HyperFungibleToken.send / WrappedHyperFungibleToken.send
- Attacker controls: public send parameters, bridged message bodies, beneficiary bytes, callback data, and relayer-fee inputs
- Exploit idea: Release, mint, burn, or refund a value that is inconsistent with what was actually escrowed or burned. Drive a partial success or revert path first, then replay the same user-controlled input and check whether stale state is reused.
- Invariant to test: locked or burned value and released or minted value must match exactly once per transfer lifecycle
- Expected Immunefi impact: Critical: wrongful mint, unlock, withdrawal, refund, or redirect of protocol-controlled or user escrowed assets.
- Fast validation: Use fee-on-transfer, decimal, or replay edge cases and assert local supply plus remote custody remains conserved across send, receive, and timeout. Exercise a success-then-replay or fail-then-replay sequence and assert claimed flags, receipts, escrow, and nonces stay coherent.
