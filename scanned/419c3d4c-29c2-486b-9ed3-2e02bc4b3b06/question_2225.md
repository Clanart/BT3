# Q2225: NEAR omni-types EVM receipt parser optional-field encoding ambiguity via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public EVM proof path through `verify_proof`` and then replay or reorder another proof-consuming public entrypoint so that `near/omni-types/src/evm/receipt.rs::Receipt/LogEntry` ends up accepting two inconsistent interpretations of the same economic event specifically around `optional-field encoding ambiguity` under decodes receipts and log entries before inclusion and event-class checks, violating `receipt parsing must not let crafted receipts or logs alias one event as another bridge event class`?

## Target
- File/function: `near/omni-types/src/evm/receipt.rs::Receipt/LogEntry`
- Entrypoint: `public EVM proof path through `verify_proof``
- Attacker controls: RLP-encoded receipt bytes, log arrays, log index, and ABI field boundaries
- Exploit idea: Focus on empty strings, optional fee recipients, and zero-length messages that are encoded specially. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: receipt parsing must not let crafted receipts or logs alias one event as another bridge event class
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Generate payloads that differ only by optional-field encoding and assert that they cannot verify under the same proof while producing different bridge behavior. Then replay or reorder another proof-consuming public entrypoint and assert that the bridge still exposes only one valid economic outcome.
