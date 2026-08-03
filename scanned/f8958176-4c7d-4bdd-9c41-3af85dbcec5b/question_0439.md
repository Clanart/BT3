# Q439: Paymaster Mode Confusion After Partial State Change

## Question
Can an unprivileged attacker enter through `EntryPoint.handleOps(userOps) -> SimplexPaymaster validation path` with attacker-controlled user-operation calldata, paymasterData bytes, token addresses, oracle values, signatures, and governance message bodies and replaying the same public flow after one part of storage changed and another part did not, and make `_validatePaymasterUserOp` reuse bytes that were valid for one paymaster mode under another mode with different security assumptions so `the paymaster mode selected by validation` becomes inconsistent with `the exact mode and token path the user operation encoded`, breaking the invariant that mode selection must stay bound to one decode path so validation cannot reinterpret bytes under a weaker authorization model and leading to Critical: attacker reaches a cheaper or less authenticated sponsorship path than the encoded mode intended?

## Target
- File/function: evm/src/utils/SimplexPaymaster.sol::_validatePaymasterUserOp
- Entrypoint: EntryPoint.handleOps(userOps) -> SimplexPaymaster validation path
- Attacker controls: user-operation calldata, paymasterData bytes, token addresses, oracle values, signatures, and governance message bodies
- Exploit idea: Reuse bytes that were valid for one paymaster mode under another mode with different security assumptions. Drive a partial success or revert path first, then replay the same user-controlled input and check whether stale state is reused.
- Invariant to test: mode selection must stay bound to one decode path so validation cannot reinterpret bytes under a weaker authorization model
- Expected Immunefi impact: Critical: attacker reaches a cheaper or less authenticated sponsorship path than the encoded mode intended.
- Fast validation: Replay the same bytes across supported paymaster modes and assert validation never accepts them under more than one interpretation. Exercise a success-then-replay or fail-then-replay sequence and assert claimed flags, validation state, and balances stay coherent.
