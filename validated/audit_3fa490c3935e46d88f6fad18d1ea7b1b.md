### Title
`musig` panics on oversized key lists instead of returning `TooManyKeysProvided`, allowing a remote-triggered abort — ([File: crypto/dkg/musig/src/lib.rs](crypto/dkg/musig/src/lib.rs))

### Summary
The reported bug class is improper input validation where malformed/attacker-controlled input causes a process abort (CVE-2023-39456, Apache Traffic Server HTTP/2 abort). The analog in Serai is in `musig` (the n-of-n non-interactive DKG used for key aggregation): the function locates the local public key's index and converts it with `u16::try_from(...).expect(...)` *before* calling `check_keys`, which is the function responsible for rejecting key lists longer than `u16::MAX`. A key list with more than 65,535 entries, where the local public key sits at index ≥ 65,535, causes a panic instead of the documented `MusigError::TooManyKeysProvided` error.

### Finding Description
In `crypto/dkg/musig/src/lib.rs`, `musig` computes `our_i` via `keys.iter().position(|key| *key == our_pub_key)` at line 122, then performs `u16::try_from(our_i).expect("keys.len() <= u16::MAX yet index of keys > u16::MAX?")` at line 133. The invariant this `expect` relies on — `keys.len() <= u16::MAX` — is only enforced by `check_keys::<C>(keys)`, which is not called until line 126, *after* the fallible conversion. `check_keys` itself correctly maps oversized lists to `MusigError::TooManyKeysProvided` via `u16::try_from(keys.len())` at lines 51–52, but that error path is unreachable when the panic fires first.

The panic is reachable from untrusted input: in the MuSig protocol flow, the `keys` slice is the list of participant public keys, which is assembled from pubkeys contributed by counterparties. A participant (or a coordinator relaying a crafted participant set) that supplies a key list exceeding `u16::MAX` entries — with the victim's own key placed at an index beyond `u16::MAX` — triggers `expect` and aborts the process rather than receiving a clean `MusigError`. The same ordering defect means the panic is hit even for a list that `check_keys` would have rejected for a benign reason.

### Impact Explanation
A panic in a Rust process aborts the signing/key-generation task (and, absent `catch_unwind`, the entire node process). Analogous to the CVE, an unprivileged party supplying malformed participant input causes denial of service of the threshold-signing node. For a validator/processor running this code, crashing the key-aggregation path halts DKG completion and, depending on the caller, the whole processor — availability loss with no key compromise required. This is a reachable abort on malformed input, matching the report's bug class; impact is availability-only, hence Medium rather than High.

### Likelihood Explanation
Likelihood is moderate-to-low: triggering the panic requires a key list larger than 65,535 entries, which is a large but cheap message (each key is a 32-byte group encoding, ~2 MiB total), and requires the victim's pubkey to be positioned at index ≥ 65,535 — an attacker assembling the list controls ordering and can place the victim's key last. If the deployment never permits such large MuSig sets, the panic is unreachable in practice, but the code makes no such bound itself and the `expect`'s own comment shows the invariant was assumed rather than enforced. No secret leakage occurs; the impact is a deterministic crash.

### Recommendation
Call `check_keys::<C>(keys)?` before computing `our_i`, i.e., move line 126 above the `position` lookup at lines 121–124, so that oversized lists return `MusigError::TooManyKeysProvided` before any index conversion. Alternatively, replace the `expect` at line 133 with a `map_err` returning `MusigError::TooManyKeysProvided`. Reordering also removes the now-unnecessary justification in the `expect` message, since the invariant would be enforced rather than assumed.

### Proof of Concept
```rust
// In-scope: crypto/dkg/musig/src/lib.rs
// Conceptual PoC against `musig` for any Ciphersuite C:

let mut keys: Vec<C::G> = (0 ..= u16::MAX as usize)          // 65,536 attacker keys
    .map(|_| C::generator() * C::random_nonzero_F(&mut rng))
    .collect();
keys.push(C::generator() * our_private_key.deref());          // our key at index 65,536

// our_i = 65_536 -> u16::try_from(our_i) fails -> expect(...) panics
// check_keys (which would return TooManyKeysProvided) is never reached
let _ = musig::<C>(context, our_private_key, &keys);
```

Because `check_keys` runs after the `position`/`try_from` sequence, the documented `MusigError::TooManyKeysProvided { max: 65535, provided: 65537 }` path at lines 28–34 and 51–52 is bypassed and the thread panics on the `expect` at line 133.