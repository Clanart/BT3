# Q3793: NEAR omni-types EVM receipt parser one verified event can be reinterpreted as another

## Question
Can an unprivileged attacker feed `public EVM proof path through `verify_proof`` a verified event whose raw bytes `near/omni-types/src/evm/receipt.rs::Receipt/LogEntry` can reinterpret under a second event schema because of decodes receipts and log entries before inclusion and event-class checks, violating `receipt parsing must not let crafted receipts or logs alias one event as another bridge event class`?

## Target
- File/function: `near/omni-types/src/evm/receipt.rs::Receipt/LogEntry`
- Entrypoint: `public EVM proof path through `verify_proof``
- Attacker controls: RLP-encoded receipt bytes, log arrays, log index, and ABI field boundaries
- Exploit idea: Target shared envelopes and topic/payload parsers for init, finalize, deploy, and metadata events.
- Invariant to test: receipt parsing must not let crafted receipts or logs alias one event as another bridge event class
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Attempt to parse the same verified bytes under every event class and assert that only one parser accepts them.
