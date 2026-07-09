# Q2334: NEAR OmniToken burn custody accounting diverges from wrapped supply through cross-module drift

## Question
Can an unprivileged attacker use `public bridge-token burn path via controller-only callback reached from outbound bridge flows` with control over amount and predecessor account used as the balance source and desynchronize `near/omni-token/src/lib.rs::burn` from the adjacent mint, burn, or custody accounting that shares the same asset, nonce, proof subject, or mapping specifically in the `custody accounting diverges from wrapped supply` attack class because controller-only burn always withdraws from `env::predecessor_account_id()` rather than an explicit account parameter, violating `outbound burning must always debit the exact bridged balance that backs the emitted bridge event and must not depend on an attacker-rebindable predecessor context`?

## Target
- File/function: `near/omni-token/src/lib.rs::burn`
- Entrypoint: `public bridge-token burn path via controller-only callback reached from outbound bridge flows`
- Attacker controls: amount and predecessor account used as the balance source
- Exploit idea: Target branches that mint, burn, lock, unlock, transfer vault assets, or unwrap native value. Focus on drift between this module and the adjacent mint, burn, or custody accounting.
- Invariant to test: outbound burning must always debit the exact bridged balance that backs the emitted bridge event and must not depend on an attacker-rebindable predecessor context
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Build a per-asset conservation model and assert that total claims never exceed total backing after every public flow. Also assert cross-module consistency between `near/omni-token/src/lib.rs::burn` and the adjacent mint, burn, or custody accounting after every branch.
