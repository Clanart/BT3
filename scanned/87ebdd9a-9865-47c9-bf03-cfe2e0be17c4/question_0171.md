# Q171: NEAR sign_transfer origin and destination nonce desynchronization via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public `sign_transfer` on a pending transfer id` and then replay or reorder the later settlement leg on another chain so that `near/omni-bridge/src/lib.rs::sign_transfer` ends up accepting two inconsistent interpretations of the same economic event specifically around `origin and destination nonce desynchronization` under loads a stored transfer, normalizes the amount using destination token decimals, parses destination message, and asks MPC to sign a `TransferMessagePayload` for the destination chain, violating `every stored transfer must hash to one unambiguous outbound payload with one fee model, one token mapping, and one recipient intent`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::sign_transfer`
- Entrypoint: `public `sign_transfer` on a pending transfer id`
- Attacker controls: transfer id, optional fee recipient, optional fee override, attached deposit, and any stored transfer fields reachable through the chosen id
- Exploit idea: Drive retries, resume paths, or recursive bridge legs until one deposit appears under more than one transfer identity. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: every stored transfer must hash to one unambiguous outbound payload with one fee model, one token mapping, and one recipient intent
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Trace every nonce mutation site and fuzz repeated calls plus reordered callbacks to prove that one deposit cannot create two valid transfer ids or destination messages. Then replay or reorder the later settlement leg on another chain and assert that the bridge still exposes only one valid economic outcome.
