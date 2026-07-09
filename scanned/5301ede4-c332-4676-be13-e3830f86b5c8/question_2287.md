# Q2287: NEAR sign_transfer fee and principal split divergence through cross-module drift

## Question
Can an unprivileged attacker use `public `sign_transfer` on a pending transfer id` with control over transfer id, optional fee recipient, optional fee override, attached deposit, and any stored transfer fields reachable through the chosen id and desynchronize `near/omni-bridge/src/lib.rs::sign_transfer` from the adjacent replay-protection bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `fee and principal split divergence` attack class because loads a stored transfer, normalizes the amount using destination token decimals, parses destination message, and asks MPC to sign a `TransferMessagePayload` for the destination chain, violating `every stored transfer must hash to one unambiguous outbound payload with one fee model, one token mapping, and one recipient intent`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::sign_transfer`
- Entrypoint: `public `sign_transfer` on a pending transfer id`
- Attacker controls: transfer id, optional fee recipient, optional fee override, attached deposit, and any stored transfer fields reachable through the chosen id
- Exploit idea: Focus on branches where fee checks happen before normalization, denormalization, callback resolution, or storage billing. Focus on drift between this module and the adjacent replay-protection bookkeeping.
- Invariant to test: every stored transfer must hash to one unambiguous outbound payload with one fee model, one token mapping, and one recipient intent
- Expected Immunefi impact: Balance manipulation
- Fast validation: Fuzz amount/fee/native-fee edge cases around zero, max, and decimal boundaries and assert that emitted value plus stored fee always equals consumed value. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs::sign_transfer` and the adjacent replay-protection bookkeeping after every branch.
