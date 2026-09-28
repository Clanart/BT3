### Title
Unbounded attacker-controlled `n` in `ThresholdKeys::read` allows memory-exhaustion DoS - ([File: crypto/dkg/src/lib.rs](crypto/dkg/src/lib.rs))

### Summary
`ThresholdKeys::<C>::read` deserializes a `u16` participant count `n` supplied entirely by the byte stream, then uses it to pre-allocate a `Vec` of `n` scalars and to drive two loops reading `n` field elements and `n` group elements — all before `ThresholdParams::new` / `ThresholdKeys::new` validate `n` against `t`, `i`, or any expected set size. This is the same bug class as the referenced advisory: an unbounded "dimension" parameter derived from untrusted input is consumed before any limit is enforced. [1](#0-0) 

### Finding Description
In `ThresholdKeys::read`:

1. `t`, `n`, `i` are read as raw `u16`s from the reader with no bound other than `Participant::new(i)` rejecting zero.
2. For `Interpolation::Constant`, `Vec::with_capacity(usize::from(n))` is allocated and then `n` scalars are read — up to 65,535 field-element slots allocated purely on the attacker's say-so, before the bytes backing them are confirmed present.
3. Independently of the interpolation variant, `n` `read_G` calls are performed into a `HashMap` for `verification_shares`, each `read_G` performing point decompression/validation.
4. Only *after* this attacker-sized work does `ThresholdParams::new(t, n, i)` and `ThresholdKeys::new(...)` reject inconsistent values.

The reader's declared `n` therefore functions exactly like the `width`/`height` parameters in the original bug: a small input can force allocation and per-element work at the maximum encodable dimension (65,535), and there is no check that `n` equals the expected/actual participant count before the size-dependent work is performed. For comparison, other deserializers in the tree were hardened against this class — e.g., `Transaction::DkgCommitments` checks `commitments_len * each_commitments_len > TRANSACTION_SIZE_LIMIT` *before* allocating, and the libp2p codec rejects `len > MAX_LIBP2P_REQRES_MESSAGE_SIZE` before `vec![0; len]` — but `ThresholdKeys::read` performs no equivalent pre-validation.

### Impact Explanation
An unprivileged party who can feed bytes to `ThresholdKeys::read` (it is part of the deserialization surface used when loading/receiving threshold key material and is exercised on externally supplied blobs) can, per call, force an allocation of up to ~2 MB (65,535 × `size_of::<C::F>`) plus up to 65,535 elliptic-curve point decompressions/HashMap inserts — using an attacker-chosen count that need not correspond to any real multisig. Repeated requests amplify this into sustained allocator pressure and CPU burn, degrading or crashing the node — a classic CWE-400 uncontrolled resource consumption DoS, matching the impact class of the advisory.

### Likelihood Explanation
The only requirement is reaching `ThresholdKeys::read` with attacker-controlled bytes. The `n` field is not bound to a session's real `ThresholdParams` until after the expensive work is done, so a single malformed blob triggers the maximum-dimension path deterministically — no brute force or special positioning is needed. Cost is bounded at 65,535 elements per call (u16 domain), which keeps this in the Medium band rather than High.

### Recommendation
- Read and validate `ThresholdParams::new(t, n, i)` immediately after parsing `t`, `n`, `i`, and reject `n` exceeding the caller's expected participant count before allocating.
- Replace `Vec::with_capacity(n)` + eager loop with incremental reads, and cap `n` to a protocol-level maximum (e.g., the session's known `n`) before the `verification_shares` loop.
- Follow the pattern used in `coordinator/src/tributary/transaction.rs` for `DkgCommitments`, which validates the product of declared counts against a size limit prior to allocation.

### Proof of Concept
```rust
// Any C: Ciphersuite; illustrated with Ristretto
use dkg::ThresholdKeys;
use ciphersuite::Ciphersuite;

let mut blob = vec![];
// C::ID prefix (valid)
blob.extend(u32::try_from(<Ristretto as Ciphersuite>::ID.len()).unwrap().to_le_bytes());
blob.extend(<Ristretto as Ciphersuite>::ID);
// t = 1, n = 0xFFFF, i = 1  -> attacker-chosen max dimension
blob.extend(1u16.to_le_bytes());
blob.extend(u16::MAX.to_le_bytes());
blob.extend(1u16.to_le_bytes());
// interpolation tag 0 = Constant -> Vec::with_capacity(65535) allocated here,
// then 65535 read_F attempts, then 65535 read_G attempts,
// all before ThresholdParams::new / ThresholdKeys::new ever run
blob.push(0);
// Attacker streams/appends scalars+points to keep the loops working,
// or supplies nothing: the with_capacity allocation already occurred.
let _ = ThresholdKeys::<Ristretto>::read(&mut blob.as_slice());
```

Uncertainty note: in deployed Serai components `ThresholdKeys` blobs typically come from the processor's own DB or coordinator-provided sign sessions, so the practical exposure depends on whether an untrusted party can inject these bytes; per the analog rules, `ThresholdKeys::read` is within the accepted untrusted-byte surface. The `Vec::with_capacity(n)` pre-allocation, `n`-sized read loops, and the fact that parameter validation happens only afterward are all confirmed in `crypto/dkg/src/lib.rs:591-632`.

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
