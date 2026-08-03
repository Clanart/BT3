# Q3967: Beneficiary Decoding Error After Partial State Change

## Question
Can an unprivileged attacker enter through `HandlerV2.handlePostRequests(IHost host, request) -> token onAccept` with attacker-controlled public send parameters, bridged message bodies, beneficiary bytes, callback data, and relayer-fee inputs and replaying the same public flow after one part of storage changed and another part did not, and make `onAccept` decode attacker-controlled beneficiary bytes into the wrong recipient while keeping the rest of the message valid so `the beneficiary address paid on the destination or refund path` becomes inconsistent with `the exact recipient encoded in the authenticated message`, breaking the invariant that beneficiary decoding must be length-safe, format-safe, and must not reinterpret bytes under a second address model and leading to High: valid activity resolves to the wrong beneficiary, wrong token amount, wrong reward amount, or wrong order value?

## Target
- File/function: sdk/packages/core/contracts/apps/HyperFungibleTokenUpgradeable.sol::onAccept
- Entrypoint: HandlerV2.handlePostRequests(IHost host, request) -> token onAccept
- Attacker controls: public send parameters, bridged message bodies, beneficiary bytes, callback data, and relayer-fee inputs
- Exploit idea: Decode attacker-controlled beneficiary bytes into the wrong recipient while keeping the rest of the message valid. Drive a partial success or revert path first, then replay the same user-controlled input and check whether stale state is reused.
- Invariant to test: beneficiary decoding must be length-safe, format-safe, and must not reinterpret bytes under a second address model
- Expected Immunefi impact: High: valid activity resolves to the wrong beneficiary, wrong token amount, wrong reward amount, or wrong order value.
- Fast validation: Fuzz beneficiary byte lengths and edge encodings and assert only the intended 20-byte or account-id format can settle. Exercise a success-then-replay or fail-then-replay sequence and assert claimed flags, receipts, escrow, and nonces stay coherent.
