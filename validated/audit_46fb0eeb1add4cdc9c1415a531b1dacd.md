### Title
Attacker-controlled `ThresholdKeys` serialization binds an inconsistent secret share / verification share set, yielding a group key unrelated to the holder's share - (File: crypto/dkg/src/lib.rs)

### Summary
`ThresholdKeys::read` accepts fully attacker-controlled bytes — `t`, `n`, `i`, the interpolation method and coefficients, the secret share, and all `n` verification shares — then calls `ThresholdKeys::new`, which derives `group_key` solely by interpolating the first `t` verification shares. No check is ever made that `secret_share` is consistent with `verification_shares[i]` (i.e. `generator * secret_share == verification_shares[i]`), nor that the interpolated `group_key` corresponds to any secret the holder actually possesses. This is the Serai analog of the swig arbitrary-file-read class: untrusted bytes select which data gets "included" (the shares that define the group key), and the reader silently produces a key object whose public identity is decoupled from its private material.

### Finding Description
- `ThresholdKeys::read` reads `t`, `n`, `i` from the byte stream, then reads `n` scalars for `Interpolation::Constant` or accepts `Lagrange`, reads `secret_share`, then reads `n` verification-share points — all from attacker input [1](#0-0) 
- `ThresholdKeys::new` validates counts and index bounds, and `params.i() <= n` via `ThresholdParams::new`, but computes `group_key` as `sum(verification_shares[i] * interpolation_factor(i))` for `i in 1..=t` with zero consistency check against `secret_share` [2](#0-1) 
- In the honest PedPoP flow this invariant is enforced cryptographically: `calculate_share` batch-verifies each decrypted share against the sender's committed coefficients via `share_verification_statements` before constructing `ThresholdKeys` [3](#0-2) 
- The deserialization path bypasses all of this. A forged blob can specify `verification_shares` that interpolate to a group key whose discrete log is known to the attacker, while `secret_share` and `i` are chosen independently. The resulting `ThresholdKeys` is a fully-formed object: `group_key()` returns the attacker's key, `view()` interpolates normally, and `sign()` produces shares that will not verify — or, conversely, if the attacker wants the holder's real share to sign under a foreign group key, they can keep the real `secret_share` and swap only the `verification_shares`, causing the node to sign for a group key the attacker controls.

### Impact Explanation
Any component that accepts a serialized `ThresholdKeys` from a party (key import, key exchange, recovery/restore flows using `ThresholdKeys::read` / `dkg::recovery`) will derive the multisig's group key — the address deposits are sent to — entirely from attacker-chosen verification shares. Two concrete consequences:

1. Funds reported received that are not spendable by the intended holders: attacker submits verification shares interpolating to a key they control; the node reports `group_key()` = attacker's key while the holder's `secret_share` cannot produce valid signatures for it.
2. Silent share/group-key mismatch: because `read` never asserts `generator * secret_share == verification_shares[i]`, a deserialized key can appear healthy while every signature it produces is garbage, or worse, valid shares for a group key unrelated to the share holder's intended key.

### Likelihood Explanation
Reachable wherever `ThresholdKeys::read` (or `dkg::recover`'s read path) ingests bytes not produced by the local DKG — cross-node key distribution, backup restore, or coordinator-supplied key material — which is exactly the untrusted-byte-to-`ThresholdKeys::read` surface in scope. The attack requires only the ability to supply the serialized blob; no threshold collusion, no malicious participant in a real DKG, and no broken primitives are needed. The missing check is unconditional, not probabilistic.

### Recommendation
In `ThresholdKeys::new` (or at minimum in `ThresholdKeys::read`), enforce the consistency invariant the DKG itself guarantees: verify `C::generator() * secret_share == verification_shares[&params.i()]` and reject the blob otherwise. Optionally also commit to the group's public key inside the serialization (e.g., store the expected `group_key` and check the interpolated value matches), so that a swapped share set cannot silently redefine the group's identity.

### Proof of Concept
```rust
// Construct a malicious ThresholdKeys blob for any ciphersuite C.
// t = 1, n = 1, i = 1 with Lagrange interpolation.
let attacker_secret = C::F::random(&mut OsRng);
let attacker_point = C::generator() * attacker_secret;

let mut blob = vec![];
blob.extend(&(C::ID.len() as u32).to_le_bytes());
blob.extend(C::ID);
blob.extend(&1u16.to_le_bytes()); // t
blob.extend(&1u16.to_le_bytes()); // n
blob.extend(&1u16.to_le_bytes()); // i
blob.push(1u8);                   // Interpolation::Lagrange
blob.extend(C::F::ZERO.to_repr().as_ref()); // holder "secret share" = 0
blob.extend(attacker_point.to_bytes().as_ref()); // verification_shares[1]

let keys = ThresholdKeys::<C>::read(&mut blob.as_slice()).unwrap();
// group_key interpolates verification_shares[1] with factor 1 => attacker_point.
assert_eq!(keys.group_key(), attacker_point);
// generator * secret_share (0) != verification_shares[1]; never checked.
// Any deposits to keys.group_key() are controlled by `attacker_secret`,
// while the holder's share can never produce a valid signature for it.
```

Note on confidence: I verified the read/new paths and the absence of a share-consistency check, but did not exhaustively trace every caller of `ThresholdKeys::read` in this snapshot; the finding assumes the reachable "untrusted bytes to `ThresholdKeys::read`" surface stipulated in scope.

### Citations

**File:** crypto/dkg/src/lib.rs (L355-378)
```rust
    if verification_shares.len() != usize::from(params.n()) {
      Err(DkgError::IncorrectAmountOfVerificationShares {
        n: params.n(),
        shares: verification_shares.len(),
      })?;
    }
    for participant in verification_shares.keys().copied() {
      if u16::from(participant) > params.n() {
        Err(DkgError::InvalidParticipant { n: params.n(), participant })?;
      }
    }

    match &interpolation {
      Interpolation::Constant(_) => {
        if params.t() != params.n() {
          Err(DkgError::InapplicableInterpolation("constant interpolation for keys where t != n"))?;
        }
      }
      Interpolation::Lagrange => {}
    }

    let t = (1 ..= params.t()).map(Participant).collect::<Vec<_>>();
    let group_key =
      t.iter().map(|i| verification_shares[i] * interpolation.interpolation_factor(*i, &t)).sum();
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

**File:** crypto/dkg/pedpop/src/lib.rs (L487-491)
```rust
      batch.queue(
        rng,
        BatchId::Share(l),
        share_verification_statements::<C>(self.params.i(), &self.commitments[&l], share),
      );
```
