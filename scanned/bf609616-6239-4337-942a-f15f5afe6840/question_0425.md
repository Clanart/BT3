# Q425: PostOp Deposit Drain Across Mixed Context

## Question
Can an unprivileged attacker enter through `EntryPoint.handleOps(userOps) -> SimplexPaymaster validation path` with attacker-controlled user-operation calldata, paymasterData bytes, token addresses, oracle values, signatures, and governance message bodies and mixing bytes that were valid in one proof, chain, module, order, beneficiary, or signature context with metadata interpreted in another context, and make `_validatePaymasterUserOp` make the paymaster consume more EntryPoint deposit than the validated operation should expose so `the EntryPoint deposit drawn under one user operation` becomes inconsistent with `the bounded postOp and prefund cost the operation committed to pay`, breaking the invariant that validation and execution must cap postOp exposure so a user-controlled operation cannot drain shared paymaster deposit beyond its own charge and leading to Critical: attacker drains the paymaster deposit or forces unauthorized subsidy?

## Target
- File/function: evm/src/utils/SimplexPaymaster.sol::_validatePaymasterUserOp
- Entrypoint: EntryPoint.handleOps(userOps) -> SimplexPaymaster validation path
- Attacker controls: user-operation calldata, paymasterData bytes, token addresses, oracle values, signatures, and governance message bodies
- Exploit idea: Make the paymaster consume more EntryPoint deposit than the validated operation should expose. Try a pair of otherwise valid artifacts where one verification step authenticates the old context and a later step consumes the new context.
- Invariant to test: validation and execution must cap postOp exposure so a user-controlled operation cannot drain shared paymaster deposit beyond its own charge
- Expected Immunefi impact: Critical: attacker drains the paymaster deposit or forces unauthorized subsidy.
- Fast validation: Drive high-gas and revert-heavy paths and assert deposit draw stays within the configured postOp cap and per-operation token charge. Build two neighboring valid contexts and mutate only the binding field while asserting validation, state, and balances stay unchanged.
