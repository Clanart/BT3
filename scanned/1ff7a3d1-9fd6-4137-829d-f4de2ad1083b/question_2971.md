# Q2971: NEAR omni-types EVM receipt parser address normalization changes authenticated subject through cross-module drift

## Question
Can an unprivileged attacker use `public EVM proof path through `verify_proof`` with control over RLP-encoded receipt bytes, log arrays, log index, and ABI field boundaries and desynchronize `near/omni-types/src/evm/receipt.rs::Receipt/LogEntry` from the adjacent proof parsing and source authentication that shares the same asset, nonce, proof subject, or mapping specifically in the `address normalization changes authenticated subject` attack class because decodes receipts and log entries before inclusion and event-class checks, violating `receipt parsing must not let crafted receipts or logs alias one event as another bridge event class`?

## Target
- File/function: `near/omni-types/src/evm/receipt.rs::Receipt/LogEntry`
- Entrypoint: `public EVM proof path through `verify_proof``
- Attacker controls: RLP-encoded receipt bytes, log arrays, log index, and ABI field boundaries
- Exploit idea: Target hex, byte-array, and account-id conversions between proof parsing and token/recipient lookup. Focus on drift between this module and the adjacent proof parsing and source authentication.
- Invariant to test: receipt parsing must not let crafted receipts or logs alias one event as another bridge event class
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Round-trip every proof-derived address through all local conversions and assert that normalization never changes the bridge subject. Also assert cross-module consistency between `near/omni-types/src/evm/receipt.rs::Receipt/LogEntry` and the adjacent proof parsing and source authentication after every branch.
