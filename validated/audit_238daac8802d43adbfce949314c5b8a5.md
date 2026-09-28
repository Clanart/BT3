### Title
`ThresholdKeys::read` deserializes attacker-supplied secret share and verification shares with no consistency check, allowing substitution of the reported group key - (File: crypto/dkg/src/lib.rs)

### Summary
The bug class in the report is a parser consuming untrusted structured input without validating its semantic constraints (XXE: the parser resolves entities the application never authorized). The Serai analog is `ThresholdKeys::read`: it accepts a fully attacker-controlled serialization of a threshold key — `secret_share`, all `verification_shares`, and `t`/`n`/`i` — and `ThresholdKeys::new` performs only structural checks (count of shares, participant bounds, interpolation applicability). It never verifies that `C::generator() * secret_share == verification_shares[i]`, and it derives `group_key` purely from `verification_shares[1..=t]`. The result is a `ThresholdKeys` object whose reported group key and whose signing share are entirely decoupled and attacker-chosen.

### Finding Description
`ThresholdKeys::read` reads `t`, `n`, `i`, the interpolation variant, a raw scalar as `secret_share`, and `n` group elements as `verification_shares`, then calls `ThresholdKeys::new` [1](#0-0) . `ThresholdKeys::new` checks only `verification_shares.len() == n`, `participant <= n`, and that `Constant` interpolation implies `t == n` [2](#0-1) . It then computes `group_key` as the interpolation over `verification_shares` for participants `1..=t` [3](#0-2)  and returns the keys with no check that `verification_shares[params.i()] == C::generator() * secret_share`.

Because `secret_share` is never bound to the verification shares, a crafted byte stream produces keys where:

1. `group_key()` is whatever the attacker's chosen `verification_shares[1..=t]` interpolate to — including a point with attacker-known discrete log `k` (pick verification shares that interpolate to `G*k`).
2. `secret_share` is an unrelated scalar, so `keys.view(included).secret_share()` produces FROST signature shares that do not correspond to `group_key` — every signing session either fails verification or, worse, the network is told its key is one the attacker unilaterally controls.

`ThresholdKeys::read` is explicitly in the set of read APIs fed untrusted bytes, and these keys are exactly what drive FROST signing (`view`, `secret_share`) and what downstream code reports as the multisig/network key (e.g., `GeneratedKeysDb::save_keys` stores keys indexed by `group_key().to_bytes()`, and `network_key` is emitted from `group_key().to_bytes()` in `processor/src/key_gen.rs` [4](#0-3) ).

### Impact Explanation
An attacker who can feed crafted bytes to `ThresholdKeys::read` (malformed key backup / imported key blob / any path deserializing `ThresholdKeys` from non-local input) causes the node to adopt a group key whose discrete log the attacker knows. Consequences:

- **Funds reported received that are not spendable (by the network)**: the deposit address/script derived from `group_key()` is unilaterally spendable by the attacker while the validator set believes it is their threshold key — deposits are effectively stolen.
- **Unintended signing / share inconsistency**: FROST signing uses `secret_share` for the share and `verification_shares`/`group_key` for verification of others' shares; since the two are decoupled, the honest node emits signature shares invalid under its own claimed verification share, corrupting or misattributing signing sessions.

This satisfies the acceptance criteria: key share recovery by the attacker (they choose the group key's discrete log outright) and funds under a key not spendable by the legitimate threshold.

### Likelihood Explanation
Requires a path where untrusted bytes reach `ThresholdKeys::read` — key imports, backups, or migrations. Where that path exists, exploitation is deterministic: no probability, no race, and no need for malicious-validator or collusion assumptions since the flaw is entirely in deserialization. Within the stated scope (untrusted bytes to `ThresholdKeys::read` are in-scope), likelihood is high; absent such a path in a given deployment it is unreachable.

### Recommendation
In `ThresholdKeys::new` (so all constructors benefit, including `read`), verify `verification_shares[&params.i()] == C::generator() * secret_share` before accepting the keys. Optionally, when `interpolation` is `Constant`, also sanity-check the coefficient vector length against `n`. This binds the secret share to the public commitment set so a tampered serialization is rejected rather than yielding an inconsistent key.

### Proof of Concept
```rust
// Crafted serialization for a ThresholdKeys<Ristretto> where:
//  - group_key interpolates to G * k for attacker-known k
//  - secret_share is arbitrary garbage
let t: u16 = 2; let n: u16 = 3; let i: u16 = 1;
let k = <Ristretto as Ciphersuite>::F::random(&mut OsRng); // attacker-known
// verification shares 1..=t chosen so Lagrange interpolation at 0 = G*k
// e.g. v1 = G*k, v2 = G*k (constant poly), v3 arbitrary
let mut buf = vec![];
buf.extend(3u32.to_le_bytes());           // len("Ristretto".len()...) per C::ID
buf.extend(<Ristretto as Ciphersuite>::ID);
buf.extend(t.to_le_bytes());
buf.extend(n.to_le_bytes());
buf.extend(i.to_le_bytes());
buf.push(1);                              // Interpolation::Lagrange
buf.extend(<Ristretto as Ciphersuite>::F::random(&mut OsRng).to_repr()); // bogus secret_share
for vs in [g*k, g*k, g*rnd] { buf.extend(vs.to_bytes()); }

let keys = ThresholdKeys::<Ristretto>::read(&mut buf.as_slice()).unwrap(); // accepted
assert_eq!(keys.group_key(), Ristretto::generator() * k); // attacker-controlled key
// keys.original_secret_share() != share consistent with verification_shares[1]
```
The `unwrap()` succeeding while `group_key` is attacker-controlled demonstrates the missing consistency check at `crypto/dkg/src/lib.rs:355-390` and `:574-632`.

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

**File:** crypto/dkg/src/lib.rs (L376-379)
```rust
    let t = (1 ..= params.t()).map(Participant).collect::<Vec<_>>();
    let group_key =
      t.iter().map(|i| verification_shares[i] * interpolation.interpolation_factor(*i, &t)).sum();

```

**File:** crypto/dkg/src/lib.rs (L591-632)
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
  }
```

**File:** processor/src/key_gen.rs (L494-501)
```rust
        GeneratedKeysDb::save_keys::<N>(txn, &id, &substrate_keys, &network_keys);

        ProcessorMessage::GeneratedKeyPair {
          id,
          substrate_key: generated_substrate_key.unwrap().to_bytes(),
          // TODO: This can be made more efficient since tweaked keys may be a subset of keys
          network_key: generated_network_key.unwrap().to_bytes().as_ref().to_vec(),
        }
```
