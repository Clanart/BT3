### Title
Empty SchnorrAggregate trivially verifies, letting an attacker "shrink the signer set to zero" to forge an aggregate signature - (File: crypto/schnorr/src/aggregate.rs)

### Summary
The FeiPool bug let an attacker shrink a shared denominator (`totalRedeemablePoolTokens`) until the payout formula was trivially satisfiable and returned the entire pool. `SchnorrAggregate::verify` has the same shape: the verification equation `sum_i z_i*(R_i + c_i*A_i) - s*G == 0` is checked over the attacker-supplied `Rs`/`s` and a caller-supplied `keys_and_challenges` list, with no check that the set is non-empty. An aggregate with zero nonces and `s = 0` makes the multiexp a single `0*G` term, which is the identity, so verification returns `true` — a forged aggregate signature over zero signers.

### Finding Description
`SchnorrAggregate::read` accepts a length-prefixed `Rs` list, including length 0, plus a scalar `s` [1](#0-0) . `verify` only checks `self.Rs.len() == keys_and_challenges.len()` [2](#0-1) , then builds pairs and checks the multiexp is the identity [3](#0-2) . With both lists empty and `s = 0`, `pairs` is just `[(-0, G)]`, `multiexp_vartime` yields the identity, and `verify` returns `true`.

This mirrors the FeiPool exploit: the attacker shrinks the summation set (the analog of `totalRedeemablePoolTokens`) to zero so the equation is satisfied by `s = 0`, claiming a valid signature without any key. Note the asymmetry with the honest path: `SchnorrAggregator::complete` explicitly refuses to produce an empty aggregate (`None` when `sigs.is_empty()`) [4](#0-3) , so the verifier accepts a statement the prover side deliberately cannot create — an incorrect verifier formula.

Reachability: all inputs are public/untrusted bytes — `SchnorrAggregate::read` feeds `C::read_G`/`C::read_F` on attacker-controlled data, and `verify(dst, keys_and_challenges)` is a public verification API. Serai's own consumer pattern (`verify_aggregate` in the Tributary Tendermint layer) forwards an attacker-supplied aggregate byte string and a `signers` list; any caller whose signer list can be empty (or which trusts the length equality check as sufficient) accepts a forged signature.

### Impact Explanation
A forged Schnorr half-aggregate signature: `SchnorrAggregate::read(&[0x00,0x00,0x00,0x00] ++ [0u8;32])` verifies as valid for an empty signer set. Any protocol that treats "aggregate verifies for the given signers" as authorization, and can reach a state where the signer set is empty (e.g., all signers filtered out, removed, or the aggregate is substituted where a non-empty threshold was assumed), accepts a signature authorizing arbitrary messages/weights with no private key material. This is the "everyone's balance was burned so the remaining share claims the whole pool" attack restated as "nobody signed, so the empty equation balances."

### Likelihood Explanation
Exploitation requires the calling context to evaluate an aggregate over an empty (or attacker-influenced-to-empty) signer list. The primitive unconditionally returns `true`; whether a given integrator reaches that state depends on their signer-set construction. The check costs the attacker nothing — 36 fixed bytes — so the likelihood is bounded by integrator assumptions rather than cryptography. Medium: deterministic forgery against the API contract, contingent on a reachable empty signer set.

### Recommendation
In `SchnorrAggregate::verify` (crypto/schnorr/src/aggregate.rs), reject `self.Rs.is_empty()` (equivalently, `keys_and_challenges.is_empty()`) before checking the multiexp, matching `SchnorrAggregator::complete`'s `None` behavior for empty inputs. Consider also asserting `s` is non-zero or that the aggregate contains at least one signer so the verification equation is never trivially satisfiable.

### Proof of Concept
```rust
use schnorr::aggregate::SchnorrAggregate;
use dalek_ff_group::Ed25519;
use ciphersuite::Ciphersuite;

// An aggregate signature over zero signers: len = 0, s = 0
let mut bytes = vec![0x00, 0x00, 0x00, 0x00]; // u32 len = 0
bytes.extend([0u8; 32]);                      // s = 0

let aggregate =
  SchnorrAggregate::<Ed25519>::read::<&[u8]>(&mut bytes.as_slice()).unwrap();

// No signers, yet verify returns true:
// pairs == [(-0, G)] -> multiexp is identity -> true
assert!(aggregate.verify(b"Any DST", &[]));
```

### Citations

**File:** crypto/schnorr/src/aggregate.rs (L77-88)
```rust
  pub fn read<R: Read>(reader: &mut R) -> io::Result<Self> {
    let mut len = [0; 4];
    reader.read_exact(&mut len)?;

    #[allow(non_snake_case)]
    let mut Rs = vec![];
    for _ in 0 .. u32::from_le_bytes(len) {
      Rs.push(C::read_G(reader)?);
    }

    Ok(SchnorrAggregate { Rs, s: C::read_F(reader)? })
  }
```

**File:** crypto/schnorr/src/aggregate.rs (L127-130)
```rust
  pub fn verify(&self, dst: &'static [u8], keys_and_challenges: &[(C::G, C::F)]) -> bool {
    if self.Rs.len() != keys_and_challenges.len() {
      return false;
    }
```

**File:** crypto/schnorr/src/aggregate.rs (L138-145)
```rust
    let mut pairs = Vec::with_capacity((2 * keys_and_challenges.len()) + 1);
    for (i, (key, challenge)) in keys_and_challenges.iter().enumerate() {
      let z = weight(&mut digest);
      pairs.push((z, self.Rs[i]));
      pairs.push((z * challenge, *key));
    }
    pairs.push((-self.s, C::generator()));
    multiexp_vartime(&pairs).is_identity().into()
```

**File:** crypto/schnorr/src/aggregate.rs (L175-179)
```rust
  pub fn complete(mut self) -> Option<SchnorrAggregate<C>> {
    if self.sigs.is_empty() {
      return None;
    }

```
