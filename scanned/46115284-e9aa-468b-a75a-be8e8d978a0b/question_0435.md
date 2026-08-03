# Q435: Registered-Token Bypass After Partial State Change

## Question
Can an unprivileged attacker enter through `EntryPoint.handleOps(userOps) -> SimplexPaymaster validation path` with attacker-controlled user-operation calldata, paymasterData bytes, token addresses, oracle values, signatures, and governance message bodies and replaying the same public flow after one part of storage changed and another part did not, and make `_fetchDetails` pay with or validate against a token path that governance never registered or has already deactivated so `the active-token gate used during validation and charging` becomes inconsistent with `the token registry and active flag configured by governance`, breaking the invariant that validation and charging must reject any token not explicitly registered and active for new operations and leading to High: unauthorized gas sponsorship or wrong token-based charging?

## Target
- File/function: evm/src/utils/SimplexPaymaster.sol::_fetchDetails
- Entrypoint: EntryPoint.handleOps(userOps) -> SimplexPaymaster validation path
- Attacker controls: user-operation calldata, paymasterData bytes, token addresses, oracle values, signatures, and governance message bodies
- Exploit idea: Pay with or validate against a token path that governance never registered or has already deactivated. Drive a partial success or revert path first, then replay the same user-controlled input and check whether stale state is reused.
- Invariant to test: validation and charging must reject any token not explicitly registered and active for new operations
- Expected Immunefi impact: High: unauthorized gas sponsorship or wrong token-based charging.
- Fast validation: Deactivate or omit a token, then try alternate validation modes and assert the inactive token can neither pass validation nor influence pricing. Exercise a success-then-replay or fail-then-replay sequence and assert claimed flags, validation state, and balances stay coherent.
