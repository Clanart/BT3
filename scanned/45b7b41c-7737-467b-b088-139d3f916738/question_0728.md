# Q728: NEAR OmniToken burn native versus wrapped branch switch

## Question
Can an unprivileged attacker choose inputs to `public bridge-token burn path via controller-only callback reached from outbound bridge flows` that make `near/omni-token/src/lib.rs::burn` classify the asset differently before and after a custody-changing step through controller-only burn always withdraws from `env::predecessor_account_id()` rather than an explicit account parameter, violating `outbound burning must always debit the exact bridged balance that backs the emitted bridge event and must not depend on an attacker-rebindable predecessor context`?

## Target
- File/function: `near/omni-token/src/lib.rs::burn`
- Entrypoint: `public bridge-token burn path via controller-only callback reached from outbound bridge flows`
- Attacker controls: amount and predecessor account used as the balance source
- Exploit idea: Target zero-address, deployed-token, custom-minter, native-vault, or bridge-token branch predicates.
- Invariant to test: outbound burning must always debit the exact bridged balance that backs the emitted bridge event and must not depend on an attacker-rebindable predecessor context
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Force each branch predicate to flip around callbacks or mapping writes and assert that the same source asset can never produce two incompatible custody models.
