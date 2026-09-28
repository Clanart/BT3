### Title
`Scanner::register_offset` increments the requested offset until the tweaked key is even, making the offset→key distribution non-uniform and silently collapsing distinct offsets - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
`Scanner::register_offset` resolves odd-Y tweaked keys not by resampling fresh randomness but by `offset += Scalar::ONE` in a loop, the same "increment-and-retry" pattern as the Infiltration wound-selection bug. This makes the effective offset distribution non-uniform (every odd-producing offset donates its probability mass to the next even-producing offset) and causes distinct requested offsets to collide onto already-registered scripts.

### Finding Description
In `networks/bitcoin/src/wallet/mod.rs`, `register_offset` computes `p2tr_script_buf(self.key + GENERATOR * offset)`; if the point is odd, `p2tr_script_buf` returns `None` and the loop retries with `offset + 1` rather than deriving a new offset:

```rust
None => offset += Scalar::ONE,
```
(networks/bitcoin/src/wallet/mod.rs:193)

Consequences:

1. **Non-uniform keyspace.** For any random offset `o`, if `key + o*G` is odd and `key + (o+1)*G` is even, both `register_offset(o)` and `register_offset(o+1)` resolve to the same effective offset `o+1`. Effective offsets reachable from an odd predecessor absorb double probability; offsets that follow an already-registered effective offset can never be used at all — exactly the "wounded agent absorbs the next index's mass" bias from the report.

2. **Silent collision / failed registration.** If `register_offset(o)` increments to `o+1` and registers the script, a later `register_offset(o+1)` — a legitimately distinct requested offset — finds `self.scripts.contains_key(&script)` and returns `None` (wallet/mod.rs:187-189). The docstring admits offsets are "surjective, not bijective" and ordering-dependent, but callers receiving `None` for a valid offset get no indication the key is already claimed. If a caller proceeds to pay to `key + (o+1)*G` anyway (or computes the expected address independently), `scan_transaction` only matches on `output.script_pubkey` (wallet/mod.rs:205) and the stored map has no distinct entry for it — outputs are either misattributed to the earlier offset or, if the caller derived a different address, never reported as received, leaving received funds undetected/unspendable.

### Impact Explanation
An unprivileged party who can cause offsets to be registered (e.g., by triggering deposit-address/key-derivation flows that feed into `Scanner::register_offset`) can pick two offsets `o` and `o+1` such that `key + o*G` is odd. Registering `o` claims effective offset `o+1`; the subsequent legitimate registration of `o+1` returns `None`, and the resulting payment script is either double-counted under the attacker's offset or the wallet reports funds received that map to the wrong offset scalar, breaking spendability attribution. Distribution-wise, some offsets become unreachable and others twice as likely, a gameable bias identical in shape to the Infiltration issue.

### Likelihood Explanation
Odd points occur with ~50% probability per offset, so collisions between a requested offset and its predecessor's effective offset are common whenever offsets are drawn from a contiguous or attacker-influenced space. Exploitation requires only the ability to request/derive offsets — public inputs to the wallet scanning path.

### Recommendation
Do not increment the offset to resolve oddness. Either reject odd-producing offsets entirely (`return None` and let the caller draw a new offset), or derive a fresh offset by hashing/retrying through fresh randomness — mirroring the report's recommendation to re-hash rather than increment. At minimum, return an error distinguishing "odd" from "collision" so callers never conflate a distinct offset with an already-registered script.

### Proof of Concept
```rust
// Let key be even so Scanner::new succeeds.
let mut scanner = Scanner::new(key).unwrap();

// Find offsets o, o+1 where key + o*G is odd and key + (o+1)*G is even.
let mut o = Scalar::ZERO;
while is_even(key + (ProjectivePoint::GENERATOR * o)) { o += Scalar::ONE; }

// Registering o silently bumps to o+1.
assert_eq!(scanner.register_offset(o).unwrap(), o + Scalar::ONE);

// The legitimately distinct offset o+1 is now unregistrable.
assert!(scanner.register_offset(o + Scalar::ONE).is_none());

// Any payment to script(key + (o+1)*G) is attributed to offset o's entry,
// while a caller that independently computed offset o+1 gets None
// and may still expect the address to be scanned.
```