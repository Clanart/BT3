# Q1872: NEAR OmniToken burn burn debits the wrong logical account at boundary values

## Question
Can an unprivileged attacker trigger `public bridge-token burn path via controller-only callback reached from outbound bridge flows` with boundary-controlled inputs covering zero, maximal, and malformed user-controlled values and make `near/omni-token/src/lib.rs::burn` violate `outbound burning must always debit the exact bridged balance that backs the emitted bridge event and must not depend on an attacker-rebindable predecessor context` in the `burn debits the wrong logical account` attack class because controller-only burn always withdraws from `env::predecessor_account_id()` rather than an explicit account parameter becomes fragile at those edges?

## Target
- File/function: `near/omni-token/src/lib.rs::burn`
- Entrypoint: `public bridge-token burn path via controller-only callback reached from outbound bridge flows`
- Attacker controls: amount and predecessor account used as the balance source
- Exploit idea: Target burns keyed to predecessor account, owner, or controller context rather than an explicit subject. Concentrate on zero, maximal, and malformed user-controlled values.
- Invariant to test: outbound burning must always debit the exact bridged balance that backs the emitted bridge event and must not depend on an attacker-rebindable predecessor context
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Manipulate caller/proxy layouts and assert that the debited balance always belongs to the asset owner represented in the bridge event. Sweep boundary values for zero, maximal, and malformed user-controlled values and assert that the same invariant holds at every edge.
