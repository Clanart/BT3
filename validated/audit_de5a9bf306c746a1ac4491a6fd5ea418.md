### Title
Unauthenticated resource-exhaustion DoS via unbounded length-prefixed read in `SchnorrAggregate::read` - (File: crypto/schnorr/src/aggregate.rs)

### Summary
Analogous to the Gogs SSH handshake stall (unbounded work driven by an unauthenticated peer with no enforced bound/timeout), `SchnorrAggregate::read` trusts an attacker-controlled `u32` count and then performs that many sequential `read_G` calls against a generic `io::Read`. There is no upper bound on the count and no chunked/bounded-read mitigation, so an attacker supplying untrusted bytes over a streaming reader can force unbounded memory growth and/or indefinitely stall the deserializing worker — the same asymmetric DoS shape as GHSA-xp79-5mx3-jx52.

### Finding Description
`SchnorrAggregate::read` reads a little-endian `u32` `len`, then loops `for _ in 0 .. u32::from_le_bytes(len)` pushing `C::read_G(reader)?` results into `Rs`, followed by `C::read_F` [1](#0-0) . Two compounding issues:

1. **No bound on `len`**: up to ~4.29 billion `read_G` iterations are requested purely from a 4-byte attacker-controlled prefix. Contrast with sibling Serai code that explicitly guards against this pattern — `Call::read` in `networks/ethereum/src/machine.rs` reads claimed data in 1 KB chunks precisely because "a valid DoS would be to claim a 4 GB data is present for only 4 bytes" [2](#0-1) , and tributary's `Transaction::read` enforces `TRANSACTION_SIZE_LIMIT` on attacker-controlled counts [3](#0-2) . `SchnorrAggregate::read` applies no equivalent check.
2. **Blocking reads (the Gogs analog)**: each `read_G` performs `read_exact` on the caller-provided reader. When the reader is a network-backed stream, an attacker who has sent the 4-byte header can trickle (or withhold) point encodings, keeping the deserializing task blocked inside `read_exact` indefinitely — a direct analog of withholding the SSH banner to pin a goroutine and file descriptor. Because `Rs` accumulates every successfully read point, a slow trickle also grows heap usage linearly for the entire duration of the attack, with zero bandwidth cost rate proportional only to attacker patience.

Other in-scope `read` paths do not share this exposure: `Commitments::read` is bounded by `params.t()` [4](#0-3) , `SecretShare::read` reads a fixed-size repr [5](#0-4) , and `ThresholdKeys::read` is bounded by a `u16` `n` [6](#0-5) . `SchnorrAggregate::read` is the only in-scope deserialization entry where a `u32` attacker count directly controls loop iterations and retained allocations.

### Impact Explanation
Any service that deserializes `SchnorrAggregate` from bytes supplied by an unauthenticated party (e.g., aggregate-signature submissions over a network transport) can be stalled indefinitely per connection and driven to unbounded memory consumption by declaring a large `len` and withholding or trickling the body. Repeated across connections this exhausts worker threads/memory, denying legitimate signature verification — the same service-neutralization impact as the reference advisory. This is a Medium-severity availability issue: it requires the integrator to parse attacker-controlled bytes (standard for a public signing/verification API), but it is a denial of service only, not secret leakage or forgery.

### Likelihood Explanation
The vulnerable code path is trivially reachable: `SchnorrAggregate::read` is `pub` and the attacker's input requirement is only a 4-byte length prefix followed by an arbitrarily slow byte stream — identical effort to the reference PoC (connect, send minimal bytes, stall). Exploitation depends on the reader being a streaming/blocking source rather than an already-buffered slice; for in-memory `&[u8]` readers the failure is fast (EOF), but any socket/channel-backed `io::Read` exposes the stall, matching the Gogs scenario where a transport-level caller passed an unbounded-blocking connection into the parser.

### Recommendation
Bound the aggregate size before iterating: either take an expected/maximum signature count as a parameter (like `ReadWrite::read(reader, params)` does with `ThresholdParams` in `crypto/dkg/pedpop/src/lib.rs`) or enforce a hard cap (e.g., reject `len` above a sane maximum such as the validator-set size). Additionally, pre-check that the declared length is consistent with the remaining input when the reader exposes it, and document that `SchnorrAggregate::read` must not be called on unbounded blocking readers without an enclosing timeout — mirroring the upstream fix pattern of enforcing limits/deadlines at the point untrusted input is consumed.

### Proof of Concept
```rust
// Demonstrates the unbounded-length read against a trickling reader.
use std::io::{self, Read};
use ciphersuite::Ciphersuite;
use ciphersuite_ed25519::Ed25519;
use schnorr::SchnorrAggregate;

// A reader that delivers 4 bytes (the count) then stalls forever,
// never returning data nor erroring -- like a peer withholding the
// SSH banner in the reference advisory.
struct Stall;
impl Read for Stall {
    fn read(&mut self, _buf: &mut [u8]) -> io::Result<usize> {
        std::thread::park(); // never yields a byte
    }
}

fn attack() {
    // Attacker-controlled length prefix: u32::MAX
    let mut chained = io::Cursor::new(u32::MAX.to_le_bytes()).chain(Stall);
    // Blocks forever inside the first read_G -> read_exact, while the
    // loop bound promises ~4.29 billion iterations of retained points.
    let _ = SchnorrAggregate::<Ed25519>::read(&mut chained);
}
```

A variant using a dribbling reader (`read` returns a few bytes per call at arbitrary intervals) shows the `Rs` vector growing without bound for the full attack duration while the worker remains occupied — requiring only that the attacker sustain the connection at near-zero bandwidth, exactly the asymmetric-cost property exploited in GHSA-xp79-5mx3-jx52.

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

**File:** networks/ethereum/src/machine.rs (L51-60)
```rust
    // A valid DoS would be to claim a 4 GB data is present for only 4 bytes
    // We read this in 1 KB chunks to only read data actually present (with a max DoS of 1 KB)
    let mut data = vec![];
    while data_len > 0 {
      let chunk_len = data_len.min(1024);
      let mut chunk = vec![0; chunk_len];
      reader.read_exact(&mut chunk)?;
      data.extend(&chunk);
      data_len -= chunk_len;
    }
```

**File:** coordinator/src/tributary/transaction.rs (L283-296)
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
```

**File:** crypto/dkg/pedpop/src/lib.rs (L110-128)
```rust
  fn read<R: Read>(reader: &mut R, params: ThresholdParams) -> io::Result<Self> {
    let mut commitments = Vec::with_capacity(params.t().into());
    let mut cached_msg = vec![];

    #[allow(non_snake_case)]
    let mut read_G = || -> io::Result<C::G> {
      let mut buf = <C::G as GroupEncoding>::Repr::default();
      reader.read_exact(buf.as_mut())?;
      let point = C::read_G(&mut buf.as_ref())?;
      cached_msg.extend(buf.as_ref());
      Ok(point)
    };

    for _ in 0 .. params.t() {
      commitments.push(read_G()?);
    }

    Ok(Commitments { commitments, cached_msg, sig: SchnorrSignature::read(reader)? })
  }
```

**File:** crypto/dkg/pedpop/src/lib.rs (L264-268)
```rust
  fn read<R: Read>(reader: &mut R, _: ThresholdParams) -> io::Result<Self> {
    let mut repr = F::Repr::default();
    reader.read_exact(repr.as_mut())?;
    Ok(SecretShare(repr))
  }
```

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
