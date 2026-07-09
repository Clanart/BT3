# Q675: NEAR sign_transfer burn or lock before irreversible state

## Question
Can an unprivileged attacker use `public `sign_transfer` on a pending transfer id` to force `near/omni-bridge/src/lib.rs::sign_transfer` to burn or lock assets before the transfer record becomes safely irreversible, and then recover or redirect the bridge flow via loads a stored transfer, normalizes the amount using destination token decimals, parses destination message, and asks MPC to sign a `TransferMessagePayload` for the destination chain, violating `every stored transfer must hash to one unambiguous outbound payload with one fee model, one token mapping, and one recipient intent`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::sign_transfer`
- Entrypoint: `public `sign_transfer` on a pending transfer id`
- Attacker controls: transfer id, optional fee recipient, optional fee override, attached deposit, and any stored transfer fields reachable through the chosen id
- Exploit idea: Look for branches where custody changes happen before the final pending-state, mapping, or callback outcome is fixed.
- Invariant to test: every stored transfer must hash to one unambiguous outbound payload with one fee model, one token mapping, and one recipient intent
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Model failures between custody changes and state writes, then assert that no branch both consumes user value and allows the transfer to be replayed or dropped.
