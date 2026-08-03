# Q131: FillOrder Detection Bypass Across Mixed Context

## Question
Can an unprivileged attacker enter through `EntryPoint.handleOps(userOps) -> SolverAccount.validateUserOp` with attacker-controlled user-operation calldata, signature bytes, nonce and session material, executionData, and missingAccountFunds and mixing bytes that were valid in one proof, chain, module, order, beneficiary, or signature context with metadata interpreted in another context, and make `_containsFillOrder` smuggle a fill-order call through validation without triggering the intended solver-selection checks so `the validation result for a user operation that can reach fill-order execution` becomes inconsistent with `the stricter validation outcome required for fill-order flows`, breaking the invariant that every user operation that can reach fill-order execution must either satisfy the intent-selection path or fail validation outright and leading to Critical: unauthorized order fill or escrow release through the solver-account path?

## Target
- File/function: evm/src/apps/intentsv2/SolverAccount.sol::_containsFillOrder
- Entrypoint: EntryPoint.handleOps(userOps) -> SolverAccount.validateUserOp
- Attacker controls: user-operation calldata, signature bytes, nonce and session material, executionData, and missingAccountFunds
- Exploit idea: Smuggle a fill-order call through validation without triggering the intended solver-selection checks. Try a pair of otherwise valid artifacts where one verification step authenticates the old context and a later step consumes the new context.
- Invariant to test: every user operation that can reach fill-order execution must either satisfy the intent-selection path or fail validation outright
- Expected Immunefi impact: Critical: unauthorized order fill or escrow release through the solver-account path.
- Fast validation: Encode fillOrder through alternate execute batches or calldata wrappers and assert validation still recognizes and blocks the unauthorized path. Build two neighboring valid contexts and mutate only the binding field while asserting validation, state, and balances stay unchanged.
