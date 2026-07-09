# Q393: NEAR OmniToken burn burn or lock before irreversible state through cross-module drift

## Question
Can an unprivileged attacker use `public bridge-token burn path via controller-only callback reached from outbound bridge flows` with control over amount and predecessor account used as the balance source and desynchronize `near/omni-token/src/lib.rs::burn` from the adjacent mint, burn, or custody accounting that shares the same asset, nonce, proof subject, or mapping specifically in the `burn or lock before irreversible state` attack class because controller-only burn always withdraws from `env::predecessor_account_id()` rather than an explicit account parameter, violating `outbound burning must always debit the exact bridged balance that backs the emitted bridge event and must not depend on an attacker-rebindable predecessor context`?

## Target
- File/function: `near/omni-token/src/lib.rs::burn`
- Entrypoint: `public bridge-token burn path via controller-only callback reached from outbound bridge flows`
- Attacker controls: amount and predecessor account used as the balance source
- Exploit idea: Look for branches where custody changes happen before the final pending-state, mapping, or callback outcome is fixed. Focus on drift between this module and the adjacent mint, burn, or custody accounting.
- Invariant to test: outbound burning must always debit the exact bridged balance that backs the emitted bridge event and must not depend on an attacker-rebindable predecessor context
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Model failures between custody changes and state writes, then assert that no branch both consumes user value and allows the transfer to be replayed or dropped. Also assert cross-module consistency between `near/omni-token/src/lib.rs::burn` and the adjacent mint, burn, or custody accounting after every branch.
