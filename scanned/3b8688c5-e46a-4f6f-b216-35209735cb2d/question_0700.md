# Q700: NEAR add_fin_transfer storage-preparation omission changes settlement meaning

## Question
Can an unprivileged attacker make `internal finalization-state writer reached from public finalize flows` omit or reorder required storage setup so that `near/omni-bridge/src/lib.rs::add_fin_transfer` settles under a different assumption about who can receive principal or fees because of inserts a transfer id into `finalised_transfers` and charges storage for that finalized record, violating `finalization writes must be globally unique and must not be reverted or duplicated in ways that reopen the same bridge event`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::add_fin_transfer`
- Entrypoint: `internal finalization-state writer reached from public finalize flows`
- Attacker controls: transfer id chosen from source event and the timing of repeat calls
- Exploit idea: Target recipient or fee-recipient storage checks that gate token delivery or fee minting.
- Invariant to test: finalization writes must be globally unique and must not be reverted or duplicated in ways that reopen the same bridge event
- Expected Immunefi impact: Permanent freezing of funds
- Fast validation: Reorder or omit storage-deposit actions and assert that settlement either fully aborts before finalization or completes with all intended recipients properly provisioned.
