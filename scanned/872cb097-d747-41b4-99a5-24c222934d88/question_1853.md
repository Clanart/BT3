# Q1853: NEAR required_balance_for_init_transfer_message storage payer or owner spoofing at boundary values

## Question
Can an unprivileged attacker trigger `internal accounting helper reached from public init-transfer paths` with boundary-controlled inputs covering empty strings, maximal lengths, and malformed encodings and make `near/omni-bridge/src/storage.rs::required_balance_for_init_transfer_message` violate `storage quoting must upper-bound every live outbound state footprint so attackers cannot create undercollateralized pending transfers` in the `storage payer or owner spoofing` attack class because computes how much storage balance an outbound transfer needs before it can be stored or resumed becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/storage.rs::required_balance_for_init_transfer_message`
- Entrypoint: `internal accounting helper reached from public init-transfer paths`
- Attacker controls: recipient chain, message size, fee structure, origin transfer id usage, and calculated storage account id
- Exploit idea: Exploit signer/predecessor splits, message-storage account ids, or promise bookkeeping to shift storage liabilities between accounts. Concentrate on empty strings, maximal lengths, and malformed encodings.
- Invariant to test: storage quoting must upper-bound every live outbound state footprint so attackers cannot create undercollateralized pending transfers
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Simulate conflicting `sender_id`, `signer_id`, and pre-funded storage accounts and assert that only the intended payer can fund, resume, or recover that transfer. Sweep boundary values for empty strings, maximal lengths, and malformed encodings and assert that the same invariant holds at every edge.
