### Title
Missing integrity binding in `ThresholdKeys` deserialization lets untrusted bytes install an attacker-controlled group key and share - (File: crypto/dkg/src/lib.rs)

### Summary
CVE-2018-16048 is a missing authorization control: an operation restricted to authorized parties could be invoked by anyone because the check binding the caller to the resource was absent. The analog in Serai is a missing *integrity/authorization binding* in `ThresholdKeys::new` / `ThresholdKeys::read`: the constructor validates the count, participant range, and interpolation method of the provided `verification_shares`, but never checks that `secret_share` is consistent with `verification_shares[params.i()]` (i.e., that `C::generator() * secret_share == verification_shares[i]`). Because `group_key` is computed purely from the attacker-supplied verification shares, bytes fed to `ThresholdKeys::read` (explicitly listed as untrusted input) fully determine both the identity of the key (`group_key`) and the signing scalar, with no proof that the share belongs to that key.

### Finding Description
`ThresholdKeys::new` performs three validations: `verification_shares.len() == n`, every participant index `<= n`, and `Constant` interpolation only when `t == n` [1](#0-0) . It then derives `group_key` by interpolating the verification shares of participants `1..=t` and stores the caller-supplied `secret_share` verbatim [2](#0-1) . There is no check that `C::generator() * secret_share == verification_shares[&params.i()]`.

`ThresholdKeys::read` reconstructs `ThresholdKeys` entirely from a byte stream — curve ID, `t`, `n`, `i`, interpolation coefficients, `secret_share`, and all `n` verification shares — and passes them straight to `ThresholdKeys::new` [3](#0-2) . Any party able to feed these bytes (a replaced key file, a forged serialized blob) can therefore install a `ThresholdKeys` where:

- `verification_shares` encode a polynomial whose intercept `a_0` is known to the attacker, making `group_key = G * a_0` a key the attacker fully controls, and
- `secret_share` is any value the attacker chooses (it does not even need to lie on that polynomial for `Lagrange` interpolation, since nothing verifies it).

The downstream FROST path then trusts `view()`/`group_key()` from this object: `AlgorithmSignMachine::sign` calls `self.params.keys.view(included)` and binds `self.params.keys.group_key()` into the `rho` transcript [4](#0-3) , so signatures are produced under, and verified against, the attacker-substituted `group_key`.

### Impact Explanation
The holder of deserialized `ThresholdKeys` will produce valid FROST signature shares for a group key chosen entirely by the supplier of the bytes. If the attacker sets the secret share consistent with the forged verification shares, every `view()`/`interpolation_factor` computation is self-consistent and `SignatureShare`s verify against the forged `group_key` — the victim signs messages under a key whose discrete log the attacker knows, and the attacker can also recover/forge signatures unilaterally. This is the same failure shape as the CVE: a missing authorization/consistency check lets an unauthorized input substitute the authoritative identity (here, the group key) under which privileged operations (threshold signing) execute. Concrete accepted impact: signing of unintended messages under an attacker-controlled group key identity.

### Likelihood Explanation
Exploitation requires the attacker to supply the serialized `ThresholdKeys` bytes to the victim (storage tampering, a malicious restore payload, or any path where `ThresholdKeys::read` consumes unauthenticated data). No cryptographic break, collusion, or malformed curve points are needed — `read_F`/`read_G` enforce canonical encodings, and the forged shares are perfectly well-formed scalars/points. The only barrier is access to the byte stream, making this Medium severity rather than High: it requires an input-injection position rather than pure passive network access.

### Recommendation
In `ThresholdKeys::new` (and thereby `ThresholdKeys::read`), require `C::generator() * secret_share == verification_shares[&params.i()]` and return an error otherwise. Optionally also reject identity/`C::G::identity()` verification shares. This binds the secret share to the declared public key material, restoring the missing authorization check: a party can only instantiate `ThresholdKeys` for a `group_key` they actually hold a share of.

### Proof of Concept
```rust
// Untrusted byte supplier constructs ThresholdKeys for a key IT controls.
let n = 5u16; let t = 3u16; let i = Participant::new(1).unwrap();

// Attacker picks a secret polynomial they know: a0 = secret
let a0 = <C as Ciphersuite>::F::random(rng);           // attacker-known group secret
let mut shares_poly = HashMap::new();
for l in 1 ..= n {
    // evaluation of a known polynomial f(l); only correctness vs. group_key matters
    let f_l = a0 + <C>::F::from(u64::from(l));          // deg-1 example
    shares_poly.insert(Participant::new(l).unwrap(), C::generator() * f_l);
}

// Malicious serialization: secret_share need NOT equal f(i); nothing checks it.
let forged_secret = <C>::F::random(rng);
let keys = ThresholdKeys::new(
    ThresholdParams::new(t, n, i).unwrap(),
    Interpolation::Lagrange,
    Zeroizing::new(forged_secret),     // not bound to shares_poly[&i]
    shares_poly,                       // encodes attacker's known a0
).unwrap();                            // succeeds — no share/public binding check

// keys.group_key() == G * a0: a key the attacker fully controls.
// AlgorithmMachine::new(alg, keys).sign(...) produces signature shares
// bound to attacker-chosen group_key via the rho transcript (frost/sign.rs:363).
```
The same result is reached purely through `ThresholdKeys::read` on attacker-controlled bytes, since `read` delegates all validation to `ThresholdKeys::new` [5](#0-4) .

### Citations

**File:** crypto/dkg/src/lib.rs (L355-374)
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
```

**File:** crypto/dkg/src/lib.rs (L376-390)
```rust
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

**File:** crypto/frost/src/sign.rs (L312-364)
```rust
    let view = self.params.keys.view(included.clone()).unwrap();
    validate_map(&preprocesses, &included, multisig_params.i())?;

    {
      // Domain separate FROST
      self.params.algorithm.transcript().domain_separate(b"FROST");
    }

    let nonces = self.params.algorithm.nonces();
    #[allow(non_snake_case)]
    let mut B = BindingFactor(HashMap::<Participant, _>::with_capacity(included.len()));
    {
      // Parse the preprocesses
      for l in &included {
        {
          self
            .params
            .algorithm
            .transcript()
            .append_message(b"participant", C::F::from(u64::from(u16::from(*l))).to_repr());
        }

        if *l == self.params.keys.params().i() {
          let commitments = self.preprocess.commitments.clone();
          commitments.transcript(self.params.algorithm.transcript());

          let addendum = self.preprocess.addendum.clone();
          {
            let mut buf = vec![];
            addendum.write(&mut buf).unwrap();
            self.params.algorithm.transcript().append_message(b"addendum", buf);
          }

          B.insert(*l, commitments);
          self.params.algorithm.process_addendum(&view, *l, addendum)?;
        } else {
          let preprocess = preprocesses.remove(l).unwrap();
          preprocess.commitments.transcript(self.params.algorithm.transcript());
          {
            let mut buf = vec![];
            preprocess.addendum.write(&mut buf).unwrap();
            self.params.algorithm.transcript().append_message(b"addendum", buf);
          }

          B.insert(*l, preprocess.commitments);
          self.params.algorithm.process_addendum(&view, *l, preprocess.addendum)?;
        }
      }

      // Re-format into the FROST-expected rho transcript
      let mut rho_transcript = A::Transcript::new(b"FROST_rho");
      rho_transcript.append_message(b"group_key", self.params.keys.group_key().to_bytes());
      rho_transcript.append_message(b"message", C::hash_msg(msg));
```
