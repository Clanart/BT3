### Title
Unbounded `n` in `ThresholdKeys::read` permits attacker-amplified memory exhaustion - (File: crypto/dkg/src/lib.rs)

### Summary
`ThresholdKeys::read` deserializes the participant count `n` directly from untrusted bytes as a `u16` (up to 65,535) and immediately uses it to pre-allocate a scalar vector (`Vec::with_capacity(usize::from(n))`) and to drive two read loops (`n` scalars for `Interpolation::Constant`, `n` group elements for `verification_shares`). No sanity bound on `n` is applied before allocation, so a handful of attacker-controlled bytes forces allocations and work scaled by 65,535 — a direct analog of "malformed length/integer field outside the expected range causes denial of service."

### Finding Description [1](#0-0) 

- `t`, `n`, and `i` are read as raw `u16`/`Participant` from the wire (`crypto/dkg/src/lib.rs:591-602`). `n` is trusted as a loop bound and capacity hint before `ThresholdParams::new` has any chance to reject it.
- For `Interpolation::Constant` (tag `0`), `Vec::with_capacity(usize::from(n))` allocates space for up to 65,535 scalars (~2 MiB for a 32-byte field) before a single byte of the payload is validated (`crypto/dkg/src/lib.rs:604-613`).
- Then `for l in (1 ..= n)` inserts `n` verification shares into a `HashMap`, each via `C::read_G` (`crypto/dkg/src/lib.rs:620-623`). With a reader that keeps returning data (or a moderately sized buffer), up to 65,535 point decompressions and hashmap insertions run; even on early EOF, the constant-interpolation capacity allocation has already occurred.
- Only after all of this does `ThresholdParams::new(t, n, i)` / `ThresholdKeys::new` run validation (`crypto/dkg/src/lib.rs:625-631`) — too late to bound the allocation.

The input-to-allocation amplification is ~65,000×: a ~13-byte prefix (`id_len`, `id`, `t`, `n`, `i`, interpolation tag) is sufficient to trigger a multi-MiB allocation and a failed read; repeated calls (e.g., feeding `ThresholdKeys::read` untrusted serialized key blobs, a supported entry point per the engagement scope) convert a trivial bandwidth cost into sustained allocator pressure, fragmenting/exhausting memory of the host process.

For comparison, the sibling deserializer in the same codebase does enforce a product-size cap before allocating (`coordinator/src/tributary/transaction.rs:286-290` checks `commitments_len * each_commitments_len > TRANSACTION_SIZE_LIMIT`), showing the expected defensive pattern `ThresholdKeys::read` lacks.

### Impact Explanation
An unprivileged party who can cause a Serai node/library user to call `ThresholdKeys::<C>::read` on attacker-controlled bytes can force the process to allocate ~2 MiB (scalars) plus ~2 MiB+ (verification share storage) and perform up to 65,535 elliptic-curve point decodings per call, from a message as small as ~13 bytes. Issued repeatedly or against many buffered blobs, this is a CPU- and memory-exhaustion denial of service against the threshold-key loading path — matching the CVE-2025-30355 class (an out-of-range integer field degrading availability of the federating participant).

### Likelihood Explanation
Likelihood is deployment-dependent: `ThresholdKeys::read` is only exploitable where serialized threshold keys are accepted from an untrusted source (e.g., a coordinated backup/restore, peer-provisioned key material, or an RPC surface that accepts key blobs). Within those paths, triggering is trivial and reliable — no race, no brute force; the malformed `n` field alone is sufficient. Severity is bounded by the `u16` cap (≈65,535 units of work per call), keeping this at Medium rather than High.

### Recommendation
Validate `n` (and `t ≤ n`, `i ≤ n`, `t ≥ 1`) against `ThresholdParams` limits *before* allocating — i.e., construct `ThresholdParams::new(t, n, i)` immediately after reading the three `u16`s and reject early. Additionally:
- Replace `Vec::with_capacity(usize::from(n))` with incremental `push` (allocation then tracks actual bytes consumed), and
- Impose a hard upper bound on `n` consistent with Serai's real validator-set sizes (e.g., reject `n > MAX_PARTICIPANTS`) so that even a fully-formed 65,535-share blob cannot be fed in.

### Proof of Concept
Conceptual PoC (Ristretto as `C`):

```rust
use ciphersuite::Ciphersuite;
use ciphersuite::Ristretto;
use dkg::ThresholdKeys;
use std::io::Cursor;

// Craft a serialized ThresholdKeys blob with n = 0xFFFF.
let mut blob = vec![];
blob.extend((Ristretto::ID.len() as u32).to_le_bytes());
blob.extend(Ristretto::ID);
blob.extend(1u16.to_le_bytes());        // t = 1
blob.extend(0xffffu16.to_le_bytes());   // n = 65535 -> 2 MiB with_capacity alloc
blob.extend(1u16.to_le_bytes());        // i = 1
blob.push(0);                           // Interpolation::Constant -> Vec::with_capacity(65535)

// Even though the stream is now exhausted and the read fails moments later,
// crypto/dkg/src/lib.rs:608 has already allocated ~2 MiB of scalar storage.
let res = ThresholdKeys::<Ristretto>::read(&mut Cursor::new(blob));
assert!(res.is_err());
```

Each ~20-byte invocation forces a multi-MiB allocation (and, if the attacker supplies a few MiB of trailing data, up to 65,535 `read_G` point decodings plus `HashMap` insertions before `ThresholdParams::new` runs). A loop of such inputs exhausts the victim's memory/CPU from negligible bandwidth.

### Citations

**File:** crypto/dkg/src/lib.rs (L591-623)
```rust
    let (t, n, i) = {
      let mut read_u16 = || -> io::Result<u16> {
        let mut value = [0; 2];
        reader.read_exact(&mut value)?;
        Ok(u16::from_le_bytes(value))
      };
      (
        read_u16()?,
        read_u16()?,
        Participant::new(read_u16()?).ok_or(io::Error::other("invalid participant index"))?,
      )
    };

    let mut interpolation = [0];
    reader.read_exact(&mut interpolation)?;
    let interpolation = match interpolation[0] {
      0 => Interpolation::Constant({
        let mut res = Vec::with_capacity(usize::from(n));
        for _ in 0 .. n {
          res.push(C::read_F(reader)?);
        }
        res
      }),
      1 => Interpolation::Lagrange,
      _ => Err(io::Error::other("invalid interpolation method"))?,
    };

    let secret_share = Zeroizing::new(C::read_F(reader)?);

    let mut verification_shares = HashMap::new();
    for l in (1 ..= n).map(Participant) {
      verification_shares.insert(l, <C as Ciphersuite>::read_G(reader)?);
    }
```
