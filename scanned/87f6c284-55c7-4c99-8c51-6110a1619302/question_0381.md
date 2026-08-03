# Q381: Recursive Dispatch Abuse Across Mixed Context

## Question
Can an unprivileged attacker enter through `CallDispatcher.dispatch(encodedCalls)` with attacker-controlled arbitrary call arrays, target addresses, calldata, call values, and any residual dispatcher balances or approvals and mixing bytes that were valid in one proof, chain, module, order, or beneficiary context with metadata interpreted in another context, and make `dispatch` turn the dispatcher back on itself to bypass whatever sequencing the calling flow expected so `the intended one-pass call sequence` becomes inconsistent with `a single non-recursive call graph for the active flow`, breaking the invariant that dispatcher recursion or self-targeting must not let attackers reorder, duplicate, or widen the active protocol call graph and leading to High: duplicate settlement, duplicate claim, or double execution of a one-time flow?

## Target
- File/function: evm/src/utils/CallDispatcher.sol::dispatch
- Entrypoint: CallDispatcher.dispatch(encodedCalls)
- Attacker controls: arbitrary call arrays, target addresses, calldata, call values, and any residual dispatcher balances or approvals
- Exploit idea: Turn the dispatcher back on itself to bypass whatever sequencing the calling flow expected. Try a pair of otherwise valid artifacts where one verification step authenticates the old context and a later step consumes the new context.
- Invariant to test: dispatcher recursion or self-targeting must not let attackers reorder, duplicate, or widen the active protocol call graph
- Expected Immunefi impact: High: duplicate settlement, duplicate claim, or double execution of a one-time flow.
- Fast validation: Build a payload that targets the dispatcher itself or a contract that re-invokes it and assert recursion cannot duplicate value-moving side effects. Build two neighboring valid contexts and mutate only the binding field while asserting state, receipts, and balances stay unchanged.
