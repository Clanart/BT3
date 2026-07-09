# Q2008: NEAR add_fin_transfer storage withdrawal escapes live liabilities

## Question
Can an unprivileged attacker call `internal finalization-state writer reached from public finalize flows` and make `near/omni-bridge/src/lib.rs::add_fin_transfer` release storage funds that still back unresolved bridge state because of inserts a transfer id into `finalised_transfers` and charges storage for that finalized record, violating `finalization writes must be globally unique and must not be reverted or duplicated in ways that reopen the same bridge event`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::add_fin_transfer`
- Entrypoint: `internal finalization-state writer reached from public finalize flows`
- Attacker controls: transfer id chosen from source event and the timing of repeat calls
- Exploit idea: Look for withdrawals and unregister paths that do not fully account for pending, finalized, or fast-transfer records.
- Invariant to test: finalization writes must be globally unique and must not be reverted or duplicated in ways that reopen the same bridge event
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Open live bridge state, withdraw aggressively, and assert that storage balances cannot fall below the reserved amount implied by that live state.
