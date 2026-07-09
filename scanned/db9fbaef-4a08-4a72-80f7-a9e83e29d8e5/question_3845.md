# Q3845: NEAR sign_transfer same fee collectible twice via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public `sign_transfer` on a pending transfer id` and then replay or reorder the later settlement leg on another chain so that `near/omni-bridge/src/lib.rs::sign_transfer` ends up accepting two inconsistent interpretations of the same economic event specifically around `same fee collectible twice` under loads a stored transfer, normalizes the amount using destination token decimals, parses destination message, and asks MPC to sign a `TransferMessagePayload` for the destination chain, violating `every stored transfer must hash to one unambiguous outbound payload with one fee model, one token mapping, and one recipient intent`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::sign_transfer`
- Entrypoint: `public `sign_transfer` on a pending transfer id`
- Attacker controls: transfer id, optional fee recipient, optional fee override, attached deposit, and any stored transfer fields reachable through the chosen id
- Exploit idea: Target pending-transfer cleanup, fast-transfer removal, and replay protection around fee-claim proofs. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: every stored transfer must hash to one unambiguous outbound payload with one fee model, one token mapping, and one recipient intent
- Expected Immunefi impact: Balance manipulation
- Fast validation: Claim once, then replay or mutate non-economic proof fields and assert that no second claim succeeds or blocks unrelated transfers. Then replay or reorder the later settlement leg on another chain and assert that the bridge still exposes only one valid economic outcome.
