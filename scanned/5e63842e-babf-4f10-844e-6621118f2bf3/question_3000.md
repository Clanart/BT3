# Q3000: BaseCallFilter Bypass With Duplicate Or Reordered Items

## Question
Can an unprivileged attacker enter through `pallet_call_decompressor::decompress_call(origin=None, compressed, encoded_call_size)` with attacker-controlled compressed call bytes, claimed decompressed size, SCALE-encoded runtime calls, and replay ordering and placing duplicate or reordered leaves, signatures, requests, responses, timeouts, or commitments inside one user-accessible batch, and make `validate_unsigned` reach an unsigned runtime call that the base filter or subtype checks meant to exclude so `the filtered runtime call executed by the decompressor` becomes inconsistent with `only the small allowlist of unsigned calls that were intentionally permitted`, breaking the invariant that the decompressor must never expand its caller privilege beyond the explicitly supported unsigned subcalls and leading to Critical: unauthenticated cross-chain governance or host-management execution?

## Target
- File/function: modules/pallets/call-decompressor/src/lib.rs::validate_unsigned
- Entrypoint: pallet_call_decompressor::decompress_call(origin=None, compressed, encoded_call_size)
- Attacker controls: compressed call bytes, claimed decompressed size, SCALE-encoded runtime calls, and replay ordering
- Exploit idea: Reach an unsigned runtime call that the base filter or subtype checks meant to exclude. Use a batch with one honest item and one duplicated or reordered item to see whether unique-item assumptions collapse.
- Invariant to test: the decompressor must never expand its caller privilege beyond the explicitly supported unsigned subcalls
- Expected Immunefi impact: Critical: unauthenticated cross-chain governance or host-management execution.
- Fast validation: Try alternate encoded calls that are adjacent to the allowlisted subcalls and assert validation and execution both reject them. Write a focused batch test with repeated indices or commitments and assert only unique authenticated items can affect state.
