# Q382: Recursive Dispatch Abuse With Duplicate Or Reordered Items

## Question
Can an unprivileged attacker enter through `CallDispatcher.dispatch(encodedCalls)` with attacker-controlled arbitrary call arrays, target addresses, calldata, call values, and any residual dispatcher balances or approvals and placing duplicate or reordered leaves, signatures, requests, responses, timeouts, or commitments inside one user-accessible batch, and make `dispatch` turn the dispatcher back on itself to bypass whatever sequencing the calling flow expected so `the intended one-pass call sequence` becomes inconsistent with `a single non-recursive call graph for the active flow`, breaking the invariant that dispatcher recursion or self-targeting must not let attackers reorder, duplicate, or widen the active protocol call graph and leading to High: duplicate settlement, duplicate claim, or double execution of a one-time flow?

## Target
- File/function: evm/src/utils/CallDispatcher.sol::dispatch
- Entrypoint: CallDispatcher.dispatch(encodedCalls)
- Attacker controls: arbitrary call arrays, target addresses, calldata, call values, and any residual dispatcher balances or approvals
- Exploit idea: Turn the dispatcher back on itself to bypass whatever sequencing the calling flow expected. Use a batch with one honest item and one duplicated or reordered item to see whether unique-item assumptions collapse.
- Invariant to test: dispatcher recursion or self-targeting must not let attackers reorder, duplicate, or widen the active protocol call graph
- Expected Immunefi impact: High: duplicate settlement, duplicate claim, or double execution of a one-time flow.
- Fast validation: Build a payload that targets the dispatcher itself or a contract that re-invokes it and assert recursion cannot duplicate value-moving side effects. Write a focused batch test with repeated indices or commitments and assert only unique authenticated items can affect state.
