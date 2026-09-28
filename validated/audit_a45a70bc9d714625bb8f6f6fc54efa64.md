### Title
`ThresholdKeys::read`/`ThresholdKeys::new` accept attacker-rewritten participant index, threshold params, and verification shares without checking share consistency, allowing override of a node's claimed identity and signatures under attacker-selected keys - ([File: crypto/dkg/src/lib.rs])

### Summary
CVE-2022-36129 describes an unauthenticated endpoint letting a joining Vault node override the voter status of an existing cluster member. The analog in Serai is `ThresholdKeys::read` / `ThresholdKeys::new` in `crypto/dkg/src/lib.rs`: serialized `ThresholdKeys` carry `(t, n, i)`, the interpolation method, the secret share, and the full map of per-participant verification shares, and are re-accepted with only range/count checks — no check that `secret_share` corresponds to `verification_shares[i]`, that the shares form a consistent polynomial, or that the claimed participant index `i` is the same one originally issued. An attacker who supplies these bytes can "re-vote" the holder into an arbitrary position/parameters, exactly the "redefine a member's status on join" bug class.

### Finding Description
`ThresholdKeys::new` performs only three validations: `verification_shares.len() == n`, every key index `<= n`, and `t == n` when using `Interpolation::Constant`. It then derives `group_key` purely from `verification_shares[1..=t]` and the interpolation factors. Nothing binds `params.i()` or `secret_share` to the verification-share map. [1](#0-0) 

`ThresholdKeys::read` deserializes all of `(t, n, i, interpolation, secret_share, verification_shares)` from attacker-controlled bytes and forwards them straight to `ThresholdKeys::new`. [2](#0-1) 

`view()` uses the (attacker-chosen) `params().i()` for `interpolation_factor` and offset assignment, and reconstructs interpolated verification shares from the attacker-chosen map — so the entire `ThresholdView` is self-consistent with whatever identity the attacker assigned. [3](#0-2) 

FROST `sign()`/`complete()` then verify shares against this attacker-defined view, so a tampered key set passes internal verification. [4](#0-3) 

### Impact Explanation
Because `verify_share` and `verify` only check consistency within the supplied view, an attacker can rewrite a victim's serialized `ThresholdKeys` so that signing produces valid signatures under a key the victim never agreed to:

- Set `t = 1`, `n = 1`, `i = 1`, keep the victim's real `secret_share` `s_v`, and set `verification_shares[1]` to the victim's (publicly known) verification share `V_v = G·s_v`. `group_key()` becomes `V_v`, `view()` yields `secret_share = s_v` (Lagrange factor over the singleton set is 1), and the FROST signature produced is a valid Schnorr signature on the arbitrary message under `V_v`. The attacker cannot produce this signature themselves (they don't know `s_v`) — this is a forgery/key-confusion oracle: the victim signs arbitrary messages under a per-participant key, believing they are executing a `t-of-n` group signature for the real group key.
- More generally, the attacker can set `n`/`t`/`i`, the `Constant` interpolation coefficients, and the other verification shares (including dummy participants whose shares they satisfy) so the emitted signature verifies under `Σ λ_j·V_j + offset·G` of their choosing, any function affine in `s_v` — e.g., another participant's share key, or a promotion/derived key on an alternate generator where `s_v` is meaningful.
- Tampered real blobs (changing only `i` or other participants' `V_j`) also let the attacker cause honest participants' shares to be blamed at `complete()`, redistributing fault — a direct analog of "overriding a node's voter status" corrupting cluster membership state.

This meets the "concrete signing of an unintended message / key-share misuse" bar: attacker-controlled bytes to `ThresholdKeys::read` cause the victim's `sign`/`complete` API to emit signatures under attacker-selected public keys.

### Likelihood Explanation
Reachable whenever serialized `ThresholdKeys` (the `read`/`serialize` format explicitly exists for persistence/transport) can be influenced by an untrusted party — backups, replicated stores, or any path where key material blobs cross a trust boundary. The bytes-only manipulation (rewriting `t`, `n`, `i`, interpolation tag, and verification shares) requires no knowledge of any secret, since the victim's real `secret_share` field is reused verbatim and the required `verification_shares` entries are public points. Exploitation yields valid signatures under attacker-chosen keys, which the attack requirements classify as a meaningful signing primitive.

### Recommendation
In `ThresholdKeys::new` (and hence `ThresholdKeys::read`), verify that `C::generator() * secret_share == verification_shares[&params.i()]` and reject mismatches. Additionally, for serialized keys, bind a transcript/commitment of `(t, n, i, verification_shares)` or authenticate the blob externally (MAC/integrity), so membership parameters — the "voter roster" — cannot be silently rewritten between write and read. Consider rejecting `t = n = 1` reconstructions or requiring DKG-produced provenance markers for `Lagrange` keys.

### Proof of Concept
1. Obtain an honest node's serialized `ThresholdKeys<C>` blob (or any context where its bytes are attacker-influenced before `ThresholdKeys::read`).
2. Parse out the victim's real `secret_share` bytes and their real verification share `V_v` (public).
3. Rewrite the header: `t = 1`, `n = 1`, `i = 1`, interpolation byte = `1` (Lagrange), keep `secret_share` unchanged, and write `verification_shares = {1: V_v}` (or any target point `G·s_v`-consistent with the intended final `group_key`, or `Constant` coefficients to get `c·s_v`).
4. Feed the blob to `ThresholdKeys::read` → succeeds (`new` passes all checks).
5. Run `AlgorithmMachine::new(alg, keys).preprocess(...)` → `sign(preprocesses, attacker_msg)` → `complete(shares)`: all internal `verify_share` checks pass because the view is self-consistent.
6. The result is a valid Schnorr signature on `attacker_msg` under `group_key = V_v` — a signature the attacker could not produce alone, under a key the victim never intended to sign with.

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

**File:** crypto/dkg/src/lib.rs (L494-521)
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

    /*
      The offset is included by adding it to the participant with the lowest ID.

      This is done after interpolating to ensure, regardless of the method of interpolation, that
      the method of interpolation does not scale the offset. For Lagrange interpolation, we could
      add the offset to every key share before interpolating, yet for Constant interpolation, we
      _have_ to add it as we do here (which also works even when we intend to perform Lagrange
      interpolation).
    */
    if included[0] == self.params().i() {
      *secret_share += self.offset;
    }
    *verification_shares.get_mut(&included[0]).unwrap() += C::generator() * self.offset;
```

**File:** crypto/dkg/src/lib.rs (L591-631)
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

    ThresholdKeys::new(
      ThresholdParams::new(t, n, i).map_err(io::Error::other)?,
      interpolation,
      secret_share,
      verification_shares,
    )
    .map_err(io::Error::other)
```

**File:** crypto/frost/src/sign.rs (L312-313)
```rust
    let view = self.params.keys.view(included.clone()).unwrap();
    validate_map(&preprocesses, &included, multisig_params.i())?;
```
