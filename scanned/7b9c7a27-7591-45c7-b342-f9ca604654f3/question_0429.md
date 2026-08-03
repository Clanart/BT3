# Q429: Governance-Module Misbinding Across Mixed Context

## Question
Can an unprivileged attacker enter through `HandlerV2.handlePostRequests(IHost host, request) -> SimplexPaymaster.onAccept` with attacker-controlled user-operation calldata, paymasterData bytes, token addresses, oracle values, signatures, and governance message bodies and mixing bytes that were valid in one proof, chain, module, order, beneficiary, or signature context with metadata interpreted in another context, and make `onAccept` accept a privileged paymaster update, token registration, deactivation, upgrade, or withdrawal request from the wrong Hyperbridge module so `the trusted cross-chain governance source for paymaster actions` becomes inconsistent with `the exact Hyperbridge module allowed to govern paymaster configuration`, breaking the invariant that cross-chain paymaster governance must authenticate both Hyperbridge as source chain and the intended governing module as source app and leading to Critical: unauthorized upgrade, token registration, token deactivation, or asset withdrawal?

## Target
- File/function: evm/src/utils/SimplexPaymaster.sol::onAccept
- Entrypoint: HandlerV2.handlePostRequests(IHost host, request) -> SimplexPaymaster.onAccept
- Attacker controls: user-operation calldata, paymasterData bytes, token addresses, oracle values, signatures, and governance message bodies
- Exploit idea: Accept a privileged paymaster update, token registration, deactivation, upgrade, or withdrawal request from the wrong Hyperbridge module. Try a pair of otherwise valid artifacts where one verification step authenticates the old context and a later step consumes the new context.
- Invariant to test: cross-chain paymaster governance must authenticate both Hyperbridge as source chain and the intended governing module as source app
- Expected Immunefi impact: Critical: unauthorized upgrade, token registration, token deactivation, or asset withdrawal.
- Fast validation: Deliver a governance-shaped message from the wrong Hyperbridge module and assert no upgrade, token registration, deactivation, or withdrawal occurs. Build two neighboring valid contexts and mutate only the binding field while asserting validation, state, and balances stay unchanged.
