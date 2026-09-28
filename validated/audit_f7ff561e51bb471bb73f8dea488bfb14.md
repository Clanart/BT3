### Title
Missing identity (zero-key) check on registered MuSig participant keys allows a fully attacker-controlled group key - (File: crypto/dkg/musig/src/lib.rs)

### Summary
The external report describes a missing `!= 0` sanity check when registering an address, letting a zero value be stored and later used as if it were a valid counterparty. The direct analog in Serai is `check_keys` in `crypto/dkg/musig/src/lib.rs`: it validates that the key list is non-empty, fits in `u16`, and has no duplicate encodings — but never rejects the identity (zero) group element. Since the identity is a canonically encodable point, an unprivileged participant can register `G::identity()` as their public key, and `musig`/`musig_key`/`musig_key_vartime` will aggregate it without complaint.

### Finding Description
`check_keys` performs exactly three checks — emptiness, length, and duplicate `to_bytes()` — and returns the key count [1](#0-0) . The aggregate key is then computed as `Σ binding_factor_i * key_i` via `musig_key_multiexp` [2](#0-1) , and `musig` builds `ThresholdKeys` over this aggregate with `Interpolation::Constant(binding_factors)` [3](#0-2) .

If one registered key is the identity point, its term contributes `binding_factor * 0 = 0` to the sum. An attacker who registers their own key `K = k·G` in one slot and the identity in another slot produces a group key `a·K` whose discrete logarithm `a·k mod l` the attacker fully knows (`a` is the public binding factor). Note that `ThresholdKeys::new` also performs no identity check on `verification_shares` — it only checks the count and that indexes are `<= n` [4](#0-3) , then derives `group_key` by summing shares for participants `1..=t` [5](#0-4) . Contrast with `Curve::read_G`, which explicitly rejects identity for FROST-typed points [6](#0-5)  — that defense does not extend to `musig`, which accepts keys as already-deserialized `C::G` values.

### Impact Explanation
The resulting n-of-n multisig key has a discrete log known to the attacker. Any Schnorr/FROST-style verification against `group_key()` can be satisfied by a signature the attacker produces alone under scalar `a·k`, making every other participant's cooperation unnecessary — a forgery of the multisig. Downstream, if this aggregate key is registered as a wallet/scanner key (e.g., `register_key` stores arbitrary `G` and scans for outputs paying to it [7](#0-6) ), funds sent to the "multisig" are spendable solely by the attacker. This is the same shape as the UXD bug: a zero value is stored at registration and later treated as a real key, so assets under it are not protected by the intended party set.

### Likelihood Explanation
Exploitation requires the attacker to occupy a slot in the `keys` list supplied to `musig`/`musig_key` — i.e., to register an identity public key wherever participant keys are registered (e.g., the coordinator path passing `participants` into `musig` [8](#0-7) ). Identity is a canonical encoding, so nothing at the serialization or aggregation layer stops it. The attacker's own key must also be in the list for `musig` (or anyone can call `musig_key` to compute the compromised aggregate), which is the normal registration flow. This is a public-input, unprivileged reachability path; the only uncertainty is whether an upstream registry independently rejects identity keys — the crypto layer itself does not.

### Recommendation
Add an explicit identity rejection in `check_keys` (or at `musig`/`musig_key` entry): `if bool::from(key.is_identity()) { Err(MusigError::InvalidKey) }`. For defense in depth, `ThresholdKeys::new` should also reject identity verification shares, matching the identity rejection already present in `Curve::read_G`.

### Proof of Concept
```rust
// crypto/dkg/musig/src/lib.rs path, over dalek_ff_group::Ristretto
use zeroize::Zeroizing;
use ciphersuite::{group::ff::Field, Ciphersuite};
use dalek_ff_group::Ristretto;

const CONTEXT: [u8; 32] = [0u8; 32];

let k = Zeroizing::new(<Ristretto as Ciphersuite>::F::random(&mut OsRng));
let pub_k = <Ristretto as Ciphersuite>::generator() * *k;

// Attacker registers their own key AND the identity point as a second "participant"
let keys = vec![pub_k, <Ristretto as Ciphersuite>::G::identity()];

// Accepted: no identity check in check_keys
let group_key = musig_key::<Ristretto>(CONTEXT, &keys).unwrap();
let tk = musig::<Ristretto>(CONTEXT, k.clone(), &keys).unwrap();
assert_eq!(tk.group_key(), group_key);

// group_key = a1 * pub_k + a2 * identity = a1 * k * G
// a1 is publicly recomputable via binding_factor(transcript, 1)
// => attacker knows dlog(group_key) = a1 * k and can forge signatures
//    for the "multisig" without any other participant.
```
`check_keys` accepts the identity key (it only fails on empty/oversized/duplicated lists), `musig` returns `Ok`, and the aggregate reduces to a scalar multiple of the attacker's own key.

### Citations

**File:** crypto/dkg/musig/src/lib.rs (L46-63)
```rust
fn check_keys<C: Ciphersuite>(keys: &[C::G]) -> Result<u16, MusigError<C>> {
  if keys.is_empty() {
    Err(MusigError::NoKeysProvided)?;
  }

  let keys_len = u16::try_from(keys.len())
    .map_err(|_| MusigError::TooManyKeysProvided { max: u16::MAX, provided: keys.len() })?;

  let mut set = HashSet::with_capacity(keys.len());
  for key in keys {
    let bytes = key.to_bytes().as_ref().to_vec();
    if !set.insert(bytes) {
      Err(MusigError::DuplicatedParticipant(*key))?;
    }
  }

  Ok(keys_len)
}
```

**File:** crypto/dkg/musig/src/lib.rs (L87-98)
```rust
fn musig_key_multiexp<C: Ciphersuite>(
  context: [u8; 32],
  keys: &[C::G],
) -> Result<Vec<(C::F, C::G)>, MusigError<C>> {
  let keys_len = check_keys::<C>(keys)?;
  let transcript = binding_factor_transcript::<C>(context, keys_len, keys);
  let mut multiexp = Vec::with_capacity(keys.len());
  for i in 1 ..= keys_len {
    multiexp.push((binding_factor::<C>(transcript.clone(), i), keys[usize::from(i - 1)]));
  }
  Ok(multiexp)
}
```

**File:** crypto/dkg/musig/src/lib.rs (L143-161)
```rust
  for (i, key) in (1 ..= keys_len).zip(keys.iter().copied()) {
    let binding_factor = binding_factor::<C>(transcript.clone(), i);
    binding_factors.push(binding_factor);
    multiexp.push((binding_factor, key));

    let i = Participant::new(i).expect("non-zero u16 wasn't a valid Participant index?");
    verification_shares.insert(i, key);
  }
  let group_key = multiexp::multiexp(&multiexp);
  debug_assert_eq!(our_pub_key, verification_shares[&params.i()]);
  debug_assert_eq!(musig_key_vartime::<C>(context, keys), Ok(group_key));

  ThresholdKeys::new(
    params,
    Interpolation::Constant(binding_factors),
    private_key,
    verification_shares,
  )
  .map_err(MusigError::DkgError)
```

**File:** crypto/dkg/src/lib.rs (L355-365)
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
```

**File:** crypto/dkg/src/lib.rs (L376-378)
```rust
    let t = (1 ..= params.t()).map(Participant).collect::<Vec<_>>();
    let group_key =
      t.iter().map(|i| verification_shares[i] * interpolation.interpolation_factor(*i, &t)).sum();
```

**File:** crypto/frost/src/curve/mod.rs (L123-131)
```rust
  /// Read a point from a reader, rejecting identity.
  #[allow(non_snake_case)]
  fn read_G<R: Read>(reader: &mut R) -> io::Result<Self::G> {
    let res = <Self as Ciphersuite>::read_G(reader)?;
    if res.is_identity().into() {
      Err(io::Error::other("identity point"))?;
    }
    Ok(res)
  }
```

**File:** processor/src/multisigs/scanner.rs (L276-302)
```rust
  pub async fn register_key(
    &mut self,
    txn: &mut D::Transaction<'_>,
    activation_number: usize,
    key: <N::Curve as Ciphersuite>::G,
  ) {
    info!("Registering key {} in scanner at {activation_number}", hex::encode(key.to_bytes()));

    let mut scanner_lock = self.scanner.write().await;
    let scanner = scanner_lock.as_mut().unwrap();
    assert!(
      activation_number > scanner.ram_scanned.unwrap_or(0),
      "activation block of new keys was already scanned",
    );

    if scanner.keys.is_empty() {
      assert!(scanner.ram_scanned.is_none());
      scanner.ram_scanned = Some(activation_number);
      assert!(ScannerDb::<N, D>::save_scanned_block(txn, activation_number).is_empty());
    }

    ScannerDb::<N, D>::register_key(txn, activation_number, key);
    scanner.keys.push((activation_number, key));
    #[cfg(not(test))] // TODO: A test violates this. Improve the test with a better flow
    assert!(scanner.keys.len() <= 2);

    scanner.eventualities.insert(key.to_bytes().as_ref().to_vec(), EventualitiesTracker::new());
```

**File:** coordinator/src/tributary/signing_protocol.rs (L118-121)
```rust
    let keys: ThresholdKeys<Ristretto> =
      musig(musig_context(self.spec.set().into()), self.key.clone(), participants)
        .expect("signing for a set we aren't in/validator present multiple times")
        .into();
```
