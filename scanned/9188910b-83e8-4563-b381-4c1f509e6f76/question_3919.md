# Q3919: NEAR omni-types EVM receipt parser one verified event can be reinterpreted as another via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public EVM proof path through `verify_proof`` and then replay or reorder another proof-consuming public entrypoint so that `near/omni-types/src/evm/receipt.rs::Receipt/LogEntry` ends up accepting two inconsistent interpretations of the same economic event specifically around `one verified event can be reinterpreted as another` under decodes receipts and log entries before inclusion and event-class checks, violating `receipt parsing must not let crafted receipts or logs alias one event as another bridge event class`?

## Target
- File/function: `near/omni-types/src/evm/receipt.rs::Receipt/LogEntry`
- Entrypoint: `public EVM proof path through `verify_proof``
- Attacker controls: RLP-encoded receipt bytes, log arrays, log index, and ABI field boundaries
- Exploit idea: Target shared envelopes and topic/payload parsers for init, finalize, deploy, and metadata events. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: receipt parsing must not let crafted receipts or logs alias one event as another bridge event class
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Attempt to parse the same verified bytes under every event class and assert that only one parser accepts them. Then replay or reorder another proof-consuming public entrypoint and assert that the bridge still exposes only one valid economic outcome.
