### Title
Unbounded attacker-controlled signature count in `SchnorrAggregate::read`/`verify` enables computational DoS - (File: crypto/schnorr/src/aggregate.rs)

### Summary
`SchnorrAggregate::read` accepts a 32-bit count of nonce points from untrusted input and `SchnorrAggregate::verify` performs expensive per-element work — a `weight()` computation costing hundreds of sequential field operations per entry plus a multiexponentiation — with no cap on the number of aggregated signatures. This is the same bug class as the reported `eth_getRestorationProof` issue: a public-facing, unbounded verification path whose cost is driven entirely by attacker-chosen parameters.

### Finding Description
In `crypto/schnorr/src/aggregate.rs`, `SchnorrAggregate::read` reads a `u32` length prefix and then loops `u32::from_le_bytes(len)` times calling `C::read_G`, permitting up to ~4.3 billion entries with no sanity bound [1](#0-0) .

The expensive path is `verify`: for every `(key, challenge)` pair it calls `weight(&mut digest)` [2](#0-1) . `weight` reduces `(F::NUM_BITS + 128)` bits by loading 64-bit words, and for each word after the first performs `for _ in 0..64 { res += res; }` — i.e., ~320 sequential field doublings for a 256-bit field — plus repeated `digest.challenge` invocations [3](#0-2) . It then runs `multiexp_vartime` over `2 * n + 1` pairs [4](#0-3) .

An unprivileged attacker who can feed bytes to `SchnorrAggregate::read` and trigger `verify` (a listed in-scope reachable API) controls `n` directly. Each ~32-byte point they supply forces a point decompression in `read`, plus ~hundreds of field multiplications in `weight`, plus multiexp table work in `verify` — an asymmetric cost per byte of input with no protocol-level maximum. The cost grows without bound as `n` grows, identical in shape to the `TargetEpoch = 0` issue where the work scales with an attacker-selected parameter that cannot be disabled.

### Impact Explanation
Any service that deserializes and verifies `SchnorrAggregate` values from untrusted input can be driven into CPU exhaustion by submitting aggregates with a large signature count. Cost per request scales linearly in the attacker-chosen count while the attacker's bandwidth cost is only ~32 bytes per unit of work, plus the superlinear constant factor from `weight()`'s ~320 doublings per element. Repeated requests can starve honest verification work.

### Likelihood Explanation
The vector requires only that untrusted bytes reach `SchnorrAggregate::read`/`verify` — precisely the public-input reachability class in scope. No key knowledge, valid signatures, or participant status is needed; `verify` does not check `n` against any bound before doing the expensive work. A single crafted payload with thousands of `Rs` entries is enough to impose disproportionate CPU load.

### Recommendation
- Impose a protocol-level maximum on the aggregate size, enforced in `SchnorrAggregate::read` before the loop (e.g., reject `len` exceeding a configured cap).
- Enforce the same bound in `verify` as a precondition, rather than relying on callers.
- Consider deriving all `weight` scalars via a single transcript squeeze or a cheaper reduction (e.g., `hash_to_F`-style wide reduction already used elsewhere in the codebase) so per-element cost is a constant-time hash instead of hundreds of sequential doublings.

### Proof of Concept
```rust
use schnorr::{SchnorrAggregate, SchnorrSignature};
use ciphersuite::{Ciphersuite, Secp256k1}; // or any in-scope Ciphersuite
use group::GroupEncoding;

// Attacker-controlled bytes: u32 count = N, then N * 32-byte points, then a scalar.
let n: u32 = 1_000_000; // ~32 MB of input, or scale down for constrained channels
let mut buf = n.to_le_bytes().to_vec();
let r = <Secp256k1 as Ciphersuite>::G::generator().to_bytes();
for _ in 0 .. n {
  buf.extend(r.as_ref());
}
buf.extend(<Secp256k1 as Ciphersuite>::F::ZERO.to_repr().as_ref());

// read() succeeds and performs N point decompressions.
let agg = SchnorrAggregate::<Secp256k1>::read(&mut buf.as_slice()).unwrap();

// verify() runs weight() N times: ~320 field doublings each (~3.2e8 field ops)
// plus a multiexp over 2N+1 pairs, even though every statement is trivially false.
let keys_and_challenges: Vec<(_, _)> = (0 .. n as usize)
  .map(|_| (<Secp256k1 as Ciphersuite>::G::generator(), <Secp256k1 as Ciphersuite>::F::ONE))
  .collect();
let _ = agg.verify(b"dst", &keys_and_challenges); // CPU cost fully attacker-controlled
```

### Citations

**File:** crypto/schnorr/src/aggregate.rs (L35-63)
```rust
  let BYTES: usize = usize::try_from((F::NUM_BITS + 128).div_ceil(8)).unwrap();

  let mut remaining = BYTES;

  // We load bits in as u64s
  const WORD_LEN_IN_BITS: usize = 64;
  const WORD_LEN_IN_BYTES: usize = WORD_LEN_IN_BITS / 8;

  let mut first = true;
  while i < remaining {
    // Shift over the already loaded bits
    if !first {
      for _ in 0 .. WORD_LEN_IN_BITS {
        res += res;
      }
    }
    first = false;

    // Add the next 64 bits
    res += F::from(u64::from_be_bytes(bytes[i .. (i + WORD_LEN_IN_BYTES)].try_into().unwrap()));
    i += WORD_LEN_IN_BYTES;

    // If we've exhausted this challenge, get another
    if i == bytes.len() {
      bytes = digest.challenge(b"aggregation_weight_continued");
      remaining -= i;
      i = 0;
    }
  }
```

**File:** crypto/schnorr/src/aggregate.rs (L77-88)
```rust
  pub fn read<R: Read>(reader: &mut R) -> io::Result<Self> {
    let mut len = [0; 4];
    reader.read_exact(&mut len)?;

    #[allow(non_snake_case)]
    let mut Rs = vec![];
    for _ in 0 .. u32::from_le_bytes(len) {
      Rs.push(C::read_G(reader)?);
    }

    Ok(SchnorrAggregate { Rs, s: C::read_F(reader)? })
  }
```

**File:** crypto/schnorr/src/aggregate.rs (L138-145)
```rust
    let mut pairs = Vec::with_capacity((2 * keys_and_challenges.len()) + 1);
    for (i, (key, challenge)) in keys_and_challenges.iter().enumerate() {
      let z = weight(&mut digest);
      pairs.push((z, self.Rs[i]));
      pairs.push((z * challenge, *key));
    }
    pairs.push((-self.s, C::generator()));
    multiexp_vartime(&pairs).is_identity().into()
```
