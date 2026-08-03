# Q3962: Custody Versus Supply Drift With Duplicate Or Reordered Items

## Question
Can an unprivileged attacker enter through `HyperFungibleToken.send / WrappedHyperFungibleToken.send` with attacker-controlled public send parameters, bridged message bodies, beneficiary bytes, callback data, and relayer-fee inputs and placing duplicate or reordered leaves, signatures, requests, responses, timeouts, or commitments inside one user-accessible batch, and make `send` release, mint, burn, or refund a value that is inconsistent with what was actually escrowed or burned so `the bridged amount credited or released` becomes inconsistent with `the exact amount locked, burned, or proven in the authenticated message`, breaking the invariant that locked or burned value and released or minted value must match exactly once per transfer lifecycle and leading to Critical: wrongful mint, unlock, withdrawal, refund, or redirect of protocol-controlled or user escrowed assets?

## Target
- File/function: sdk/packages/core/contracts/apps/HyperFungibleTokenUpgradeable.sol::send
- Entrypoint: HyperFungibleToken.send / WrappedHyperFungibleToken.send
- Attacker controls: public send parameters, bridged message bodies, beneficiary bytes, callback data, and relayer-fee inputs
- Exploit idea: Release, mint, burn, or refund a value that is inconsistent with what was actually escrowed or burned. Use a batch with one honest item and one duplicated or reordered item to see whether unique-item assumptions collapse.
- Invariant to test: locked or burned value and released or minted value must match exactly once per transfer lifecycle
- Expected Immunefi impact: Critical: wrongful mint, unlock, withdrawal, refund, or redirect of protocol-controlled or user escrowed assets.
- Fast validation: Use fee-on-transfer, decimal, or replay edge cases and assert local supply plus remote custody remains conserved across send, receive, and timeout. Write a focused batch test with repeated indices or commitments and assert only unique authenticated items can affect state.
