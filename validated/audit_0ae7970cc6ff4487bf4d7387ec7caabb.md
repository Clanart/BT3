### Title
`ThresholdKeys::read`/`new` never verifies `secret_share` against the `verification_shares`/`interpolation` it is paired with, so crafted serialized keys yield a `group_key` for funds that can never be spent - (File: crypto/dkg/src/lib.rs)

### Summary
The reported vault inflation attack works because the contract derives a price entirely from attacker-influenced state (`totalShares`, force-fed balance) without checking the victim's actual position. The same shape exists in `ThresholdKeys`: `ThresholdKeys::new` computes `group_key` purely from the supplied `verification_shares` map and `interpolation` coefficients, and nothing — in `new`, `read`, or `view` — ever checks that `secret_share * G == verification_shares[i]`. An attacker who supplies the serialized `ThresholdKeys` bytes (a listed untrusted-input sink via `ThresholdKeys::read`) can therefore fully control the reported group key while the embedded secret share is unrelated to it.

### Finding Description
`ThresholdKeys::new` builds `group_key` by interpolating the first `t` entries of the caller/attacker-supplied `verification_shares` map: [1](#0-0) 

The only validations are `verification_shares.len() == n`, each participant index `<= n`, and that `Constant` interpolation is only used when `t == n` [2](#0-1) . There is no consistency check between `secret_share` and `verification_shares[params.i]`. `ThresholdKeys::read` then reconstructs keys from raw bytes — including an attacker-chosen `Interpolation::Constant` coefficient vector of length `n` — and passes them straight into `new` [3](#0-2) .

Analogous to the vault minting shares priced off an inflated balance, an attacker can craft bytes where:
- `interpolation` is `Constant([1, 0, 0, ..., c])` (any `t == n`), letting them set `group_key` to an arbitrary point via `c`, or
- `secret_share` is simply a scalar unrelated to `verification_shares[i]`.

The deserialized `ThresholdKeys` then reports `original_group_key()`/`group_key()` pointing at an address the embedded secret share cannot sign for. Downstream, `networks/bitcoin` derives a P2TR address from `keys.offset(..).group_key()` and `Scanner`/`SignableTransaction::multisig` match on that derived script_pubkey [4](#0-3) , so deposits to the reported address are received but unspendable — the produced "signature shares" interpolate to a secret different from the group key's discrete log.

### Impact Explanation
Funds sent to the address derived from a maliciously constructed `ThresholdKeys` serialization are permanently unspendable: the contained `secret_share` does not correspond to the interpolated `group_key`, so no FROST signing session over these keys can produce a valid signature for it. This satisfies "funds reported received that are not spendable". Because `read`/`serialize` round-trip silently and `view()`/`multisig()` only panic or produce invalid signatures at signing time, the defect is detected only after funds are committed.

### Likelihood Explanation
Reachability requires a victim (or an operator importing/backing up key material, or a peer distributing `ThresholdKeys` blobs) to deserialize attacker-controlled bytes via `ThresholdKeys::read`, then scan funds to the derived group key. That is within the enumerated untrusted-input surface, but it does require the victim to trust externally supplied key material rather than locally generated DKG output, which keeps the likelihood moderate. Severity Medium.

### Recommendation
In `ThresholdKeys::new` (and hence `read`), verify `C::generator() * *secret_share == verification_shares[&params.i]` and reject otherwise. Optionally also sanity-check `Constant` interpolation coefficients (e.g., non-degenerate combination consistent with `t == n` semantics) or restrict deserialization to `Lagrange`.

### Proof of Concept
```rust
// Attacker crafts serialized ThresholdKeys for Secp256k1:
//   t = n = 2, i = 1, Interpolation::Constant([0, 1])
//   secret_share = random s (attacker-chosen, unrelated)
//   verification_shares = {1: G*x1, 2: G*x2} arbitrary
// group_key = VS[1]*0 + VS[2]*1 = G*x2
let keys = ThresholdKeys::<Secp256k1>::read(&mut crafted_bytes).unwrap();
let addr = p2tr_script_buf(keys.group_key()).unwrap(); // address for x2
// Victim scans deposits to `addr` believing keys.control it.
// Signing: view() interpolates secret_share s, producing shares that sum to
// a scalar != x2 -> every signature fails; funds at `addr` are unspendable.
// No check anywhere in `read` -> `new` rejects this inconsistent share.
```

### Citations

**File:** crypto/dkg/src/lib.rs (L349-391)
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

**File:** crypto/dkg/src/lib.rs (L604-632)
```rust
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

**File:** networks/bitcoin/src/wallet/send.rs (L273-285)
```rust
  pub fn multisig(self, keys: &ThresholdKeys<Secp256k1>) -> Option<TransactionMachine> {
    let mut sigs = vec![];
    for i in 0 .. self.tx.input.len() {
      let offset = keys.clone().offset(self.offsets[i]);
      if p2tr_script_buf(offset.group_key())? != self.prevouts[i].script_pubkey {
        None?;
      }

      sigs.push(AlgorithmMachine::new(Schnorr::new(), keys.clone().offset(self.offsets[i])));
    }

    Some(TransactionMachine { tx: self, sigs })
  }
```
