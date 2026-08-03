# Q3247: Partial Rollback Gap After Partial State Change

## Question
Can an unprivileged attacker enter through `pallet_ismp::handle_unsigned(origin=None, messages)` with attacker-controlled message batches, proof bytes, timeout metadata, source and destination identifiers, module ids, and message bodies and replaying the same public flow after one part of storage changed and another part did not, and make `on_accept` leave one-time markers, commitments, or receipts in a state that permits a second profitable execution after a revert path so `the rollback-protected pending state` becomes inconsistent with `the full message lifecycle state after success or failure`, breaking the invariant that if a callback fails, every one-time marker and pending state element must be restored consistently so attackers cannot replay into a second settlement and leading to Critical: wrongful mint, unlock, withdrawal, refund, or redirect of protocol-controlled or user escrowed assets?

## Target
- File/function: modules/pallets/ismp/src/dispatcher.rs::on_accept
- Entrypoint: pallet_ismp::handle_unsigned(origin=None, messages)
- Attacker controls: message batches, proof bytes, timeout metadata, source and destination identifiers, module ids, and message bodies
- Exploit idea: Leave one-time markers, commitments, or receipts in a state that permits a second profitable execution after a revert path. Drive a partial success or revert path first, then replay the same user-controlled input and check whether stale state is reused.
- Invariant to test: if a callback fails, every one-time marker and pending state element must be restored consistently so attackers cannot replay into a second settlement
- Expected Immunefi impact: Critical: wrongful mint, unlock, withdrawal, refund, or redirect of protocol-controlled or user escrowed assets.
- Fast validation: Force a callback failure, then retry with the same message and assert receipts, commitments, and module callbacks line up with a single pending state. Exercise a success-then-replay or fail-then-replay sequence and assert claimed flags, receipts, escrow, and nonces stay coherent.
