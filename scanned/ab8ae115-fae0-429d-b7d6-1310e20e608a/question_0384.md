# Q384: Recursive Dispatch Abuse By Reusing Data Cross Module

## Question
Can an unprivileged attacker enter through `CallDispatcher.dispatch(encodedCalls)` with attacker-controlled arbitrary call arrays, target addresses, calldata, call values, and any residual dispatcher balances or approvals and reusing data that should belong to one chain, module, order, state machine, or payee in another publicly reachable path, and make `dispatch` turn the dispatcher back on itself to bypass whatever sequencing the calling flow expected so `the intended one-pass call sequence` becomes inconsistent with `a single non-recursive call graph for the active flow`, breaking the invariant that dispatcher recursion or self-targeting must not let attackers reorder, duplicate, or widen the active protocol call graph and leading to High: duplicate settlement, duplicate claim, or double execution of a one-time flow?

## Target
- File/function: evm/src/utils/CallDispatcher.sol::dispatch
- Entrypoint: CallDispatcher.dispatch(encodedCalls)
- Attacker controls: arbitrary call arrays, target addresses, calldata, call values, and any residual dispatcher balances or approvals
- Exploit idea: Turn the dispatcher back on itself to bypass whatever sequencing the calling flow expected. Craft two public flows that share one byte string or hash and check whether module, chain, or payee binding is enforced everywhere.
- Invariant to test: dispatcher recursion or self-targeting must not let attackers reorder, duplicate, or widen the active protocol call graph
- Expected Immunefi impact: High: duplicate settlement, duplicate claim, or double execution of a one-time flow.
- Fast validation: Build a payload that targets the dispatcher itself or a contract that re-invokes it and assert recursion cannot duplicate value-moving side effects. Feed the same bytes through two reachable entrypoints and assert the second path rejects instead of inheriting the first path's authorization.
