# Q436: Registered-Token Bypass By Reusing Data Cross Module

## Question
Can an unprivileged attacker enter through `EntryPoint.handleOps(userOps) -> SimplexPaymaster validation path` with attacker-controlled user-operation calldata, paymasterData bytes, token addresses, oracle values, signatures, and governance message bodies and reusing data that should belong to one chain, module, account, or order in another publicly reachable path, and make `_fetchDetails` pay with or validate against a token path that governance never registered or has already deactivated so `the active-token gate used during validation and charging` becomes inconsistent with `the token registry and active flag configured by governance`, breaking the invariant that validation and charging must reject any token not explicitly registered and active for new operations and leading to High: unauthorized gas sponsorship or wrong token-based charging?

## Target
- File/function: evm/src/utils/SimplexPaymaster.sol::_fetchDetails
- Entrypoint: EntryPoint.handleOps(userOps) -> SimplexPaymaster validation path
- Attacker controls: user-operation calldata, paymasterData bytes, token addresses, oracle values, signatures, and governance message bodies
- Exploit idea: Pay with or validate against a token path that governance never registered or has already deactivated. Craft two public flows that share one byte string or hash and check whether module, chain, or account binding is enforced everywhere.
- Invariant to test: validation and charging must reject any token not explicitly registered and active for new operations
- Expected Immunefi impact: High: unauthorized gas sponsorship or wrong token-based charging.
- Fast validation: Deactivate or omit a token, then try alternate validation modes and assert the inactive token can neither pass validation nor influence pricing. Feed the same bytes through two reachable entrypoints and assert the second path rejects instead of inheriting the first path's authorization.
