# Q1587: Starknet nonce bitmap helper bitmap slot boundary corrupts replay protection via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public nonce-tracking path through `fin_transfer`` and then replay or reorder the earlier source-chain event or later forwarded bridge leg so that `starknet/src/omni_bridge.cairo::_nonce_slot_and_bit` ends up accepting two inconsistent interpretations of the same economic event specifically around `bitmap slot boundary corrupts replay protection` under packs nonces into `(slot = nonce / 251, bit = pow2(nonce % 251))` before writing the bitmap, violating `bitmap packing must mark exactly one nonce per settlement and must not alias, overflow, or skip boundary values`?

## Target
- File/function: `starknet/src/omni_bridge.cairo::_nonce_slot_and_bit`
- Entrypoint: `public nonce-tracking path through `fin_transfer``
- Attacker controls: destination nonce values across slot boundaries and extremal `u64` inputs
- Exploit idea: Probe nonces around `250/251/252`, zero, and max `u64` values in the Starknet bitmap scheme. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: bitmap packing must mark exactly one nonce per settlement and must not alias, overflow, or skip boundary values
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Set and query boundary nonces and assert that each write flips exactly one intended replay bit. Then replay or reorder the earlier source-chain event or later forwarded bridge leg and assert that the bridge still exposes only one valid economic outcome.
