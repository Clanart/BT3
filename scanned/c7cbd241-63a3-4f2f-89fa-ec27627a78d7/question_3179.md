# Q3179: NEAR sign_transfer stored state versus signed bytes mismatch

## Question
Can an unprivileged attacker use `public `sign_transfer` on a pending transfer id` so that `near/omni-bridge/src/lib.rs::sign_transfer` stores one economic transfer but signs or publishes different bytes because of loads a stored transfer, normalizes the amount using destination token decimals, parses destination message, and asks MPC to sign a `TransferMessagePayload` for the destination chain, violating `every stored transfer must hash to one unambiguous outbound payload with one fee model, one token mapping, and one recipient intent`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::sign_transfer`
- Entrypoint: `public `sign_transfer` on a pending transfer id`
- Attacker controls: transfer id, optional fee recipient, optional fee override, attached deposit, and any stored transfer fields reachable through the chosen id
- Exploit idea: Look for canonical-state versus emitted-bytes drift on optional strings, decimals, origin ids, or fee recipients.
- Invariant to test: every stored transfer must hash to one unambiguous outbound payload with one fee model, one token mapping, and one recipient intent
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Compare persisted transfer records to their signed or published payloads and assert byte-for-byte equivalence of all economically-relevant fields.
