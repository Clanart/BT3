### Title
Unbounded attacker-controlled element count in `SchnorrAggregate::read` enables remote resource exhaustion - (File: crypto/schnorr/src/aggregate.rs)

### Summary
`SchnorrAggregate::read` reads a 4-byte length prefix directly from the input stream and then loops `0 .. u32::from_le_bytes(len)` times, calling `C::read_G(reader)` and pushing each result into `Rs` — a maximum of 2³²-1 group-element decodings and Vec growth driven entirely by attacker-supplied bytes, with no upper bound, no consistency check against remaining input length, and no allocation cap [1](#0-0) .

### Finding Description
The external report (CVE-2019-9371, libvpx) describes resource exhaustion caused by improper validation of attacker-controlled input. The analogous shape exists in Serai's Schnorr half-aggregation deserialization: the serialized form is `u32 count || count * G-encodings || F`, and `count` is trusted blindly as the loop bound.

```rust
// crypto/schnorr/src/aggregate.rs:77-85
pub fn read<R: Read>(reader: &mut R) -> io::Result<Self> {
  let mut len = [0; 4];
  reader.read_exact(&mut len)?;
  let mut Rs = vec![];
  for _ in 0 .. u32::from_le_bytes(len) {
    Rs.push(C::read_G(reader)?);
  }
  Ok(SchnorrAggregate { Rs, s: C::read_F(reader)? })
}
```

Two compounding issues:

1. **No plausibility bound.** A verifier knows the aggregate can only be as large as the number of signatures it requested, yet `read` accepts any count up to `u32::MAX`. Every iteration performs a full point decompression (`read_G` does canonical-encoding checks and, for dalek-ff-group curves, point validation) and grows `Rs` without bound.
2. **DoS precedes verification.** Exhaustion happens during parsing, before `SchnorrAggregate::verify` (which itself allocates `Vec::with_capacity(2*keys_and_challenges.len()+1)` and runs a multiexponentiation proportional to the attacker-controlled `Rs.len()` [2](#0-1) ) is ever reached.

Any caller that feeds this function a stream reader (network message, `io::Read` wrapper over a socket) rather than a strictly pre-sized slice is exposed: a peer that keeps sending bytes keeps the loop decoding and allocating. Even where the reader is a bounded buffer, a `len` field of e.g. `0xFFFFFFFF` forces up to 4 billion decompression attempts until EOF — the error path only short-circuits on the first `read_exact` failure, so an attacker controlling the buffer size controls the work factor. Compare with the defensive pattern used elsewhere in the codebase, where a claimed count is cross-checked against a hard limit *before* the loop, e.g. `commitments_len * each_commitments_len > TRANSACTION_SIZE_LIMIT` in the tributary transaction parser [3](#0-2)  — `SchnorrAggregate::read` has no equivalent check.

### Impact Explanation
An unprivileged peer able to deliver serialized `SchnorrAggregate` bytes to a verifying node causes CPU exhaustion (billions of elliptic-curve decompressions) and/or memory exhaustion (`Rs` growing to tens of GB for curve points, since each valid `C::G` is a full group element — e.g. ~40+ bytes heap cost plus Vec overhead for Ristretto). Because the failure mode is unbounded work inside a single `read` call, it denies service to the whole verification path processing the message, matching the CVE's remote-DoS-with-user-interaction class (the victim must process an attacker-supplied aggregate).

### Likelihood Explanation
Reachability requires a call site passing untrusted bytes through a reader that isn't already size-capped upstream. In Serai's deployment the aggregate is exchanged as a length-prefixed protocol message; if the transport enforces a hard message bound the practical impact is bounded by that bound, which tempers the rating. However, the parse itself is unbounded relative to the actual content — a message can claim `count = u32::MAX` while the loop only terminates on decompression failure or EOF — so any framing that permits large messages, chunked streaming, or deferred length enforcement is exploitable by a single malicious sender. Medium severity aligns with the source advisory (DoS, no key/signing impact).

### Recommendation
Bound `count` before looping:

- Reject `count` values exceeding a protocol maximum (e.g. the number of signatures the verifier actually requested, passed in as a parameter, or a hard constant like `u16::MAX`).
- Preferably, remove the embedded count entirely and require the caller to supply the expected signature count — mirroring `MultiDLEqProof::read(r, discrete_logs)` where the element count comes from the caller's context rather than the untrusted stream [4](#0-3) .
- If `read` is used in `verify` contexts, also cap `keys_and_challenges.len()` before `Vec::with_capacity(2*len+1)` and `multiexp_vartime`.

### Proof of Concept
Conceptual PoC against a streaming reader (e.g. the libp2p-style codec in `coordinator/src/p2p.rs`, which does cap at `MAX_LIBP2P_REQRES_MESSAGE_SIZE` [5](#0-4) , but any reader without such a cap is affected):

```rust
use std::io;
use ciphersuite::Ristretto;
use schnorr::aggregate::SchnorrAggregate;

fn main() {
  // An infinite stream of bytes with a maximal declared element count.
  // The first 4 bytes are 0xFFFFFFFF (u32::MAX little-endian).
  let mut header = u32::MAX.to_le_bytes().to_vec();
  let mut stream = io::Cursor::new(header).chain(io::repeat(0x20u8)); // 0x20 bytes: valid-ish Ristretto encodings ~50% of the time; use 32-byte canonical identity encodings for a fully-accepted stream
  // This loops up to 4 billion times, pushing a decoded point each iteration.
  let _ = SchnorrAggregate::<Ristretto>::read(&mut stream); // never returns; RSS grows monotonically
}
```

Even with a finite buffer, `count = u32::MAX` against a ~128 GB-equivalent byte budget shows the asymmetry: each 32 bytes of attacker bandwidth buys one full point decompression plus a `Vec` push on the victim, and there is no code path that rejects the request early.

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

**File:** coordinator/src/tributary/transaction.rs (L283-297)
```rust
          let mut each_commitments_len = [0; 2];
          reader.read_exact(&mut each_commitments_len)?;
          let each_commitments_len = usize::from(u16::from_le_bytes(each_commitments_len));
          if (commitments_len * each_commitments_len) > TRANSACTION_SIZE_LIMIT {
            Err(io::Error::other(
              "commitments present in transaction exceeded transaction size limit",
            ))?;
          }
          let mut commitments = vec![vec![]; commitments_len];
          for commitments in &mut commitments {
            *commitments = vec![0; each_commitments_len];
            reader.read_exact(commitments)?;
          }
          commitments
        };
```

**File:** crypto/dleq/src/lib.rs (L308-315)
```rust
  pub fn read<R: Read>(r: &mut R, discrete_logs: usize) -> io::Result<MultiDLEqProof<G>> {
    let c = read_scalar(r)?;
    let mut s = vec![];
    for _ in 0 .. discrete_logs {
      s.push(read_scalar(r)?);
    }
    Ok(MultiDLEqProof { c, s })
  }
```

**File:** coordinator/src/p2p.rs (L261-272)
```rust
    let mut len = [0; 4];
    io.read_exact(&mut len).await?;
    let len = usize::try_from(u32::from_le_bytes(len)).expect("not at least a 32-bit platform?");
    if len > MAX_LIBP2P_REQRES_MESSAGE_SIZE {
      Err(io::Error::other("request length exceeded MAX_LIBP2P_REQRES_MESSAGE_SIZE"))?;
    }
    // This may be a non-trivial allocation easily causable
    // While we could chunk the read, meaning we only perform the allocation as bandwidth is used,
    // the max message size should be sufficiently sane
    let mut buf = vec![0; len];
    io.read_exact(&mut buf).await?;
    Ok(buf)
```
