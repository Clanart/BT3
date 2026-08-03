# Q383: Recursive Dispatch Abuse After Partial State Change

## Question
Can an unprivileged attacker enter through `CallDispatcher.dispatch(encodedCalls)` with attacker-controlled arbitrary call arrays, target addresses, calldata, call values, and any residual dispatcher balances or approvals and replaying the same public flow after one part of storage changed and another part did not, and make `dispatch` turn the dispatcher back on itself to bypass whatever sequencing the calling flow expected so `the intended one-pass call sequence` becomes inconsistent with `a single non-recursive call graph for the active flow`, breaking the invariant that dispatcher recursion or self-targeting must not let attackers reorder, duplicate, or widen the active protocol call graph and leading to High: duplicate settlement, duplicate claim, or double execution of a one-time flow?

## Target
- File/function: evm/src/utils/CallDispatcher.sol::dispatch
- Entrypoint: CallDispatcher.dispatch(encodedCalls)
- Attacker controls: arbitrary call arrays, target addresses, calldata, call values, and any residual dispatcher balances or approvals
- Exploit idea: Turn the dispatcher back on itself to bypass whatever sequencing the calling flow expected. Drive a partial success or revert path first, then replay the same user-controlled input and check whether stale state is reused.
- Invariant to test: dispatcher recursion or self-targeting must not let attackers reorder, duplicate, or widen the active protocol call graph
- Expected Immunefi impact: High: duplicate settlement, duplicate claim, or double execution of a one-time flow.
- Fast validation: Build a payload that targets the dispatcher itself or a contract that re-invokes it and assert recursion cannot duplicate value-moving side effects. Exercise a success-then-replay or fail-then-replay sequence and assert claimed flags, receipts, escrow, and nonces stay coherent.
