# Q942: Stale Height Replay With Duplicate Or Reordered Items

## Question
Can an unprivileged attacker enter through `pallet_ismp::handle_unsigned(origin=None, messages)` with attacker-controlled consensus proof bytes, authority indices, header batches, state-machine identifiers, and claimed heights and placing duplicate or reordered leaves, signatures, requests, responses, timeouts, or commitments inside one user-accessible batch, and make `verify_current_epoch_proof` treat stale or already-consumed proof material as fresh and overwrite newer state so `the stored latest height or consensus state` becomes inconsistent with `the newest previously accepted height and state`, breaking the invariant that consensus state and stored commitments must move strictly forward and must never roll back to an older authenticated height and leading to Critical: false state acceptance that can unlock unauthorized cross-chain execution, withdrawals, or settlement?

## Target
- File/function: modules/consensus/pharos/verifier/src/state_proof.rs::verify_current_epoch_proof
- Entrypoint: pallet_ismp::handle_unsigned(origin=None, messages)
- Attacker controls: consensus proof bytes, authority indices, header batches, state-machine identifiers, and claimed heights
- Exploit idea: Treat stale or already-consumed proof material as fresh and overwrite newer state. Use a batch with one honest item and one duplicated or reordered item to see whether unique-item assumptions collapse.
- Invariant to test: consensus state and stored commitments must move strictly forward and must never roll back to an older authenticated height
- Expected Immunefi impact: Critical: false state acceptance that can unlock unauthorized cross-chain execution, withdrawals, or settlement.
- Fast validation: Accept one update first, then replay older or equal-height material with one field changed and assert latest height, consensus bytes, and commitments remain unchanged. Write a focused batch test with repeated indices or commitments and assert only unique authenticated items can affect state.
