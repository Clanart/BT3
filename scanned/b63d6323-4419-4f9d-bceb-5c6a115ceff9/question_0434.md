# Q434: Registered-Token Bypass With Duplicate Or Reordered Items

## Question
Can an unprivileged attacker enter through `EntryPoint.handleOps(userOps) -> SimplexPaymaster validation path` with attacker-controlled user-operation calldata, paymasterData bytes, token addresses, oracle values, signatures, and governance message bodies and placing duplicate or reordered calls, signatures, commitments, or batched items inside one user-accessible flow, and make `_fetchDetails` pay with or validate against a token path that governance never registered or has already deactivated so `the active-token gate used during validation and charging` becomes inconsistent with `the token registry and active flag configured by governance`, breaking the invariant that validation and charging must reject any token not explicitly registered and active for new operations and leading to High: unauthorized gas sponsorship or wrong token-based charging?

## Target
- File/function: evm/src/utils/SimplexPaymaster.sol::_fetchDetails
- Entrypoint: EntryPoint.handleOps(userOps) -> SimplexPaymaster validation path
- Attacker controls: user-operation calldata, paymasterData bytes, token addresses, oracle values, signatures, and governance message bodies
- Exploit idea: Pay with or validate against a token path that governance never registered or has already deactivated. Use one honest item and one duplicated or reordered item to see whether unique-item assumptions collapse.
- Invariant to test: validation and charging must reject any token not explicitly registered and active for new operations
- Expected Immunefi impact: High: unauthorized gas sponsorship or wrong token-based charging.
- Fast validation: Deactivate or omit a token, then try alternate validation modes and assert the inactive token can neither pass validation nor influence pricing. Write a focused batch or replay test with repeated items and assert only unique authenticated items can affect state.
