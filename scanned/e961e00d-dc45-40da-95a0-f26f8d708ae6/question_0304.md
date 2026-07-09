# Q304: NEAR foreign-to-foreign bridge forwarding unlock or relock asymmetry via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public cross-chain forward path created by `fin_transfer` with non-Near recipient` and then replay or reorder matching fast-transfer completion or fee-claim leg so that `near/omni-bridge/src/lib.rs::get_bridged_token plus process_fin_transfer_to_other_chain` ends up accepting two inconsistent interpretations of the same economic event specifically around `unlock or relock asymmetry` under maps a foreign asset into its target-chain representation and either stores or fast-resolves a new pending transfer without ever landing value on Near, violating `foreign-to-foreign forwarding must not let mismapped assets or stale lock state create unbacked wrapped supply on a third chain`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::get_bridged_token plus process_fin_transfer_to_other_chain`
- Entrypoint: `public cross-chain forward path created by `fin_transfer` with non-Near recipient`
- Attacker controls: origin token address, target chain, fee split, and fast-transfer status
- Exploit idea: Look for one branch that unlocks origin liquidity while another branch also mints or stores a second claim. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: foreign-to-foreign forwarding must not let mismapped assets or stale lock state create unbacked wrapped supply on a third chain
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Model successful and failed delivery plus fast-transfer branches and assert that aggregate locked liquidity matches outstanding claims after each path. Then replay or reorder matching fast-transfer completion or fee-claim leg and assert that the bridge still exposes only one valid economic outcome.
