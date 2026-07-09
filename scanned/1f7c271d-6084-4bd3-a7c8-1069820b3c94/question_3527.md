# Q3527: NEAR omni-types EVM receipt parser length or offset shift reinterprets adjacent fields through cross-module drift

## Question
Can an unprivileged attacker use `public EVM proof path through `verify_proof`` with control over RLP-encoded receipt bytes, log arrays, log index, and ABI field boundaries and desynchronize `near/omni-types/src/evm/receipt.rs::Receipt/LogEntry` from the adjacent proof parsing and source authentication that shares the same asset, nonce, proof subject, or mapping specifically in the `length or offset shift reinterprets adjacent fields` attack class because decodes receipts and log entries before inclusion and event-class checks, violating `receipt parsing must not let crafted receipts or logs alias one event as another bridge event class`?

## Target
- File/function: `near/omni-types/src/evm/receipt.rs::Receipt/LogEntry`
- Entrypoint: `public EVM proof path through `verify_proof``
- Attacker controls: RLP-encoded receipt bytes, log arrays, log index, and ABI field boundaries
- Exploit idea: Target VAA body offsets, byte slicing helpers, RLP decoders, and Borsh length prefixes. Focus on drift between this module and the adjacent proof parsing and source authentication.
- Invariant to test: receipt parsing must not let crafted receipts or logs alias one event as another bridge event class
- Expected Immunefi impact: Light client verification bypass
- Fast validation: Fuzz underlong, overlong, and near-boundary payloads and assert that accepted bytes decode to exactly one structured message. Also assert cross-module consistency between `near/omni-types/src/evm/receipt.rs::Receipt/LogEntry` and the adjacent proof parsing and source authentication after every branch.
