# Q540: NEAR required_balance_for_init_transfer_message recipient or message ambiguity at boundary values

## Question
Can an unprivileged attacker trigger `internal accounting helper reached from public init-transfer paths` with boundary-controlled inputs covering empty strings, maximal lengths, and malformed encodings and make `near/omni-bridge/src/storage.rs::required_balance_for_init_transfer_message` violate `storage quoting must upper-bound every live outbound state footprint so attackers cannot create undercollateralized pending transfers` in the `recipient or message ambiguity` attack class because computes how much storage balance an outbound transfer needs before it can be stored or resumed becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/storage.rs::required_balance_for_init_transfer_message`
- Entrypoint: `internal accounting helper reached from public init-transfer paths`
- Attacker controls: recipient chain, message size, fee structure, origin transfer id usage, and calculated storage account id
- Exploit idea: Exploit non-canonical string, ByteArray, hex, or account-id forms to make one source-side intent resolve to a different destination-side recipient or message. Concentrate on empty strings, maximal lengths, and malformed encodings.
- Invariant to test: storage quoting must upper-bound every live outbound state footprint so attackers cannot create undercollateralized pending transfers
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Cross-check source-side serialization against every downstream parser and assert that equivalent-looking inputs cannot resolve to distinct destination accounts or app messages. Sweep boundary values for empty strings, maximal lengths, and malformed encodings and assert that the same invariant holds at every edge.
