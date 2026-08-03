# Q441: Treasury Withdrawal Misbinding Across Mixed Context

## Question
Can an unprivileged attacker enter through `HandlerV2.handlePostRequests(IHost host, request) -> SimplexPaymaster.onAccept` with attacker-controlled user-operation calldata, paymasterData bytes, token addresses, oracle values, signatures, and governance message bodies and mixing bytes that were valid in one proof, chain, module, order, beneficiary, or signature context with metadata interpreted in another context, and make `onAccept` route a privileged asset-withdrawal action to the wrong token or wrong amount while the governance callback still authenticates so `the token and amount paid out by the privileged withdrawal path` becomes inconsistent with `the exact token and amount encoded in the authenticated governance message`, breaking the invariant that treasury-directed asset withdrawals must settle exactly the authenticated token and amount and must not be reachable through non-governance flows and leading to Critical: unauthorized withdrawal of paymaster-held assets or EntryPoint deposit?

## Target
- File/function: evm/src/utils/SimplexPaymaster.sol::onAccept
- Entrypoint: HandlerV2.handlePostRequests(IHost host, request) -> SimplexPaymaster.onAccept
- Attacker controls: user-operation calldata, paymasterData bytes, token addresses, oracle values, signatures, and governance message bodies
- Exploit idea: Route a privileged asset-withdrawal action to the wrong token or wrong amount while the governance callback still authenticates. Try a pair of otherwise valid artifacts where one verification step authenticates the old context and a later step consumes the new context.
- Invariant to test: treasury-directed asset withdrawals must settle exactly the authenticated token and amount and must not be reachable through non-governance flows
- Expected Immunefi impact: Critical: unauthorized withdrawal of paymaster-held assets or EntryPoint deposit.
- Fast validation: Submit governance-style payloads around onAccept and assert only the authenticated withdrawal action can move the specified asset amount. Build two neighboring valid contexts and mutate only the binding field while asserting validation, state, and balances stay unchanged.
