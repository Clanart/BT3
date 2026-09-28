### Title
Unbounded attacker-controlled allocation in `ThresholdKeys::read` enables memory exhaustion - (File: crypto/dkg/src/lib.rs)

### Summary
Analogous to the httpd `max_body_size` report (a declared size that is trusted without enforcement before the body is buffered), `ThresholdKeys::<C>::read` trusts an attacker-supplied participant count `n` and immediately allocates a `Vec` of `n` field elements — and subsequently loops `n` times reading scalars and group elements — before `ThresholdParams::new` / `ThresholdKeys::new` get any chance to reject the parameters. `n` is a raw `u16` read straight off the wire, so a handful of bytes causes an allocation orders of magnitude larger than the input.

### Finding Description
In `ThresholdKeys::read` (`crypto/dkg/src/lib.rs`), after the curve-ID check, the reader pulls three `u16`s `(t, n, i)` directly from the untrusted stream (lines 591–602). The next byte selects the interpolation mode; for `Interpolation::Constant` it executes:

```rust
let mut res = Vec::with_capacity(usize::from(n));
for _ in 0 .. n {
  res.push(C::read_F(reader)?);
}
```

(lines 604–613), followed by `n` `read_G` calls for `verification_shares` (lines 620–623). Only afterward are the parameters validated inside `ThresholdParams::new(t, n, i)` / `ThresholdKeys::new` (lines 625–631). There is no upper bound on `n` at the point of allocation — `n` can be `0xffff`, so `Vec::with_capacity` reserves space for ~65,535 field elements (~2 MB) the moment the interpolation tag byte is read, even if the stream then ends and `read_F` fails. The reported bug pattern — a declared length trusted before content arrives and before limits are enforced — is reproduced exactly: the "limit" (`t <= n`, participant bounds) is checked only after the allocation.

This contrasts with other deserializers in the wider codebase that explicitly guard against this pattern, e.g. `RrCodec::read_request` in `coordinator/src/p2p.rs` (lines 261–271) checks `len > MAX_LIBP2P_REQRES_MESSAGE_SIZE` before `vec![0; len]`, and `Call::read` in `networks/ethereum/src/machine.rs` (lines 51–60) streams a claimed 4 GB `data_len` in 1 KB chunks precisely to avoid this DoS — demonstrating the project recognizes the class but does not apply it here.

### Impact Explanation
`ThresholdKeys::read` is an explicitly enumerated untrusted-input sink. An unprivileged party that can cause a node/integrator to deserialize attacker-controlled bytes as `ThresholdKeys` (key-import, recovery, or any API feeding this `read`) can trigger a ~2 MB allocation plus `n` deserializing reads per call from roughly ~30 bytes of input — an amplification factor of ~10⁵ on allocation-per-byte. Repeated invocations or concurrent streams exhaust memory and terminate the process (availability loss), matching the Medium/VA:H profile of the source advisory. If the attacker supplies the full ~4 MB body, they additionally burn CPU on 65,535 point decodings.

### Likelihood Explanation
No secret recovery or forgery is required — only that hostile bytes reach `ThresholdKeys::read`. The function performs the allocation unconditionally and early (before any validity check on `t`/`n`/`i`, before any cryptographic operation), so the trigger is deterministic: `u16::MAX` in the `n` position plus interpolation tag `0` is sufficient to force the maximum allocation regardless of what follows. The only mitigating factor is the `u16` cap, bounding each call to a few MB rather than truly unbounded — hence Medium rather than High.

### Recommendation
Validate bounds before allocating: read `t`, `n`, `i`, construct `ThresholdParams::new(t, n, i)` first (rejecting `t > n`, `n == 0`, `i > n`), and/or cap `n` at a protocol-meaningful maximum before `Vec::with_capacity`. Prefer `Vec::new()` with incremental `push`es (allocating only as bytes are actually consumed, as `Call::read` in `networks/ethereum/src/machine.rs` does) over trusting the declared count for the reservation.

### Proof of Concept
```rust
use ciphersuite::Ciphersuite;
use frost::curve::Secp256k1;
use frost::ThresholdKeys;
use std::io;

fn main() {
  // Attacker-supplied bytes: valid curve ID, then t=1, n=0xffff, i=1,
  // interpolation = Constant(0), then EOF.
  let mut bytes = vec![];
  bytes.extend(u32::try_from(Secp256k1::ID.len()).unwrap().to_le_bytes());
  bytes.extend(Secp256k1::ID);
  bytes.extend(1u16.to_le_bytes());      // t
  bytes.extend(u16::MAX.to_le_bytes());  // n = 65535 -> drives with_capacity
  bytes.extend(1u16.to_le_bytes());      // i
  bytes.push(0);                         // Interpolation::Constant
  // No further bytes needed: Vec::with_capacity(65535) already allocated
  // ~2 MB before read_F hits EOF.
  let _ = ThresholdKeys::<Secp256k1>::read::<&[u8]>(&mut bytes.as_ref());
}
```

**Uncertainty note:** the strongest in-scope analog found is this allocation-before-validation in `ThresholdKeys::read`. Other in-scope deserializers I checked (`Commitments::read` in pedpop sized by trusted `params.t()`, `DLEqProof::read`/`SchnorrSignature::read`/`EncryptedMessage::read` fixed-size, `read_preprocess` sized by the local algorithm's nonce list, `ReceivedOutput::read` bounded by rust-bitcoin's consensus caps) derive their read counts from trusted parameters rather than attacker-controlled length fields, so they do not exhibit this bug class.