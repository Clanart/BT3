# Q468: Governance Replay Of Decimal Updates With Duplicate Or Reordered Items

## Question
Can an unprivileged attacker enter through `HandlerV2.handlePostRequests(IHost host, request) -> VWAPOracle.onAccept` with attacker-controlled fill-path inputs and outputs, source-chain bytes, token decimal updates, authenticated governance messages, and replay ordering and placing duplicate or reordered leaves, signatures, requests, responses, timeouts, or commitments inside one user-accessible batch, and make `onAccept` apply a privileged decimal update more than once or after it should no longer be live so `the one-time effect of one decimals update` becomes inconsistent with `one authenticated application of the update`, breaking the invariant that decimal updates must not be replayable through duplicate messages or retry paths once already applied and leading to High: duplicate settlement, duplicate claim, or double execution of a one-time flow?

## Target
- File/function: evm/src/utils/VWAPOracle.sol::onAccept
- Entrypoint: HandlerV2.handlePostRequests(IHost host, request) -> VWAPOracle.onAccept
- Attacker controls: fill-path inputs and outputs, source-chain bytes, token decimal updates, authenticated governance messages, and replay ordering
- Exploit idea: Apply a privileged decimal update more than once or after it should no longer be live. Use a batch with one honest item and one duplicated or reordered item to see whether unique-item assumptions collapse.
- Invariant to test: decimal updates must not be replayable through duplicate messages or retry paths once already applied
- Expected Immunefi impact: High: duplicate settlement, duplicate claim, or double execution of a one-time flow.
- Fast validation: Apply one decimals update, replay it, and assert subsequent public pricing behavior stays single-apply. Write a focused batch test with repeated indices or commitments and assert only unique authenticated items can affect state.
