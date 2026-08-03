# Q2515: Duplicate Batch Execution After Partial State Change

## Question
Can an unprivileged attacker enter through `pallet_ismp::handle_unsigned(origin=None, messages)` with attacker-controlled message batches, proof bytes, timeout metadata, source and destination identifiers, module ids, and message bodies and replaying the same public flow after one part of storage changed and another part did not, and make `handle` execute the same request, response, or timeout more than once inside one batch or across two batches so `the one-time execution marker` becomes inconsistent with `the unique authenticated message set`, breaking the invariant that duplicate messages must be rejected before any external callback can produce a second execution or second settlement and leading to High: duplicate settlement, duplicate claim, or double execution of a one-time flow?

## Target
- File/function: modules/ismp/core/src/handlers/timeout.rs::handle
- Entrypoint: pallet_ismp::handle_unsigned(origin=None, messages)
- Attacker controls: message batches, proof bytes, timeout metadata, source and destination identifiers, module ids, and message bodies
- Exploit idea: Execute the same request, response, or timeout more than once inside one batch or across two batches. Drive a partial success or revert path first, then replay the same user-controlled input and check whether stale state is reused.
- Invariant to test: duplicate messages must be rejected before any external callback can produce a second execution or second settlement
- Expected Immunefi impact: High: duplicate settlement, duplicate claim, or double execution of a one-time flow.
- Fast validation: Create a batch with duplicates or replay the same batch and assert callbacks, receipts, and balance-moving side effects occur at most once. Exercise a success-then-replay or fail-then-replay sequence and assert claimed flags, receipts, escrow, and nonces stay coherent.
