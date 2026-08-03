# Q403: Beneficiary Decoding Error Across Mixed Context

## Question
Can an unprivileged attacker enter through `HandlerV2.handlePostRequests(IHost host, request) -> token onAccept` with attacker-controlled public send parameters, bridged message bodies, beneficiary bytes, callback data, and relayer-fee inputs and mixing bytes that were valid in one proof, chain, module, order, or beneficiary context with metadata interpreted in another context, and make `gateway` decode attacker-controlled beneficiary bytes into the wrong recipient while keeping the rest of the message valid so `the beneficiary address paid on the destination or refund path` becomes inconsistent with `the exact recipient encoded in the authenticated message`, breaking the invariant that beneficiary decoding must be length-safe, format-safe, and must not reinterpret bytes under a second address model and leading to High: valid activity resolves to the wrong beneficiary, wrong token amount, wrong reward amount, or wrong order value?

## Target
- File/function: evm/src/utils/HyperFungibleTokenImpl.sol::gateway
- Entrypoint: HandlerV2.handlePostRequests(IHost host, request) -> token onAccept
- Attacker controls: public send parameters, bridged message bodies, beneficiary bytes, callback data, and relayer-fee inputs
- Exploit idea: Decode attacker-controlled beneficiary bytes into the wrong recipient while keeping the rest of the message valid. Try a pair of otherwise valid artifacts where one verification step authenticates the old context and a later step consumes the new context.
- Invariant to test: beneficiary decoding must be length-safe, format-safe, and must not reinterpret bytes under a second address model
- Expected Immunefi impact: High: valid activity resolves to the wrong beneficiary, wrong token amount, wrong reward amount, or wrong order value.
- Fast validation: Fuzz beneficiary byte lengths and edge encodings and assert only the intended 20-byte or account-id format can settle. Build two neighboring valid contexts and mutate only the binding field while asserting state, receipts, and balances stay unchanged.
