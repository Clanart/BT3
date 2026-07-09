# Q1434: NEAR omni-types EVM receipt parser partial EVM validation leaves exploitable gap

## Question
Can an unprivileged attacker provide an EVM proof to `public EVM proof path through `verify_proof`` that passes inclusion checks in `near/omni-types/src/evm/receipt.rs::Receipt/LogEntry` while the decoded receipt or log still authorizes a different bridge action because of decodes receipts and log entries before inclusion and event-class checks, violating `receipt parsing must not let crafted receipts or logs alias one event as another bridge event class`?

## Target
- File/function: `near/omni-types/src/evm/receipt.rs::Receipt/LogEntry`
- Entrypoint: `public EVM proof path through `verify_proof``
- Attacker controls: RLP-encoded receipt bytes, log arrays, log index, and ABI field boundaries
- Exploit idea: Probe inconsistencies between receipt inclusion, log selection, event decoding, and block-hash validation.
- Invariant to test: receipt parsing must not let crafted receipts or logs alias one event as another bridge event class
- Expected Immunefi impact: Light client verification bypass
- Fast validation: Mutate one proof component at a time and assert that no accepted proof can change any economically-relevant decoded field after inclusion succeeds.
