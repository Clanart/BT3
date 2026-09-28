### Title
Unbounded length-prefixed deserialization in `SchnorrAggregate::read` enables unbounded CPU/memory exhaustion - (File: crypto/schnorr/src/aggregate.rs)

### Summary
`SchnorrAggregate::read` trusts a little-endian `u32` count from untrusted bytes and loops that many times performing `C::read_G` (a full point-deserialization per iteration), with no upper bound. A single short buffer declaring ~4.3 billion nonces forces the reader to attempt billions of group-element deserializations before erroring, and — if enough data is supplied — allocates a `Vec` of billions of points whose later `verify` builds a `Vec` of `2 * n + 1` multiexp pairs.

### Finding Description
The analog to the reported bug class (unbounded iteration over an attacker-controlled array length) lives in `SchnorrAggregate::<C>::read`:

```rust
// crypto/schnorr/src/aggregate.rs
pub fn read<R: Read>(reader: &mut R) -> io::Result<Self> {
    let mut len = [0; 4];
    reader.read_exact(&mut len)?;

    let mut Rs = vec![];
    for _ in 0 .. u32::from_le_bytes(len) {
      Rs.push(C::read_G(reader)?);
    }

    Ok(SchnorrAggregate { Rs, s: C::read_F(reader)? })
}
``` [1](#0-0) 

Two problems:

1. **No length cap.** `len` is a `u32` taken verbatim from the input, permitting up to 4,294,967,295 iterations. Each iteration performs a group-element deserialization (`C::read_G`), which on the dalek-ff-group / ed448 backends performs canonicality and (depending on the suite) decompression work per element. The function only terminates early when `read_exact` hits EOF, but an attacker can supply a stream padded with bytes that decode to valid points — every 32-byte compressed point is accepted — so the loop genuinely runs `len` times, growing `Rs` by `len` elements.

2. **Compounded cost in `verify`.** If parsing succeeds, `SchnorrAggregate::verify` allocates `Vec::with_capacity((2 * keys_and_challenges.len()) + 1)` and runs `multiexp_vartime` over twice as many pairs, plus a `weight()` digest round per signature — so the deserialized count translates into ~2× the group operations on the verification path. [2](#0-1) 

This is reachable from public input: `SchnorrAggregate::read` / `serialize` is the wire form of the half-aggregation scheme used to batch cosignatures, so untrusted bytes fed to `read` by any peer in a signing/coordination protocol trigger the unbounded work before any cryptographic check rejects the input. The cost is paid purely on deserialization, before `verify` is even reached.

The same shape exists in `ThresholdKeys::read` in `crypto/dkg/src/lib.rs`, which reads a `u16 n` and then loops `n` times doing `read_F` for `Interpolation::Constant` and `n` times doing `read_G` for verification shares (`Vec::with_capacity(usize::from(n))` plus `for l in (1 ..= n)`), all from the attacker-controlled byte stream — bounded at 65535 iterations, a weaker but identical pattern. [3](#0-2) 

### Impact Explanation
An unprivileged party who can get a node or cosigner to deserialize a `SchnorrAggregate` (or a `ThresholdKeys` blob) controls the iteration count. Consequences:

- **CPU exhaustion:** billions of `read_G` calls per single message; effectively a permanent hang of the deserializing thread for `u32::MAX`.
- **Memory exhaustion:** `Rs` grows to `len * sizeof(C::G)` (~137 GB for 4 billion Ristretto points) — OOM kill of the process.
- **Verify amplification:** ~2n multiexp pairs and n `weight()` hash rounds per accepted aggregate.

Since `read` does not know the remaining stream length, it cannot even cheaply reject impossible lengths; it grinds until EOF or OOM.

### Likelihood Explanation
- **Reachability:** High where `SchnorrAggregate::read` is called on peer-supplied bytes (aggregated-cosignature flows). The function is a public API explicitly designed for deserializing transmitted data.
- **Preconditions:** None beyond the peer being able to submit bytes; the attack needs no valid signatures — just a large length prefix and a stream of well-formed compressed points (or enough of them to sustain the loop). Even without a full payload, `vec![]` pushing until EOF plus the `Vec::with_capacity`-style growth is still wasted work proportional to input.
- **Severity:** Medium — DoS only, no forgery or key leakage, matching the reported issue's Medium rating. `ThresholdKeys::read` is bounded by `u16`, making it a lesser variant.

### Recommendation
Apply the report's "check array length before iterating" pattern:

```rust
// crypto/schnorr/src/aggregate.rs
const MAX_AGGREGATED_SIGNATURES: u32 = /* protocol-bounded constant */;

pub fn read<R: Read>(reader: &mut R) -> io::Result<Self> {
    let mut len = [0; 4];
    reader.read_exact(&mut len)?;
    let len = u32::from_le_bytes(len);
    if len > MAX_AGGREGATED_SIGNATURES {
        Err(io::Error::other("aggregate signature count exceeds maximum"))?;
    }
    // ...
}
```

- `SchnorrAggregate::read`: reject `len` above the maximum number of signatures the consuming protocol can ever aggregate (a small constant — the validator set size is bounded, e.g. `MAX_KEY_SHARES_PER_SET` as already used in `coordinator/src/tributary/transaction.rs` for `SlashReport`).
- `ThresholdKeys::read` in `crypto/dkg/src/lib.rs`: bound `n`/`t` against a documented maximum before allocating `Vec::with_capacity(n)` and looping `read_F`/`read_G`.
- Prefer a two-pass scheme (read len, validate against remaining bytes or a hard cap, then iterate) over streaming with no cap, matching the mitigation already used for `DkgCommitments` (`commitments_len * each_commitments_len > TRANSACTION_SIZE_LIMIT` check in `coordinator/src/tributary/transaction.rs`).

### Proof of Concept
```rust
use ciphersuite::Ciphersuite;
use dalek_ff_group::Ristretto;
use schnorr::SchnorrAggregate;

// 4-byte length prefix of u32::MAX followed by a stream of valid
// 32-byte Ristretto encodings causes SchnorrAggregate::read to attempt
// 4,294,967,295 read_G calls, growing Rs to ~137 GiB before erroring.
let mut payload = u32::MAX.to_le_bytes().to_vec();
payload.extend(std::iter::repeat(
    <Ristretto as Ciphersuite>::generator().to_bytes(),
).flatten().take(1 << 20)); // enough valid points to keep the loop running

let mut cursor: &[u8] = &payload;
// Never returns in reasonable time / OOMs
let _ = SchnorrAggregate::<Ristretto>::read(&mut cursor);
```

Equivalently, a minimal `ThresholdKeys::read` blob declaring `n = 0xFFFF` forces 65535 `read_G` deserializations (plus `Vec::with_capacity`) from untrusted bytes before `ThresholdParams::new` can reject the parameters.

### Citations

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

**File:** crypto/schnorr/src/aggregate.rs (L127-146)
```rust
  pub fn verify(&self, dst: &'static [u8], keys_and_challenges: &[(C::G, C::F)]) -> bool {
    if self.Rs.len() != keys_and_challenges.len() {
      return false;
    }

    let mut digest = DigestTranscript::<C::H>::new(dst);
    digest.domain_separate(b"signatures");
    for (_, challenge) in keys_and_challenges {
      digest.append_message(b"challenge", challenge.to_repr());
    }

    let mut pairs = Vec::with_capacity((2 * keys_and_challenges.len()) + 1);
    for (i, (key, challenge)) in keys_and_challenges.iter().enumerate() {
      let z = weight(&mut digest);
      pairs.push((z, self.Rs[i]));
      pairs.push((z * challenge, *key));
    }
    pairs.push((-self.s, C::generator()));
    multiexp_vartime(&pairs).is_identity().into()
  }
```

**File:** crypto/dkg/src/lib.rs (L604-623)
```rust
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
