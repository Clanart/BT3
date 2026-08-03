# Q2466: Partial Rollback Gap With Duplicate Or Reordered Items

## Question
Can an unprivileged attacker enter through `pallet_ismp::handle_unsigned(origin=None, messages)` with attacker-controlled message batches, proof bytes, timeout metadata, source and destination identifiers, module ids, and message bodies and placing duplicate or reordered leaves, signatures, requests, responses, timeouts, or commitments inside one user-accessible batch, and make `handle` leave one-time markers, commitments, or receipts in a state that permits a second profitable execution after a revert path so `the rollback-protected pending state` becomes inconsistent with `the full message lifecycle state after success or failure`, breaking the invariant that if a callback fails, every one-time marker and pending state element must be restored consistently so attackers cannot replay into a second settlement and leading to Critical: wrongful mint, unlock, withdrawal, refund, or redirect of protocol-controlled or user escrowed assets?

## Target
- File/function: modules/ismp/core/src/handlers/request.rs::handle
- Entrypoint: pallet_ismp::handle_unsigned(origin=None, messages)
- Attacker controls: message batches, proof bytes, timeout metadata, source and destination identifiers, module ids, and message bodies
- Exploit idea: Leave one-time markers, commitments, or receipts in a state that permits a second profitable execution after a revert path. Use a batch with one honest item and one duplicated or reordered item to see whether unique-item assumptions collapse.
- Invariant to test: if a callback fails, every one-time marker and pending state element must be restored consistently so attackers cannot replay into a second settlement
- Expected Immunefi impact: Critical: wrongful mint, unlock, withdrawal, refund, or redirect of protocol-controlled or user escrowed assets.
- Fast validation: Force a callback failure, then retry with the same message and assert receipts, commitments, and module callbacks line up with a single pending state. Write a focused batch test with repeated indices or commitments and assert only unique authenticated items can affect state.
