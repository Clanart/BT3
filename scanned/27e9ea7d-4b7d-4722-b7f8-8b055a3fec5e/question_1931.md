# Q1931: Stale Height Replay After Partial State Change

## Question
Can an unprivileged attacker enter through `pallet_ismp::handle_unsigned(origin=None, messages)` with attacker-controlled consensus proof bytes, authority indices, header batches, state-machine identifiers, and claimed heights and replaying the same public flow after one part of storage changed and another part did not, and make `from` treat stale or already-consumed proof material as fresh and overwrite newer state so `the stored latest height or consensus state` becomes inconsistent with `the newest previously accepted height and state`, breaking the invariant that consensus state and stored commitments must move strictly forward and must never roll back to an older authenticated height and leading to Critical: false state acceptance that can unlock unauthorized cross-chain execution, withdrawals, or settlement?

## Target
- File/function: modules/ismp/clients/optimism/src/error.rs::from
- Entrypoint: pallet_ismp::handle_unsigned(origin=None, messages)
- Attacker controls: consensus proof bytes, authority indices, header batches, state-machine identifiers, and claimed heights
- Exploit idea: Treat stale or already-consumed proof material as fresh and overwrite newer state. Drive a partial success or revert path first, then replay the same user-controlled input and check whether stale state is reused.
- Invariant to test: consensus state and stored commitments must move strictly forward and must never roll back to an older authenticated height
- Expected Immunefi impact: Critical: false state acceptance that can unlock unauthorized cross-chain execution, withdrawals, or settlement.
- Fast validation: Accept one update first, then replay older or equal-height material with one field changed and assert latest height, consensus bytes, and commitments remain unchanged. Exercise a success-then-replay or fail-then-replay sequence and assert claimed flags, receipts, escrow, and nonces stay coherent.
