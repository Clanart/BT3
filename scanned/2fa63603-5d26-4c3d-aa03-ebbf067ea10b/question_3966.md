# Q3966: Beneficiary Decoding Error With Duplicate Or Reordered Items

## Question
Can an unprivileged attacker enter through `HandlerV2.handlePostRequests(IHost host, request) -> token onAccept` with attacker-controlled public send parameters, bridged message bodies, beneficiary bytes, callback data, and relayer-fee inputs and placing duplicate or reordered leaves, signatures, requests, responses, timeouts, or commitments inside one user-accessible batch, and make `onAccept` decode attacker-controlled beneficiary bytes into the wrong recipient while keeping the rest of the message valid so `the beneficiary address paid on the destination or refund path` becomes inconsistent with `the exact recipient encoded in the authenticated message`, breaking the invariant that beneficiary decoding must be length-safe, format-safe, and must not reinterpret bytes under a second address model and leading to High: valid activity resolves to the wrong beneficiary, wrong token amount, wrong reward amount, or wrong order value?

## Target
- File/function: sdk/packages/core/contracts/apps/HyperFungibleTokenUpgradeable.sol::onAccept
- Entrypoint: HandlerV2.handlePostRequests(IHost host, request) -> token onAccept
- Attacker controls: public send parameters, bridged message bodies, beneficiary bytes, callback data, and relayer-fee inputs
- Exploit idea: Decode attacker-controlled beneficiary bytes into the wrong recipient while keeping the rest of the message valid. Use a batch with one honest item and one duplicated or reordered item to see whether unique-item assumptions collapse.
- Invariant to test: beneficiary decoding must be length-safe, format-safe, and must not reinterpret bytes under a second address model
- Expected Immunefi impact: High: valid activity resolves to the wrong beneficiary, wrong token amount, wrong reward amount, or wrong order value.
- Fast validation: Fuzz beneficiary byte lengths and edge encodings and assert only the intended 20-byte or account-id format can settle. Write a focused batch test with repeated indices or commitments and assert only unique authenticated items can affect state.
