# Q1370: NEAR required_balance_for_init_transfer_message storage payer or owner spoofing

## Question
Can an unprivileged attacker cause `near/omni-bridge/src/storage.rs::required_balance_for_init_transfer_message` to bill, refund, or resume the wrong storage owner through `internal accounting helper reached from public init-transfer paths` by abusing computes how much storage balance an outbound transfer needs before it can be stored or resumed, violating `storage quoting must upper-bound every live outbound state footprint so attackers cannot create undercollateralized pending transfers`?

## Target
- File/function: `near/omni-bridge/src/storage.rs::required_balance_for_init_transfer_message`
- Entrypoint: `internal accounting helper reached from public init-transfer paths`
- Attacker controls: recipient chain, message size, fee structure, origin transfer id usage, and calculated storage account id
- Exploit idea: Exploit signer/predecessor splits, message-storage account ids, or promise bookkeeping to shift storage liabilities between accounts.
- Invariant to test: storage quoting must upper-bound every live outbound state footprint so attackers cannot create undercollateralized pending transfers
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Simulate conflicting `sender_id`, `signer_id`, and pre-funded storage accounts and assert that only the intended payer can fund, resume, or recover that transfer.
