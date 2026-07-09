# Q1205: NEAR required_balance_for_init_transfer_message fee and principal split divergence at boundary values

## Question
Can an unprivileged attacker trigger `internal accounting helper reached from public init-transfer paths` with boundary-controlled inputs covering empty strings, maximal lengths, and malformed encodings and make `near/omni-bridge/src/storage.rs::required_balance_for_init_transfer_message` violate `storage quoting must upper-bound every live outbound state footprint so attackers cannot create undercollateralized pending transfers` in the `fee and principal split divergence` attack class because computes how much storage balance an outbound transfer needs before it can be stored or resumed becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/storage.rs::required_balance_for_init_transfer_message`
- Entrypoint: `internal accounting helper reached from public init-transfer paths`
- Attacker controls: recipient chain, message size, fee structure, origin transfer id usage, and calculated storage account id
- Exploit idea: Focus on branches where fee checks happen before normalization, denormalization, callback resolution, or storage billing. Concentrate on empty strings, maximal lengths, and malformed encodings.
- Invariant to test: storage quoting must upper-bound every live outbound state footprint so attackers cannot create undercollateralized pending transfers
- Expected Immunefi impact: Balance manipulation
- Fast validation: Fuzz amount/fee/native-fee edge cases around zero, max, and decimal boundaries and assert that emitted value plus stored fee always equals consumed value. Sweep boundary values for empty strings, maximal lengths, and malformed encodings and assert that the same invariant holds at every edge.
