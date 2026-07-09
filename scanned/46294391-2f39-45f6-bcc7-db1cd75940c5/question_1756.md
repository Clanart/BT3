# Q1756: NEAR omni-types EVM receipt parser partial EVM validation leaves exploitable gap through cross-module drift

## Question
Can an unprivileged attacker use `public EVM proof path through `verify_proof`` with control over RLP-encoded receipt bytes, log arrays, log index, and ABI field boundaries and desynchronize `near/omni-types/src/evm/receipt.rs::Receipt/LogEntry` from the adjacent proof parsing and source authentication that shares the same asset, nonce, proof subject, or mapping specifically in the `partial EVM validation leaves exploitable gap` attack class because decodes receipts and log entries before inclusion and event-class checks, violating `receipt parsing must not let crafted receipts or logs alias one event as another bridge event class`?

## Target
- File/function: `near/omni-types/src/evm/receipt.rs::Receipt/LogEntry`
- Entrypoint: `public EVM proof path through `verify_proof``
- Attacker controls: RLP-encoded receipt bytes, log arrays, log index, and ABI field boundaries
- Exploit idea: Probe inconsistencies between receipt inclusion, log selection, event decoding, and block-hash validation. Focus on drift between this module and the adjacent proof parsing and source authentication.
- Invariant to test: receipt parsing must not let crafted receipts or logs alias one event as another bridge event class
- Expected Immunefi impact: Light client verification bypass
- Fast validation: Mutate one proof component at a time and assert that no accepted proof can change any economically-relevant decoded field after inclusion succeeds. Also assert cross-module consistency between `near/omni-types/src/evm/receipt.rs::Receipt/LogEntry` and the adjacent proof parsing and source authentication after every branch.
