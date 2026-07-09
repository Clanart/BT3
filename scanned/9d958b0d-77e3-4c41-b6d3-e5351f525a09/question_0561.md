# Q561: NEAR OmniToken burn burn or lock before irreversible state at boundary values

## Question
Can an unprivileged attacker trigger `public bridge-token burn path via controller-only callback reached from outbound bridge flows` with boundary-controlled inputs covering zero, maximal, and malformed user-controlled values and make `near/omni-token/src/lib.rs::burn` violate `outbound burning must always debit the exact bridged balance that backs the emitted bridge event and must not depend on an attacker-rebindable predecessor context` in the `burn or lock before irreversible state` attack class because controller-only burn always withdraws from `env::predecessor_account_id()` rather than an explicit account parameter becomes fragile at those edges?

## Target
- File/function: `near/omni-token/src/lib.rs::burn`
- Entrypoint: `public bridge-token burn path via controller-only callback reached from outbound bridge flows`
- Attacker controls: amount and predecessor account used as the balance source
- Exploit idea: Look for branches where custody changes happen before the final pending-state, mapping, or callback outcome is fixed. Concentrate on zero, maximal, and malformed user-controlled values.
- Invariant to test: outbound burning must always debit the exact bridged balance that backs the emitted bridge event and must not depend on an attacker-rebindable predecessor context
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Model failures between custody changes and state writes, then assert that no branch both consumes user value and allows the transfer to be replayed or dropped. Sweep boundary values for zero, maximal, and malformed user-controlled values and assert that the same invariant holds at every edge.
