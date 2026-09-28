### Title
Quadratic CPU exhaustion in `ThresholdKeys::read` via attacker-controlled `t`/`n` (algorithmic-complexity DoS) - (File: crypto/dkg/src/lib.rs)

### Summary
The Swift CVE is a parser where attacker-controlled bytes trigger super-linear CPU (regex catastrophic backtracking). The analog in Serai is `ThresholdKeys::<C>::read` / `ThresholdKeys::new`, where two attacker-supplied `u16` fields (`t`, `n`) drive an `O(t²)` Lagrange interpolation over `O(n)` attacker-supplied points. A ~2.1 MB serialized blob forces ~4.3×10⁹ scalar multiplications plus ~65k field inversions — minutes of CPU per message, on an unauthenticated deserialization path explicitly exposed to untrusted bytes.

### Finding Description
`ThresholdKeys::read` deserializes `t`, `n`, `i` directly from the reader (`crypto/dkg/src/lib.rs:591-602`). `Participant::new` only rejects `0` (`lib.rs:29-35`) and `ThresholdParams::new` only enforces `0 < t <= n` and `i <= n` (`lib.rs:166-179`), so `t = n = 65535` is accepted. It then reads `n` group elements via `C::read_G` (`lib.rs:620-623`) and calls `ThresholdKeys::new` (`lib.rs:625-631`).

Inside `new`, the group key is computed as:

```rust
let t = (1 ..= params.t()).map(Participant).collect::<Vec<_>>();
let group_key =
  t.iter().map(|i| verification_shares[i] * interpolation.interpolation_factor(*i, &t)).sum();
```
(`lib.rs:376-378`)

For `Interpolation::Lagrange`, each `interpolation_factor` call iterates the full `included` vector (`lib.rs:234-246`), multiplying numerator and denominator and performing a field inversion. That makes `new` perform `t` factor computations, each `O(t)` — `O(t²)` total field operations, plus `t` point-scalar multiplications and `t` inversions. The same quadratic pattern exists in `ThresholdView::view` (`lib.rs:494-507`), which recomputes a per-participant interpolation factor for every included signer.

Unlike a bounded `O(n)` parse, the work here is quadratic in the byte-derived `t`, while the required input is only `O(n)` — a ~2.1 MB payload (65,535 × 32-byte points) yields billions of field multiplications. This is the same amplification shape as the reported ReDoS: small, unauthenticated input → disproportionate CPU burn.

### Impact Explanation
An unauthenticated party that can feed bytes to `ThresholdKeys::read` (e.g., a processor/coordinator message path or any peer-supplied serialized key blob) can pin a worker for minutes per message. Repeated submissions exhaust all workers handling deserialization, denying service to signing/key-gen — matching the CVE's "exhaust all worker threads → complete denial of service" impact. The check happens before any authentication of the bytes' semantic validity (params and points are structural only).

### Likelihood Explanation
Reachability only requires an untrusted `Read` reaching `ThresholdKeys::read`, which the threat model explicitly includes (`ThresholdKeys::read` is a listed public-input sink). No valid signatures, stakes, or protocol state are needed — the expensive work runs inside `read`/`new` before the caller can reject the input. Cost to the attacker is ~2 MB of bandwidth and trivial point construction (any canonical encodings work; they need not correspond to a real key). `n = 65535` is accepted because `Participant` permits every non-zero `u16`.

### Recommendation
- Enforce a protocol-level maximum on `t`/`n` (e.g., the already-defined `MAX_KEY_SHARES_PER_SET`-style bound) inside `ThresholdParams::new` or at the top of `ThresholdKeys::read`, before interpolation.
- Alternatively, compute the group key in `O(t)` via incremental/products-prefix Lagrange evaluation, or cache interpolation factors instead of recomputing each `O(t)` factor per participant in `ThresholdKeys::new` and `ThresholdView::view`.
- Bound `verification_shares.len()` checks before any `O(t²)` work (already done for count validity — keep the new size cap adjacent to it).

### Proof of Concept
Conceptual reproduction against `crypto/dkg`:

```rust
use ciphersuite::Ciphersuite;
use frost::curve::Curve; // e.g. frost::curve::Ed25519
use dkg::ThresholdKeys;

// Build a serialized ThresholdKeys blob with t = n = 65535
let mut buf = vec![];
buf.extend(u32::try_from(C::ID.len()).unwrap().to_le_bytes());
buf.extend(C::ID);
buf.extend(65535u16.to_le_bytes()); // t
buf.extend(65535u16.to_le_bytes()); // n
buf.extend(1u16.to_le_bytes());     // i
buf.push(1);                        // Interpolation::Lagrange
buf.extend(C::F::ONE.to_repr().as_ref()); // secret_share

// n canonical, non-identity points (any valid encodings)
let p = C::generator().to_bytes();
for _ in 0 .. 65535u32 {
    buf.extend(p.as_ref());
}

// ~4.3e9 field multiplications + 65k inversions inside ThresholdKeys::new
let _ = ThresholdKeys::<C>::read(&mut buf.as_slice());
```

Root cause verified in code:
- Unbounded `t`/`n` acceptance: `ThresholdParams::new` at `crypto/dkg/src/lib.rs:166-179`, `Participant::new` at `lib.rs:29-35`.
- Attacker-controlled params in the read path: `lib.rs:591-623`.
- Quadratic work: `ThresholdKeys::new` group-key loop at `lib.rs:376-378` calling `Interpolation::interpolation_factor` (`lib.rs:226-249`), which is `O(t)` per participant — `O(t²)` overall; same pattern in `ThresholdView::view` (`lib.rs:494-507`).

Caveat: the attacker's input scales linearly with `n` (~32 bytes per participant), so this is quadratic-vs-linear amplification rather than a fixed-size trigger; severity is Medium, consistent with the source CVE's availability-only rating.