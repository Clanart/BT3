# Q1060: NEAR OmniToken burn native versus wrapped branch switch through cross-module drift

## Question
Can an unprivileged attacker use `public bridge-token burn path via controller-only callback reached from outbound bridge flows` with control over amount and predecessor account used as the balance source and desynchronize `near/omni-token/src/lib.rs::burn` from the adjacent mint, burn, or custody accounting that shares the same asset, nonce, proof subject, or mapping specifically in the `native versus wrapped branch switch` attack class because controller-only burn always withdraws from `env::predecessor_account_id()` rather than an explicit account parameter, violating `outbound burning must always debit the exact bridged balance that backs the emitted bridge event and must not depend on an attacker-rebindable predecessor context`?

## Target
- File/function: `near/omni-token/src/lib.rs::burn`
- Entrypoint: `public bridge-token burn path via controller-only callback reached from outbound bridge flows`
- Attacker controls: amount and predecessor account used as the balance source
- Exploit idea: Target zero-address, deployed-token, custom-minter, native-vault, or bridge-token branch predicates. Focus on drift between this module and the adjacent mint, burn, or custody accounting.
- Invariant to test: outbound burning must always debit the exact bridged balance that backs the emitted bridge event and must not depend on an attacker-rebindable predecessor context
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Force each branch predicate to flip around callbacks or mapping writes and assert that the same source asset can never produce two incompatible custody models. Also assert cross-module consistency between `near/omni-token/src/lib.rs::burn` and the adjacent mint, burn, or custody accounting after every branch.
