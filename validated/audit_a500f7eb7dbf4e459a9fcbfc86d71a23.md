### Title
Deserialized `ThresholdKeys` are not validated against their declared participant/verification shares — mismatched or degenerate key material is silently accepted - (File: crypto/dkg/src/lib.rs)

### Summary
The referenced advisory is a type-confusion bug: a deserializer accepts a reference to an object of the wrong type and silently assigns it because no `is_a`-style type guard runs on the loaded value. The analog in Serai is `ThresholdKeys::<C>::read` / `ThresholdKeys::new`: untrusted serialized bytes are parsed into a `secret_share` scalar and a `verification_shares` map, and the reconstructed key is accepted without ever checking that the loaded `secret_share` actually corresponds to the loaded verification share for `params.i()` — i.e., that `C::generator() * secret_share == verification_shares[&i]` — nor that the resulting `group_key` is non-degenerate. The deserialized relation (share ↔ participant index ↔ group key) is not "type-checked", so a byte string describing a different key than the one implied by its verification shares is silently instantiated.

### Finding Description
`ThresholdKeys::read` deserializes the ciphersuite ID, `t`, `n`, `i`, the interpolation, the `secret_share`, and `n` verification shares, then calls `ThresholdKeys::new` [1](#0-0) . `ThresholdKeys::new` validates only:

- the count of verification shares equals `n` [2](#0-1) 
- no participant index exceeds `n` [3](#0-2) 
- interpolation applicability [4](#0-3) 

It then derives `group_key` as the interpolation of shares `1..=t` [5](#0-4)  and returns `Ok` without verifying `C::generator() * secret_share == verification_shares[&params.i()]` and without rejecting an identity `group_key` [6](#0-5) .

Two exploitable misassignments follow:

1. **Share/index confusion.** A crafted serialization can pair `i` with a `secret_share` belonging to a different participant index (or to a different DKG entirely). The object is accepted; `view()` then interpolates this wrong share as if it were participant `i`'s [7](#0-6) . Every signature share the node produces fails the per-participant share verification in FROST `complete`, and in PedPoP-style usage `recover_key` reconstructs a secret that does not correspond to `group_key` — a silently corrupted key whose declared "type" (participant identity / verification share) does not match its contents, exactly the CWE-843 shape of the advisory.

2. **Degenerate group key.** Nothing checks that the `t` verification shares interpolate to a non-identity `group_key`. `read_G` accepts the identity point and there is no `group_key.is_identity()` rejection. A crafted key whose group key is the identity corresponds to private key `0` — outputs/keys derived from it are controlled by no valid share yet are presented as a valid threshold key.

This is reachable wherever serialized `ThresholdKeys` bytes cross a trust boundary (key import, recovery, or re-deserialization from data influenced by other participants — note `GeneratedKeysDb` stores concatenated serializations that are re-read on every restart [8](#0-7) ).

### Impact Explanation
- The node signs with a share that does not match its published verification share: its shares fail verification in `SignatureMachine::complete`, and the resulting `InvalidShare`/blame path attributes fault to the honest node — an unprivileged party supplying the malformed serialization converts a deserialization gap into integrity corruption and potential slashing of a non-faulty validator.
- An identity (or attacker-chosen) `group_key` yields a "threshold key" whose secret is known (`0`) or unrelated to any share: funds addressed to it are not spendable by the threshold set as intended.
- Per the validation criteria, this is an incorrect-deserialization integrity failure: bytes of one logical "type" (share for participant `j`, or a degenerate key) are accepted as another (share for participant `i` of the real group).

### Likelihood Explanation
Severity is Medium: exploitation requires the attacker to influence the serialized `ThresholdKeys` bytes consumed by a victim (e.g., a recovery/import path or any peer-supplied key blob), which is a narrower surface than a direct network message. However, once accepted, the corruption is silent — no check anywhere downstream catches it until signatures fail or the group key is used. The fix is identical in spirit to the advisory: add the missing guard at the point of construction.

### Recommendation
In `ThresholdKeys::new` (crypto/dkg/src/lib.rs), additionally:
- reject a `group_key` that is the identity point;
- verify `C::generator() * secret_share == verification_shares[&params.i()]` (and return `Err(DkgError)` on mismatch), which "type-checks" the share against its declared participant — the analogue of the `is_a` guard added upstream;
- optionally verify `verification_shares` entries are non-identity.

### Proof of Concept
```rust
// Attacker crafts serialized bytes for participant i=1 whose secret_share
// is actually the share for participant j=2 of a real DKG output.
let real_keys: ThresholdKeys<C> = honest_dkg_for(Participant::new(2).unwrap());
let mut buf = real_keys.serialize().to_vec();
// Rewrite the embedded participant index field from 2 -> 1
// (id_len || id || t || n || i layout: patch the u16 after n)
// ... splice bytes for i = 1 ...

// Vulnerable: accepted with no error
let confused = ThresholdKeys::<C>::read(&mut buf.as_slice()).unwrap();
assert_eq!(confused.params().i(), Participant::new(1).unwrap());
// But generator * secret_share != verification_shares[1]
assert_ne!(
  C::generator() * confused.original_secret_share().deref(),
  confused.original_verification_share(Participant::new(1).unwrap())
);
// Fixed version: read/new must return Err(DkgError) here.

// Degenerate case: pick verification shares s.t. interpolation over 1..=t
// sums to identity (e.g. G*a, -G*a for t=2,n=2); read() accepts and
// group_key() == C::G::identity() — a "key" whose private key is 0.
```

Caveat: the exploit path assumes serialized `ThresholdKeys` bytes are attacker-influenced in at least one integrator flow (import/recovery); within the in-repo usage the bytes primarily round-trip through `GeneratedKeysDb`, so the concrete deployment impact depends on callers honoring the documented sink. The missing guards themselves are confirmed in the cited code.

### Citations

**File:** crypto/dkg/src/lib.rs (L355-360)
```rust
    if verification_shares.len() != usize::from(params.n()) {
      Err(DkgError::IncorrectAmountOfVerificationShares {
        n: params.n(),
        shares: verification_shares.len(),
      })?;
    }
```

**File:** crypto/dkg/src/lib.rs (L361-365)
```rust
    for participant in verification_shares.keys().copied() {
      if u16::from(participant) > params.n() {
        Err(DkgError::InvalidParticipant { n: params.n(), participant })?;
      }
    }
```

**File:** crypto/dkg/src/lib.rs (L367-374)
```rust
    match &interpolation {
      Interpolation::Constant(_) => {
        if params.t() != params.n() {
          Err(DkgError::InapplicableInterpolation("constant interpolation for keys where t != n"))?;
        }
      }
      Interpolation::Lagrange => {}
    }
```

**File:** crypto/dkg/src/lib.rs (L376-378)
```rust
    let t = (1 ..= params.t()).map(Participant).collect::<Vec<_>>();
    let group_key =
      t.iter().map(|i| verification_shares[i] * interpolation.interpolation_factor(*i, &t)).sum();
```

**File:** crypto/dkg/src/lib.rs (L380-390)
```rust
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
```

**File:** crypto/dkg/src/lib.rs (L494-507)
```rust
    let secret_share_scaled = Zeroizing::new(self.scalar * self.original_secret_share().deref());
    let mut secret_share = Zeroizing::new(
      self.core.interpolation.interpolation_factor(self.params().i(), &included) *
        secret_share_scaled.deref(),
    );

    let mut verification_shares = HashMap::with_capacity(included.len());
    for i in &included {
      let verification_share = self.core.verification_shares[i];
      let verification_share = verification_share *
        self.scalar *
        self.core.interpolation.interpolation_factor(*i, &included);
      verification_shares.insert(*i, verification_share);
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

**File:** processor/src/key_gen.rs (L47-63)
```rust
  fn read_keys<N: Network>(
    getter: &impl Get,
    key: &[u8],
  ) -> Option<(Vec<u8>, (Vec<ThresholdKeys<Ristretto>>, Vec<ThresholdKeys<N::Curve>>))> {
    let keys_vec = getter.get(key)?;
    let mut keys_ref: &[u8] = keys_vec.as_ref();

    let mut substrate_keys = vec![];
    let mut network_keys = vec![];
    while !keys_ref.is_empty() {
      substrate_keys.push(ThresholdKeys::read(&mut keys_ref).unwrap());
      let mut these_network_keys = ThresholdKeys::read(&mut keys_ref).unwrap();
      N::tweak_keys(&mut these_network_keys);
      network_keys.push(these_network_keys);
    }
    Some((keys_vec, (substrate_keys, network_keys)))
  }
```
