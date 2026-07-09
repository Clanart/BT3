# Q607: NEAR omni-types EVM receipt parser proof kind or event class confusion at boundary values

## Question
Can an unprivileged attacker trigger `public EVM proof path through `verify_proof`` with boundary-controlled inputs covering empty strings, maximal lengths, and malformed encodings and make `near/omni-types/src/evm/receipt.rs::Receipt/LogEntry` violate `receipt parsing must not let crafted receipts or logs alias one event as another bridge event class` in the `proof kind or event class confusion` attack class because decodes receipts and log entries before inclusion and event-class checks becomes fragile at those edges?

## Target
- File/function: `near/omni-types/src/evm/receipt.rs::Receipt/LogEntry`
- Entrypoint: `public EVM proof path through `verify_proof``
- Attacker controls: RLP-encoded receipt bytes, log arrays, log index, and ABI field boundaries
- Exploit idea: Look for outer proof envelopes that carry a kind tag separately from the payload bytes they validate. Concentrate on empty strings, maximal lengths, and malformed encodings.
- Invariant to test: receipt parsing must not let crafted receipts or logs alias one event as another bridge event class
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Cross-check every proof kind against every parser and assert that no valid envelope can downcast into the wrong bridge action. Sweep boundary values for empty strings, maximal lengths, and malformed encodings and assert that the same invariant holds at every edge.
