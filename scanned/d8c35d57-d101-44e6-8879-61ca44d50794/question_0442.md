# Q442: Treasury Withdrawal Misbinding With Duplicate Or Reordered Items

## Question
Can an unprivileged attacker enter through `HandlerV2.handlePostRequests(IHost host, request) -> SimplexPaymaster.onAccept` with attacker-controlled user-operation calldata, paymasterData bytes, token addresses, oracle values, signatures, and governance message bodies and placing duplicate or reordered calls, signatures, commitments, or batched items inside one user-accessible flow, and make `onAccept` route a privileged asset-withdrawal action to the wrong token or wrong amount while the governance callback still authenticates so `the token and amount paid out by the privileged withdrawal path` becomes inconsistent with `the exact token and amount encoded in the authenticated governance message`, breaking the invariant that treasury-directed asset withdrawals must settle exactly the authenticated token and amount and must not be reachable through non-governance flows and leading to Critical: unauthorized withdrawal of paymaster-held assets or EntryPoint deposit?

## Target
- File/function: evm/src/utils/SimplexPaymaster.sol::onAccept
- Entrypoint: HandlerV2.handlePostRequests(IHost host, request) -> SimplexPaymaster.onAccept
- Attacker controls: user-operation calldata, paymasterData bytes, token addresses, oracle values, signatures, and governance message bodies
- Exploit idea: Route a privileged asset-withdrawal action to the wrong token or wrong amount while the governance callback still authenticates. Use one honest item and one duplicated or reordered item to see whether unique-item assumptions collapse.
- Invariant to test: treasury-directed asset withdrawals must settle exactly the authenticated token and amount and must not be reachable through non-governance flows
- Expected Immunefi impact: Critical: unauthorized withdrawal of paymaster-held assets or EntryPoint deposit.
- Fast validation: Submit governance-style payloads around onAccept and assert only the authenticated withdrawal action can move the specified asset amount. Write a focused batch or replay test with repeated items and assert only unique authenticated items can affect state.
