### Title
`ThresholdKeys::read` / `ThresholdKeys::new` accept arbitrary verification shares with no consistency or identity check, producing an unusable group key - ([File: crypto/dkg/src/lib.rs])

### Summary
`ThresholdKeys::new` validates only the *count* and *index range* of `verification_shares`. It never checks that a share is non-identity, nor that `C::generator() * secret_share == verification_shares[params.i()]`, nor that the interpolated `group_key` is non-identity. `ThresholdKeys::read` feeds fully attacker-controlled bytes (from `C::read_G`, which only enforces canonical encoding — the identity and, for ed25519-family curves, torsion-component encodings are accepted) directly into `ThresholdKeys::new` via `ThresholdParams::new`, so any inconsistent-but-well-formed blob deserializes successfully.

### Finding Description
In `crypto/dkg/src/lib.rs`, `ThresholdKeys::new` performs only these checks:

```rust
if verification_shares.len() != usize::from(params.n()) { ... }
for participant in verification_shares.keys().copied() {
  if u16::from(participant) > params.n() { ... }
}
```

It then computes `group_key` as the sum of the first `t` interpolated verification shares and returns `Ok`. There is no check that `verification_shares[i]` equals `generator * secret_share`, that any share is non-identity, or that the resulting `group_key` is usable. Callers that generate keys internally (`dealer::key_gen`, `musig`) rely on `debug_assert_eq!` to check consistency — meaning no runtime check exists at all in release builds of `read`.

`ThresholdKeys::read` reconstructs the struct from untrusted bytes: `t`, `n`, `i`, interpolation coefficients, `secret_share` (via `read_F`, canonical-only), and `n` verification shares (via `read_G`, canonical-only; `Ciphersuite::read_G` in `crypto/ciphersuite/src/lib.rs:91` rejects non-canonical encodings but accepts the identity point and does not perform a subgroup check). Nothing binds the secret share to the declared verification shares or the derived group key.

This is the direct analog of `setGuardian` accepting a zero/invalid address: a critical value is stored without any validity check, yielding an object that appears functional but is unusable.

### Impact Explanation
A `ThresholdKeys` deserialized from crafted bytes yields a `group_key` that does not correspond to `secret_share` (e.g., arbitrary `verification_shares[1..=t]`, or identity shares collapsing the key). Downstream code treats this key as real: `networks/bitcoin/src/wallet/mod.rs` `tweak_keys`/`Scanner::new` derive a Taproot `script_pubkey` from `group_key`, and `Scanner::scan_transaction` reports outputs paying that script as `ReceivedOutput`s. Any funds sent to the derived address are permanently unspendable — no secret share corresponding to the derived key exists — and signing via `AlgorithmSignMachine::sign`/`complete` cannot produce a valid signature (shares verify against `verification_shares`, which are unrelated to `secret_share`). Funds are reported received yet are not spendable.

### Likelihood Explanation
Reachable whenever `ThresholdKeys::read` (or `ThresholdKeys::new` with externally influenced `verification_shares`) processes bytes an unprivileged party can supply — explicitly within the reachable input surface. The malformed blob is trivially constructible (canonical encodings of arbitrary points). Exploitation additionally requires a deployment that accepts key material over an untrusted channel rather than only locally generated DKG output; the defect is the missing validation itself, which makes the trust boundary decision implicit and silent rather than enforced.

### Recommendation
In `ThresholdKeys::new`, verify consistency between the secret share and verification shares, and reject degenerate material:

```rust
if self_key != verification_shares[&params.i()] {
  Err(DkgError::InvalidVerificationShare(params.i()))?;
}
if bool::from(group_key.is_identity()) {
  Err(DkgError::InvalidVerificationShare(...))?;
}
```

Specifically: check `C::generator() * secret_share == verification_shares[params.i()]` inside `ThresholdKeys::new` (covering both `read` and all generators), and reject identity verification shares / identity `group_key`. `Ciphersuite::read_G` should additionally reject identity and, for curves with cofactors, non-prime-order points.

### Proof of Concept
```rust
// C = Secp256k1 or any ciphersuite; reader supplies attacker bytes.
let mut bytes = vec![];
bytes.extend((C::ID.len() as u32).to_le_bytes());
bytes.extend(C::ID);
bytes.extend(1u16.to_le_bytes()); // t
bytes.extend(1u16.to_le_bytes()); // n
bytes.extend(1u16.to_le_bytes()); // i = Participant(1)
bytes.push(1u8);                  // Interpolation::Lagrange
bytes.extend(<C::F as Field>::random(&mut rng).to_repr()); // secret_share
// verification_shares[1] = identity — canonical encoding, accepted by read_G
bytes.extend(C::G::identity().to_bytes()); // or any arbitrary point

let keys = ThresholdKeys::<C>::read(&mut &bytes[..]).unwrap(); // Ok — no check
// keys.group_key() == identity / attacker-chosen point.
// Scanner::new(tweak_keys(keys).group_key()) yields a script_pubkey;
// deposits scanned as ReceivedOutput are unspendable since no matching
// secret share exists for the derived key.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** crypto/dkg/src/lib.rs (L349-379)
```rust
  pub fn new(
    params: ThresholdParams,
    interpolation: Interpolation<C::F>,
    secret_share: Zeroizing<C::F>,
    verification_shares: HashMap<Participant, C::G>,
  ) -> Result<ThresholdKeys<C>, DkgError> {
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

**File:** crypto/dkg/src/lib.rs (L618-631)
```rust
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
```

**File:** crypto/ciphersuite/src/lib.rs (L91-100)
```rust
  fn read_G<R: Read>(reader: &mut R) -> io::Result<Self::G> {
    let mut encoding = <Self::G as GroupEncoding>::Repr::default();
    reader.read_exact(encoding.as_mut())?;

    let point = Option::<Self::G>::from(Self::G::from_bytes(&encoding))
      .ok_or_else(|| io::Error::other("invalid point"))?;
    if point.to_bytes().as_ref() != encoding.as_ref() {
      Err(io::Error::other("non-canonical point"))?;
    }
    Ok(point)
```

**File:** networks/bitcoin/src/wallet/mod.rs (L199-214)
```rust
  pub fn scan_transaction(&self, tx: &Transaction) -> Vec<ReceivedOutput> {
    let mut res = Vec::new();
    for (vout, output) in tx.output.iter().enumerate() {
      // If the vout index exceeds 2**32, stop scanning outputs
      let Ok(vout) = u32::try_from(vout) else { break };

      if let Some(offset) = self.scripts.get(&output.script_pubkey) {
        res.push(ReceivedOutput {
          offset: *offset,
          output: output.clone(),
          outpoint: OutPoint::new(tx.compute_txid(), vout),
        });
      }
    }
    res
  }
```
