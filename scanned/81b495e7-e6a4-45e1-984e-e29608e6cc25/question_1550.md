# Q1550: NEAR OmniToken burn burn debits the wrong logical account via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public bridge-token burn path via controller-only callback reached from outbound bridge flows` and then replay or reorder the later settlement leg on another chain so that `near/omni-token/src/lib.rs::burn` ends up accepting two inconsistent interpretations of the same economic event specifically around `burn debits the wrong logical account` under controller-only burn always withdraws from `env::predecessor_account_id()` rather than an explicit account parameter, violating `outbound burning must always debit the exact bridged balance that backs the emitted bridge event and must not depend on an attacker-rebindable predecessor context`?

## Target
- File/function: `near/omni-token/src/lib.rs::burn`
- Entrypoint: `public bridge-token burn path via controller-only callback reached from outbound bridge flows`
- Attacker controls: amount and predecessor account used as the balance source
- Exploit idea: Target burns keyed to predecessor account, owner, or controller context rather than an explicit subject. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: outbound burning must always debit the exact bridged balance that backs the emitted bridge event and must not depend on an attacker-rebindable predecessor context
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Manipulate caller/proxy layouts and assert that the debited balance always belongs to the asset owner represented in the bridge event. Then replay or reorder the later settlement leg on another chain and assert that the bridge still exposes only one valid economic outcome.
