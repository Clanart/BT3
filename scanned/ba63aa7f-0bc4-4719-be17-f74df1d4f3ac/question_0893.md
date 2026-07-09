# Q893: NEAR OmniToken mint delivery callback leaves inconsistent state via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public bridge-token mint path via controller-only callback reached from bridge delivery` and then replay or reorder later callback or refund resolution so that `near/omni-token/src/lib.rs::mint` ends up accepting two inconsistent interpretations of the same economic event specifically around `delivery callback leaves inconsistent state` under controller-only mint either deposits directly or first credits the predecessor account then calls `ft_transfer_call` to the recipient when `msg` is present, violating `mint-with-message and plain mint must be economically equivalent and must not create balances on the controller or recipient that survive inconsistent callback outcomes`?

## Target
- File/function: `near/omni-token/src/lib.rs::mint`
- Entrypoint: `public bridge-token mint path via controller-only callback reached from bridge delivery`
- Attacker controls: recipient account, amount, optional `msg`, and any receiver behavior in `ft_transfer_call`
- Exploit idea: Focus on `ft_transfer_call`, unwrap callbacks, and post-delivery resolution that decide whether to burn, refund, or remove records. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: mint-with-message and plain mint must be economically equivalent and must not create balances on the controller or recipient that survive inconsistent callback outcomes
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Enumerate callback results and assert that each result maps to exactly one consistent combination of delivered value, replay state, and storage refund. Then replay or reorder later callback or refund resolution and assert that the bridge still exposes only one valid economic outcome.
