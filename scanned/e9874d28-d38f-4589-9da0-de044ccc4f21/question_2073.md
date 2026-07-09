# Q2073: NEAR omni-types EVM receipt parser optional-field encoding ambiguity

## Question
Can an unprivileged attacker exploit empty-versus-present optional fields in proofs reaching `public EVM proof path through `verify_proof`` so that `near/omni-types/src/evm/receipt.rs::Receipt/LogEntry` authenticates one payload but downstream logic interprets another because of decodes receipts and log entries before inclusion and event-class checks, violating `receipt parsing must not let crafted receipts or logs alias one event as another bridge event class`?

## Target
- File/function: `near/omni-types/src/evm/receipt.rs::Receipt/LogEntry`
- Entrypoint: `public EVM proof path through `verify_proof``
- Attacker controls: RLP-encoded receipt bytes, log arrays, log index, and ABI field boundaries
- Exploit idea: Focus on empty strings, optional fee recipients, and zero-length messages that are encoded specially.
- Invariant to test: receipt parsing must not let crafted receipts or logs alias one event as another bridge event class
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Generate payloads that differ only by optional-field encoding and assert that they cannot verify under the same proof while producing different bridge behavior.
