# Q433: Registered-Token Bypass Across Mixed Context

## Question
Can an unprivileged attacker enter through `EntryPoint.handleOps(userOps) -> SimplexPaymaster validation path` with attacker-controlled user-operation calldata, paymasterData bytes, token addresses, oracle values, signatures, and governance message bodies and mixing bytes that were valid in one proof, chain, module, order, beneficiary, or signature context with metadata interpreted in another context, and make `_fetchDetails` pay with or validate against a token path that governance never registered or has already deactivated so `the active-token gate used during validation and charging` becomes inconsistent with `the token registry and active flag configured by governance`, breaking the invariant that validation and charging must reject any token not explicitly registered and active for new operations and leading to High: unauthorized gas sponsorship or wrong token-based charging?

## Target
- File/function: evm/src/utils/SimplexPaymaster.sol::_fetchDetails
- Entrypoint: EntryPoint.handleOps(userOps) -> SimplexPaymaster validation path
- Attacker controls: user-operation calldata, paymasterData bytes, token addresses, oracle values, signatures, and governance message bodies
- Exploit idea: Pay with or validate against a token path that governance never registered or has already deactivated. Try a pair of otherwise valid artifacts where one verification step authenticates the old context and a later step consumes the new context.
- Invariant to test: validation and charging must reject any token not explicitly registered and active for new operations
- Expected Immunefi impact: High: unauthorized gas sponsorship or wrong token-based charging.
- Fast validation: Deactivate or omit a token, then try alternate validation modes and assert the inactive token can neither pass validation nor influence pricing. Build two neighboring valid contexts and mutate only the binding field while asserting validation, state, and balances stay unchanged.
