# Q426: PostOp Deposit Drain With Duplicate Or Reordered Items

## Question
Can an unprivileged attacker enter through `EntryPoint.handleOps(userOps) -> SimplexPaymaster validation path` with attacker-controlled user-operation calldata, paymasterData bytes, token addresses, oracle values, signatures, and governance message bodies and placing duplicate or reordered calls, signatures, commitments, or batched items inside one user-accessible flow, and make `_validatePaymasterUserOp` make the paymaster consume more EntryPoint deposit than the validated operation should expose so `the EntryPoint deposit drawn under one user operation` becomes inconsistent with `the bounded postOp and prefund cost the operation committed to pay`, breaking the invariant that validation and execution must cap postOp exposure so a user-controlled operation cannot drain shared paymaster deposit beyond its own charge and leading to Critical: attacker drains the paymaster deposit or forces unauthorized subsidy?

## Target
- File/function: evm/src/utils/SimplexPaymaster.sol::_validatePaymasterUserOp
- Entrypoint: EntryPoint.handleOps(userOps) -> SimplexPaymaster validation path
- Attacker controls: user-operation calldata, paymasterData bytes, token addresses, oracle values, signatures, and governance message bodies
- Exploit idea: Make the paymaster consume more EntryPoint deposit than the validated operation should expose. Use one honest item and one duplicated or reordered item to see whether unique-item assumptions collapse.
- Invariant to test: validation and execution must cap postOp exposure so a user-controlled operation cannot drain shared paymaster deposit beyond its own charge
- Expected Immunefi impact: Critical: attacker drains the paymaster deposit or forces unauthorized subsidy.
- Fast validation: Drive high-gas and revert-heavy paths and assert deposit draw stays within the configured postOp cap and per-operation token charge. Write a focused batch or replay test with repeated items and assert only unique authenticated items can affect state.
