# Q2930: NEAR OmniToken burn global asset-conservation invariant break through cross-module drift

## Question
Can an unprivileged attacker use `public bridge-token burn path via controller-only callback reached from outbound bridge flows` with control over amount and predecessor account used as the balance source and desynchronize `near/omni-token/src/lib.rs::burn` from the adjacent mint, burn, or custody accounting that shares the same asset, nonce, proof subject, or mapping specifically in the `global asset-conservation invariant break` attack class because controller-only burn always withdraws from `env::predecessor_account_id()` rather than an explicit account parameter, violating `outbound burning must always debit the exact bridged balance that backs the emitted bridge event and must not depend on an attacker-rebindable predecessor context`?

## Target
- File/function: `near/omni-token/src/lib.rs::burn`
- Entrypoint: `public bridge-token burn path via controller-only callback reached from outbound bridge flows`
- Attacker controls: amount and predecessor account used as the balance source
- Exploit idea: Treat the target as one part of a multi-leg conservation system rather than an isolated bug class. Focus on drift between this module and the adjacent mint, burn, or custody accounting.
- Invariant to test: outbound burning must always debit the exact bridged balance that backs the emitted bridge event and must not depend on an attacker-rebindable predecessor context
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Build an invariant test that sums principal, fees, wrapped supply, custody, and lock rows across all affected branches and assert conservation after every step. Also assert cross-module consistency between `near/omni-token/src/lib.rs::burn` and the adjacent mint, burn, or custody accounting after every branch.
