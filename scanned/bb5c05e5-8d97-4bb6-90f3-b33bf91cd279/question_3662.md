# Q3662: NEAR omni-types EVM receipt parser length or offset shift reinterprets adjacent fields at boundary values

## Question
Can an unprivileged attacker trigger `public EVM proof path through `verify_proof`` with boundary-controlled inputs covering empty strings, maximal lengths, and malformed encodings and make `near/omni-types/src/evm/receipt.rs::Receipt/LogEntry` violate `receipt parsing must not let crafted receipts or logs alias one event as another bridge event class` in the `length or offset shift reinterprets adjacent fields` attack class because decodes receipts and log entries before inclusion and event-class checks becomes fragile at those edges?

## Target
- File/function: `near/omni-types/src/evm/receipt.rs::Receipt/LogEntry`
- Entrypoint: `public EVM proof path through `verify_proof``
- Attacker controls: RLP-encoded receipt bytes, log arrays, log index, and ABI field boundaries
- Exploit idea: Target VAA body offsets, byte slicing helpers, RLP decoders, and Borsh length prefixes. Concentrate on empty strings, maximal lengths, and malformed encodings.
- Invariant to test: receipt parsing must not let crafted receipts or logs alias one event as another bridge event class
- Expected Immunefi impact: Light client verification bypass
- Fast validation: Fuzz underlong, overlong, and near-boundary payloads and assert that accepted bytes decode to exactly one structured message. Sweep boundary values for empty strings, maximal lengths, and malformed encodings and assert that the same invariant holds at every edge.
