# Q3748: NEAR required_balance_for_init_transfer_message storage withdrawal escapes live liabilities

## Question
Can an unprivileged attacker call `internal accounting helper reached from public init-transfer paths` and make `near/omni-bridge/src/storage.rs::required_balance_for_init_transfer_message` release storage funds that still back unresolved bridge state because of computes how much storage balance an outbound transfer needs before it can be stored or resumed, violating `storage quoting must upper-bound every live outbound state footprint so attackers cannot create undercollateralized pending transfers`?

## Target
- File/function: `near/omni-bridge/src/storage.rs::required_balance_for_init_transfer_message`
- Entrypoint: `internal accounting helper reached from public init-transfer paths`
- Attacker controls: recipient chain, message size, fee structure, origin transfer id usage, and calculated storage account id
- Exploit idea: Look for withdrawals and unregister paths that do not fully account for pending, finalized, or fast-transfer records.
- Invariant to test: storage quoting must upper-bound every live outbound state footprint so attackers cannot create undercollateralized pending transfers
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Open live bridge state, withdraw aggressively, and assert that storage balances cannot fall below the reserved amount implied by that live state.
