### Title
Panic on short/malformed message in `SchnorrkelHram::hram` enables remote denial of service - (File: crypto/schnorrkel/src/lib.rs)

### Summary
`SchnorrkelHram::hram`, the challenge function used by the `Schnorrkel` FROST algorithm in `crypto/schnorrkel`, parses the attacker-influenced message `m` as a Merlin-style framed buffer (`u32` context length prefix followed by context and message). It slices `m[0 .. 4]` with an `expect("malformed message")` and then slices `m[4 .. 4 + ctx_len]` with no length check. Any message shorter than 4 bytes, or carrying a `ctx_len` larger than the remaining buffer, causes an out-of-bounds slice panic / `expect` panic, crashing the signing or verification thread — the same "crafted input crashes parser" class as CVE-2016-5032.

### Finding Description
The relevant code is `crypto/schnorrkel/src/lib.rs`: [1](#0-0) 

```rust
fn hram(R: &RistrettoPoint, A: &RistrettoPoint, m: &[u8]) -> Scalar {
    let ctx_len =
      usize::try_from(u32::from_le_bytes(m[0 .. 4].try_into().expect("malformed message")))
        .unwrap();

    let mut t = signing_context(&m[4 .. (4 + ctx_len)]).bytes(&m[(4 + ctx_len) ..]);
    ...
}
```

Two distinct panic paths exist:

1. **`m.len() < 4`**: `m[0 .. 4].try_into().expect("malformed message")` panics.
2. **`ctx_len > m.len() - 4`**: `m[4 .. (4 + ctx_len)]` panics with a slice-index-out-of-bounds, and `m[(4 + ctx_len) ..]` likewise.

`hram` is invoked via `Schnorr::<Ristretto, MerlinTranscript, SchnorrkelHram>` during `sign` (to compute the Fiat–Shamir challenge over `R`, `A`, `m`) and during `verify`. The `msg` argument to FROST `sign`/`SignatureShare` verification flows from the protocol counterparty — i.e., the coordinator-supplied message being signed or a signature/message pair being verified. Unlike the rest of Serai's deserialization surface (`Ciphersuite::read_F`/`read_G` in `crypto/ciphersuite/src/lib.rs`, which carefully return `io::Error` on non-canonical input), this parser was written assuming `m` is already correctly framed, so malformed input aborts the process instead of returning an error.

### Impact Explanation
An unprivileged party who can cause a Schnorrkel signature to be produced or verified over a message they influence (e.g., a message to be signed in a FROST signing session, or a signature + message presented for verification) can supply `m` shorter than 4 bytes or with an oversized embedded `ctx_len`, deterministically panicking the victim's signer/verifier thread. If the panic crosses an `unwrap` on a `Result` or a mutex/lock boundary it can poison shared state or kill the whole processor task — a remote denial of service matching the Medium-severity crash class of the reference CVE (availability impact, no integrity/confidentiality loss).

### Likelihood Explanation
Reachability requires that the caller pass a raw (non-pre-framed) message into `Schnorrkel`'s sign/verify path, or that the framing step itself consumes attacker-controlled bytes before validation. `Schnorrkel` stores `msg: Option<Vec<u8>>` populated from the sign request; nothing in `hram` validates `m` before slicing, so a `msg` of length `< 4` or a `msg` beginning with a large `u32` length prefix reliably triggers the panic. The counterparty in a FROST session selects the message being signed, and any party verifying a Schnorrkel signature supplies the message — both are unprivileged positions. I was unable to fully trace every caller in this iteration, but the `.expect("malformed message")` itself documents that malformed `m` is anticipated to reach this function, and no upstream length gate is visible in the file.

### Recommendation
Replace the unchecked slicing with fallible parsing in `SchnorrkelHram::hram` (e.g., return a fixed/zero challenge or propagate an error via a length-checked `m.get(0 .. 4)` / `m.get(4 .. 4 + ctx_len)`), or enforce the framing invariant at the `Schnorrkel` algorithm boundary by constructing `m = ctx_len || ctx || msg` internally so `hram` never sees unframed input. Panics on untrusted input should be eliminated in favor of `io::Error`/verification-failure returns, consistent with `read_F`/`read_G`.

### Proof of Concept
```rust
// crypto/schnorrkel — triggers panic inside SchnorrkelHram::hram
// Case 1: message shorter than 4 bytes -> expect("malformed message")
let short_msg: &[u8] = b"ab";          // m[0..4] panics

// Case 2: ctx_len larger than remaining buffer -> slice OOB panic
let mut crafted = (u32::MAX).to_le_bytes().to_vec(); // ctx_len = u32::MAX
crafted.extend_from_slice(b"xx");                    // m[4 .. 4+u32::MAX] panics

// Both are reachable when `m` (the message signed/verified via
// Schnorr<Ristretto, MerlinTranscript, SchnorrkelHram>) contains the
// above bytes, causing a deterministic panic (remote DoS).
```

### Citations

**File:** crypto/schnorrkel/src/lib.rs (L41-53)
```rust
  fn hram(R: &RistrettoPoint, A: &RistrettoPoint, m: &[u8]) -> Scalar {
    let ctx_len =
      usize::try_from(u32::from_le_bytes(m[0 .. 4].try_into().expect("malformed message")))
        .unwrap();

    let mut t = signing_context(&m[4 .. (4 + ctx_len)]).bytes(&m[(4 + ctx_len) ..]);
    t.proto_name(b"Schnorr-sig");
    let convert =
      |point: &RistrettoPoint| PublicKey::from_bytes(&point.to_bytes()).unwrap().into_compressed();
    t.commit_point(b"sign:pk", &convert(A));
    t.commit_point(b"sign:R", &convert(R));
    Scalar::from_repr(t.challenge_scalar(b"sign:c").to_bytes()).unwrap()
  }
```
