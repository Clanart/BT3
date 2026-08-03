# Q3944: Callback Context Bleed With Duplicate Or Reordered Items

## Question
Can an unprivileged attacker enter through `HandlerV2.handlePostRequests(IHost host, request) -> token onAccept` with attacker-controlled public send parameters, bridged message bodies, beneficiary bytes, callback data, and relayer-fee inputs and placing duplicate or reordered leaves, signatures, requests, responses, timeouts, or commitments inside one user-accessible batch, and make `onAccept` execute attached callback data under the wrong token, beneficiary, or post-mint/post-unlock context so `the callback execution context` becomes inconsistent with `the exact transfer that authenticated the callback payload`, breaking the invariant that arbitrary callback data must run only after the right transfer is finalized and only with the exact beneficiary and amount that message authenticated and leading to Critical: wrongful mint, unlock, withdrawal, refund, or redirect of protocol-controlled or user escrowed assets?

## Target
- File/function: sdk/packages/core/contracts/apps/HyperFungibleToken.sol::onAccept
- Entrypoint: HandlerV2.handlePostRequests(IHost host, request) -> token onAccept
- Attacker controls: public send parameters, bridged message bodies, beneficiary bytes, callback data, and relayer-fee inputs
- Exploit idea: Execute attached callback data under the wrong token, beneficiary, or post-mint/post-unlock context. Use a batch with one honest item and one duplicated or reordered item to see whether unique-item assumptions collapse.
- Invariant to test: arbitrary callback data must run only after the right transfer is finalized and only with the exact beneficiary and amount that message authenticated
- Expected Immunefi impact: Critical: wrongful mint, unlock, withdrawal, refund, or redirect of protocol-controlled or user escrowed assets.
- Fast validation: Attach callback data to a bridged transfer, vary the token or beneficiary context around it, and assert callback execution cannot seize unrelated balances. Write a focused batch test with repeated indices or commitments and assert only unique authenticated items can affect state.
