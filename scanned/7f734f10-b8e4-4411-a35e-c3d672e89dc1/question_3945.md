# Q3945: Callback Context Bleed After Partial State Change

## Question
Can an unprivileged attacker enter through `HandlerV2.handlePostRequests(IHost host, request) -> token onAccept` with attacker-controlled public send parameters, bridged message bodies, beneficiary bytes, callback data, and relayer-fee inputs and replaying the same public flow after one part of storage changed and another part did not, and make `onAccept` execute attached callback data under the wrong token, beneficiary, or post-mint/post-unlock context so `the callback execution context` becomes inconsistent with `the exact transfer that authenticated the callback payload`, breaking the invariant that arbitrary callback data must run only after the right transfer is finalized and only with the exact beneficiary and amount that message authenticated and leading to Critical: wrongful mint, unlock, withdrawal, refund, or redirect of protocol-controlled or user escrowed assets?

## Target
- File/function: sdk/packages/core/contracts/apps/HyperFungibleToken.sol::onAccept
- Entrypoint: HandlerV2.handlePostRequests(IHost host, request) -> token onAccept
- Attacker controls: public send parameters, bridged message bodies, beneficiary bytes, callback data, and relayer-fee inputs
- Exploit idea: Execute attached callback data under the wrong token, beneficiary, or post-mint/post-unlock context. Drive a partial success or revert path first, then replay the same user-controlled input and check whether stale state is reused.
- Invariant to test: arbitrary callback data must run only after the right transfer is finalized and only with the exact beneficiary and amount that message authenticated
- Expected Immunefi impact: Critical: wrongful mint, unlock, withdrawal, refund, or redirect of protocol-controlled or user escrowed assets.
- Fast validation: Attach callback data to a bridged transfer, vary the token or beneficiary context around it, and assert callback execution cannot seize unrelated balances. Exercise a success-then-replay or fail-then-replay sequence and assert claimed flags, receipts, escrow, and nonces stay coherent.
