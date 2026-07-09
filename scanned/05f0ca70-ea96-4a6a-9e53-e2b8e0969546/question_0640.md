# Q640: NEAR foreign-to-foreign bridge forwarding unlock or relock asymmetry at boundary values

## Question
Can an unprivileged attacker trigger `public cross-chain forward path created by `fin_transfer` with non-Near recipient` with boundary-controlled inputs covering zero, maximal, and malformed user-controlled values and make `near/omni-bridge/src/lib.rs::get_bridged_token plus process_fin_transfer_to_other_chain` violate `foreign-to-foreign forwarding must not let mismapped assets or stale lock state create unbacked wrapped supply on a third chain` in the `unlock or relock asymmetry` attack class because maps a foreign asset into its target-chain representation and either stores or fast-resolves a new pending transfer without ever landing value on Near becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::get_bridged_token plus process_fin_transfer_to_other_chain`
- Entrypoint: `public cross-chain forward path created by `fin_transfer` with non-Near recipient`
- Attacker controls: origin token address, target chain, fee split, and fast-transfer status
- Exploit idea: Look for one branch that unlocks origin liquidity while another branch also mints or stores a second claim. Concentrate on zero, maximal, and malformed user-controlled values.
- Invariant to test: foreign-to-foreign forwarding must not let mismapped assets or stale lock state create unbacked wrapped supply on a third chain
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Model successful and failed delivery plus fast-transfer branches and assert that aggregate locked liquidity matches outstanding claims after each path. Sweep boundary values for zero, maximal, and malformed user-controlled values and assert that the same invariant holds at every edge.
