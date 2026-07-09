# Q2677: NEAR omni-types EVM receipt parser address normalization changes authenticated subject

## Question
Can an unprivileged attacker craft proof bytes for `public EVM proof path through `verify_proof`` such that `near/omni-types/src/evm/receipt.rs::Receipt/LogEntry` authenticates an address in one representation but later maps a normalized form to a different asset or account because of decodes receipts and log entries before inclusion and event-class checks, violating `receipt parsing must not let crafted receipts or logs alias one event as another bridge event class`?

## Target
- File/function: `near/omni-types/src/evm/receipt.rs::Receipt/LogEntry`
- Entrypoint: `public EVM proof path through `verify_proof``
- Attacker controls: RLP-encoded receipt bytes, log arrays, log index, and ABI field boundaries
- Exploit idea: Target hex, byte-array, and account-id conversions between proof parsing and token/recipient lookup.
- Invariant to test: receipt parsing must not let crafted receipts or logs alias one event as another bridge event class
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Round-trip every proof-derived address through all local conversions and assert that normalization never changes the bridge subject.
