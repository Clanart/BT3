### Title
FROST preprocess seed copied out of `Zeroizing` wrapper, leaving deterministic nonce seed residue in memory - (File: crypto/frost/src/sign.rs)

### Summary
CVE-2021-0938 is a `memzero_explicit` bypass: code appears to erase sensitive data, yet an escaping/defeated zeroization leaves secret residue that enables local information disclosure. In Serai's FROST implementation, `AlgorithmMachine::seeded_preprocess` copies the cached preprocess seed out of its `Zeroizing<[u8; 32]>` container (`ChaCha20Rng::from_seed(*seed.0)` at `crypto/frost/src/sign.rs:127`). `from_seed` takes the `[u8; 32]` by value, so the copy placed on the stack is outside the `Zeroizing` guard and is never cleared. That seed is the sole entropy input to `ChaCha20Rng`, which deterministically produces the FROST secret nonces (`d`, `e`) via `Commitments::new` → `NonceCommitments::new` → `C::random_nonce` (`crypto/frost/src/sign.rs:127-132`, `crypto/frost/src/nonce.rs:53-71`). Anyone recovering the residue recomputes the victim's nonces and, combined with the victim's broadcast `SignatureShare`, recovers their private key share.

### Finding Description
`CachedPreprocess` is documented as secret and stored in `Zeroizing` (`crypto/frost/src/sign.rs:88-92`), and `AlgorithmSignMachine` derives `Zeroize` so `self.seed` is wiped on drop (`crypto/frost/src/sign.rs:245-255`). However, the actual use of the seed unwraps the protection before use:

- `crypto/frost/src/sign.rs:127`: `let mut rng = ChaCha20Rng::from_seed(*seed.0);` — `*seed.0` is a `Copy` of the `[u8; 32]` into an unprotected stack argument. `Zeroizing`'s drop zeroizes `seed.0`, not the copy now living in the caller/callee stack frame.
- The same pattern repeats wherever `from_cache` is used (`crypto/frost/src/sign.rs:268-274`), and `ChaCha20Rng::from_seed(self.blame_entropy)` at `crypto/frost/src/sign.rs:473` leaves `blame_entropy` residue (blame entropy is only DoS-relevant, not secret).
- The copied seed is processed inside rand_chacha's `from_seed`/internals, which make no zeroization guarantee, so the 32-byte value persists in freed stack memory after `seeded_preprocess` returns.

Contrast with the careful handling elsewhere (`share_bytes.as_mut().zeroize()` in `crypto/dkg/src/lib.rs:553-555`, `key.copy_from_slice` + `zeroize` in `crypto/dkg/pedpop/src/encryption.rs:115-131`), which shows the codebase intends this class of material to be wiped.

### Impact Explanation
A FROST signature share leaks the secret key share if the nonce is known: `share = λ·sk + d + ρ·e` (with tweaks), so with `d`, `e` recovered the share equation is solved for `sk`. Because `seeded_preprocess` regenerates both `nonces` deterministically from the seed (line 128-132), residue of the seed is equivalent to residue of the raw nonces — precisely the catastrophic material `Nonce`/`CachedPreprocess` exist to protect (`crypto/frost/src/sign.rs:85-87`). An attacker who also caused the victim to sign a message (a legitimate, in-scope capability — signers expose `read_preprocess`/`sign`/`complete` over messages supplied in the protocol) can pair the published `SignatureShare` with the recovered nonces to extract the participant's threshold secret share, defeating the scheme's defense-in-depth hygiene. This mirrors the advisory's "local information disclosure" impact: no extra execution privileges are needed, and the residue exists even though every typed field is nominally `Zeroizing`/`Zeroize`.

### Likelihood Explanation
Exploitation requires a party able to read the victim's process memory after `preprocess`/`from_cache` runs (co-resident process, core dump, swap, allocator reuse within the same process — the same local-adversary model as CVE-2021-0938, AV:L). No malicious threshold assumption is needed: the victim is an honest signer performing a normal `preprocess` call, and the attacker only needs to be a co-signer supplying `msg`/preprocesses plus the memory read. Memory-residue exploitation is inherently probabilistic and environment-dependent, keeping this Medium rather than High.

### Recommendation
Stop unwrapping the seed before consumption. Options:
- Derive nonces without copying: expand the seed through a `Zeroizing` transcript/hash into a per-use buffer that is itself `Zeroizing`, or hold the `ChaCha20Rng` seed in a `Zeroizing<[u8;32]>`-backed custom `RngCore`/`SeedableRng` implementation.
- Immediately `zeroize()` any stack copy used as `from_seed` input (e.g., bind `let mut s = *seed.0; let rng = ChaCha20Rng::from_seed(s); s.zeroize();`), acknowledging that register/copy elision still makes this brittle — the transcript-based derivation is the robust fix.
- Apply the same pattern to `blame_entropy` for uniformity, and audit other `*seed.0`-style dereferences of `Zeroizing` contents.

### Proof of Concept
```rust
// crypto/frost/src/sign.rs
let mut rng = ChaCha20Rng::from_seed(*seed.0); // line 127
// `*seed.0`: [u8; 32] is Copy -> placed on stack outside Zeroizing guard.
// Zeroizing's Drop only wipes `seed.0`, not this copy.
```
Exploitation path (local attacker):
1. Victim calls `AlgorithmMachine::preprocess(rng)` (`sign.rs:171-178`) or `SignMachine::from_cache` (`sign.rs:268-274`), hitting `seeded_preprocess`.
2. Attacker reads freed stack/heap in the victim process and recovers the 32-byte seed.
3. Attacker replays `ChaCha20Rng::from_seed(seed)` + `Commitments::new(&mut rng, _, &algorithm.nonces())` (`nonce.rs:107-124`) to recompute the victim's `(d, e)` nonces.
4. From the victim's broadcast `SignatureShare` for a message the attacker submitted (`sign.rs:398-409`), solve `sk = (share - d - ρ·e)·λ⁻¹` (with scalar/offset adjustments), recovering the threshold key share — equivalent to the "knowledge of it also enables recovery" property documented at `sign.rs:85-87`.