# Q3: NEAR sign_transfer origin and destination nonce desynchronization

## Question
Can an unprivileged attacker enter through `public `sign_transfer` on a pending transfer id` with control over transfer id, optional fee recipient, optional fee override, attached deposit, and any stored transfer fields reachable through the chosen id and make `near/omni-bridge/src/lib.rs::sign_transfer` advance or reuse bridge nonces inconsistently with loads a stored transfer, normalizes the amount using destination token decimals, parses destination message, and asks MPC to sign a `TransferMessagePayload` for the destination chain, so that one economic transfer can be emitted, resumed, or signed under multiple identifiers, violating `every stored transfer must hash to one unambiguous outbound payload with one fee model, one token mapping, and one recipient intent`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::sign_transfer`
- Entrypoint: `public `sign_transfer` on a pending transfer id`
- Attacker controls: transfer id, optional fee recipient, optional fee override, attached deposit, and any stored transfer fields reachable through the chosen id
- Exploit idea: Drive retries, resume paths, or recursive bridge legs until one deposit appears under more than one transfer identity.
- Invariant to test: every stored transfer must hash to one unambiguous outbound payload with one fee model, one token mapping, and one recipient intent
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Trace every nonce mutation site and fuzz repeated calls plus reordered callbacks to prove that one deposit cannot create two valid transfer ids or destination messages.
