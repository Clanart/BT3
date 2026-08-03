# Q133: FillOrder Detection Bypass After Partial State Change

## Question
Can an unprivileged attacker enter through `EntryPoint.handleOps(userOps) -> SolverAccount.validateUserOp` with attacker-controlled user-operation calldata, signature bytes, nonce and session material, executionData, and missingAccountFunds and replaying the same public flow after one part of storage changed and another part did not, and make `_containsFillOrder` smuggle a fill-order call through validation without triggering the intended solver-selection checks so `the validation result for a user operation that can reach fill-order execution` becomes inconsistent with `the stricter validation outcome required for fill-order flows`, breaking the invariant that every user operation that can reach fill-order execution must either satisfy the intent-selection path or fail validation outright and leading to Critical: unauthorized order fill or escrow release through the solver-account path?

## Target
- File/function: evm/src/apps/intentsv2/SolverAccount.sol::_containsFillOrder
- Entrypoint: EntryPoint.handleOps(userOps) -> SolverAccount.validateUserOp
- Attacker controls: user-operation calldata, signature bytes, nonce and session material, executionData, and missingAccountFunds
- Exploit idea: Smuggle a fill-order call through validation without triggering the intended solver-selection checks. Drive a partial success or revert path first, then replay the same user-controlled input and check whether stale state is reused.
- Invariant to test: every user operation that can reach fill-order execution must either satisfy the intent-selection path or fail validation outright
- Expected Immunefi impact: Critical: unauthorized order fill or escrow release through the solver-account path.
- Fast validation: Encode fillOrder through alternate execute batches or calldata wrappers and assert validation still recognizes and blocks the unauthorized path. Exercise a success-then-replay or fail-then-replay sequence and assert claimed flags, validation state, and balances stay coherent.
