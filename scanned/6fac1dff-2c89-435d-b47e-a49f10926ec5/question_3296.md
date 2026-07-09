# Q3296: NEAR public transfer-message reads numeric cast or overflow changes economic meaning

## Question
Can an unprivileged attacker use `public off-chain coordination reads for pending transfers` to push `near/omni-bridge/src/lib.rs::get_transfer_message / get_transfer_message_storage` through a cast, overflow, or truncation path that changes amount or nonce semantics, violating `publicly-observable pending state must remain canonical so external actors cannot be induced to sign or relay the wrong transfer`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::get_transfer_message / get_transfer_message_storage`
- Entrypoint: `public off-chain coordination reads for pending transfers`
- Attacker controls: transfer id choice and timing relative to sign/claim/finalize callbacks
- Exploit idea: Focus on `u128`/`u64`/`usize` casts, PDA bucket indices, and amount conversions around maximum values.
- Invariant to test: publicly-observable pending state must remain canonical so external actors cannot be induced to sign or relay the wrong transfer
- Expected Immunefi impact: Balance manipulation
- Fast validation: Fuzz maximal numbers and assert that every accepted numeric value preserves its economic meaning across all intermediate representations.
