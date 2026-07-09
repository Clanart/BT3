# Q873: NEAR required_balance_for_init_transfer_message fee and principal split divergence via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `internal accounting helper reached from public init-transfer paths` and then replay or reorder the later settlement leg on another chain so that `near/omni-bridge/src/storage.rs::required_balance_for_init_transfer_message` ends up accepting two inconsistent interpretations of the same economic event specifically around `fee and principal split divergence` under computes how much storage balance an outbound transfer needs before it can be stored or resumed, violating `storage quoting must upper-bound every live outbound state footprint so attackers cannot create undercollateralized pending transfers`?

## Target
- File/function: `near/omni-bridge/src/storage.rs::required_balance_for_init_transfer_message`
- Entrypoint: `internal accounting helper reached from public init-transfer paths`
- Attacker controls: recipient chain, message size, fee structure, origin transfer id usage, and calculated storage account id
- Exploit idea: Focus on branches where fee checks happen before normalization, denormalization, callback resolution, or storage billing. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: storage quoting must upper-bound every live outbound state footprint so attackers cannot create undercollateralized pending transfers
- Expected Immunefi impact: Balance manipulation
- Fast validation: Fuzz amount/fee/native-fee edge cases around zero, max, and decimal boundaries and assert that emitted value plus stored fee always equals consumed value. Then replay or reorder the later settlement leg on another chain and assert that the bridge still exposes only one valid economic outcome.
