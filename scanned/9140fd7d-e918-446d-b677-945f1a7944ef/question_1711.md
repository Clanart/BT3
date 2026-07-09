# Q1711: NEAR OmniToken burn burn debits the wrong logical account through cross-module drift

## Question
Can an unprivileged attacker use `public bridge-token burn path via controller-only callback reached from outbound bridge flows` with control over amount and predecessor account used as the balance source and desynchronize `near/omni-token/src/lib.rs::burn` from the adjacent mint, burn, or custody accounting that shares the same asset, nonce, proof subject, or mapping specifically in the `burn debits the wrong logical account` attack class because controller-only burn always withdraws from `env::predecessor_account_id()` rather than an explicit account parameter, violating `outbound burning must always debit the exact bridged balance that backs the emitted bridge event and must not depend on an attacker-rebindable predecessor context`?

## Target
- File/function: `near/omni-token/src/lib.rs::burn`
- Entrypoint: `public bridge-token burn path via controller-only callback reached from outbound bridge flows`
- Attacker controls: amount and predecessor account used as the balance source
- Exploit idea: Target burns keyed to predecessor account, owner, or controller context rather than an explicit subject. Focus on drift between this module and the adjacent mint, burn, or custody accounting.
- Invariant to test: outbound burning must always debit the exact bridged balance that backs the emitted bridge event and must not depend on an attacker-rebindable predecessor context
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Manipulate caller/proxy layouts and assert that the debited balance always belongs to the asset owner represented in the bridge event. Also assert cross-module consistency between `near/omni-token/src/lib.rs::burn` and the adjacent mint, burn, or custody accounting after every branch.
