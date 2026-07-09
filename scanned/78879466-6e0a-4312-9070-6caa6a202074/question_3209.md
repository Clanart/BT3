# Q3209: NEAR required_balance_for_init_transfer_message storage quote underestimates live state

## Question
Can an unprivileged attacker reach `internal accounting helper reached from public init-transfer paths` and make `near/omni-bridge/src/storage.rs::required_balance_for_init_transfer_message` reserve less storage than the live bridge state actually consumes because of computes how much storage balance an outbound transfer needs before it can be stored or resumed, violating `storage quoting must upper-bound every live outbound state footprint so attackers cannot create undercollateralized pending transfers`?

## Target
- File/function: `near/omni-bridge/src/storage.rs::required_balance_for_init_transfer_message`
- Entrypoint: `internal accounting helper reached from public init-transfer paths`
- Attacker controls: recipient chain, message size, fee structure, origin transfer id usage, and calculated storage account id
- Exploit idea: Target helper functions that quote storage for pending transfers, finalization records, fast transfers, binds, or deployments.
- Invariant to test: storage quoting must upper-bound every live outbound state footprint so attackers cannot create undercollateralized pending transfers
- Expected Immunefi impact: Permanent freezing of funds
- Fast validation: Measure storage usage across maximal inputs and assert that quoted requirements always exceed or equal the true post-state footprint.
