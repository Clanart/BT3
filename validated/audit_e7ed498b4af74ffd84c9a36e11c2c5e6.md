### Title
Untrusted `ThresholdKeys` bytes interpreted as interpolation "program" without binding secret share to verification shares - (File: crypto/dkg/src/lib.rs)

### Summary
The tqdm advisory's root cause is that untrusted input was handed to `eval`, i.e. bytes were interpreted as meaningful execution parameters without validation. The Serai analog lives in `ThresholdKeys::read` / `ThresholdKeys::new` (`crypto/dkg/src/lib.rs`): an attacker-controlled serialization selects an `Interpolation` variant by tag byte and, for `Interpolation::Constant`, supplies `n` raw scalars that are used directly as the interpolation coefficients — the "program" by which the group key is computed. `ThresholdKeys::new` recomputes `group_key = Σ_{i=1..=t} verification_shares[i] * interpolation_factor(i, 1..=t)` from those attacker-chosen values, yet never verifies that the supplied `secret_share` actually corresponds to `verification_shares[i]` (`C::generator() * secret_share == verification_shares[i]` is never checked). The deserialized key set is therefore fully self-consistent by construction while being cryptographically incoherent.

### Finding Description
`ThresholdKeys::read` (`crypto/dkg/src/lib.rs:574-632`) reads the interpolation tag byte at lines 604-616: `0` selects `Interpolation::Constant` and reads `n` attacker-controlled scalars as coefficients; `1` selects Lagrange. `ThresholdKeys::new` (`crypto/dkg/src/lib.rs:349-391`) then computes the group key at lines 376-378:

```rust
let t = (1 ..= params.t()).map(Participant).collect::<Vec<_>>();
let group_key =
  t.iter().map(|i| verification_shares[i] * interpolation.interpolation_factor(*i, &t)).sum();
```

For `Constant`, `interpolation_factor` returns `c[i-1]` verbatim (line 228) — attacker bytes evaluated as the reconstruction formula, mirroring eval semantics. `new` checks the count of verification shares (355-360), that participant indexes are `<= n` (361-365), and that `Constant` is only used when `t == n` (368-372), but it never checks the fundamental invariant `C::generator() * secret_share == verification_shares[params.i()]`. An attacker supplying the byte stream (the sink is `ThresholdKeys::read`, explicitly in scope for untrusted bytes, e.g. a malicious key-recovery/import blob) can produce keys whose `group_key()` is any point of their choosing while `secret_share` is unrelated. Downstream, `view()` (463-533) happily interpolates with the attacker coefficients and signs shares that cannot reconstruct a valid signature for the reported `group_key`.

### Impact Explanation
Funds addressed to / scanned under `group_key()` (e.g. via bitcoin-serai `Scanner` matching the TapTweak/Schnorr output key) are reported received but are not spendable: no threshold of honest shares exists that reconstructs the discrete logarithm of the attacker-derived group key, since the secret shares were never bound to the verification shares. This is the "funds reported received that are not spendable" impact class. Conversely, a blob crafted with a consistent (share, verification share) but malicious coefficients lets the provider dictate the effective reconstruction constants, silently changing which subsets of participants can jointly sign versus what the deployment's `t-of-n` policy intended.

### Likelihood Explanation
Requires an unprivileged party to supply the bytes fed to `ThresholdKeys::read` (key import/recovery/migration path rather than locally generated PedPoP output). When reachable, exploitation is deterministic — a single crafted blob. Impact is medium-to-high (permanent loss of spendability for the affected key) but conditioned on the import path being attacker-influenced, so Medium overall.

### Recommendation
In `ThresholdKeys::new` (and transitively `read`), enforce `C::generator() * secret_share == verification_shares[&params.i()]`, rejecting inconsistent blobs at deserialization. Additionally, for `Interpolation::Constant`, consider requiring that the coefficients reproduce the group key implied by the verification shares under standard Lagrange evaluation, or document and validate the trusted-origin requirement of the serialized blob (e.g. authenticate it with a MAC/signature before `read`).

### Proof of Concept
```rust
use ciphersuite::Ciphersuite;
use dkg::{ThresholdKeys, Participant, ThresholdParams, Interpolation};
use dalek_ff_group::Ed25519 as C;
use zeroize::Zeroizing;
use std::collections::HashMap;
use group::Group;

fn main() {
  // t == n == 2 so Interpolation::Constant is permitted
  let t = 2u16; let n = 2u16; let i = Participant::new(1).unwrap();

  // Attacker-chosen "coefficients" (the eval'd program) and arbitrary points
  let c = vec![<C as Ciphersuite>::F::ONE, <C as Ciphersuite>::F::ONE];
  // verification_shares chosen so group_key = P_attacker with known discrete log
  let v1 = C::generator() * <C as Ciphersuite>::F::from(3u64);
  let v2 = C::generator() * <C as Ciphersuite>::F::from(5u64);
  let mut vs = HashMap::new();
  vs.insert(Participant::new(1).unwrap(), v1);
  vs.insert(Participant::new(2).unwrap(), v2);

  // secret_share deliberately NOT equal to the dlog of v1 — never checked
  let bogus_share = Zeroizing::new(<C as Ciphersuite>::F::from(42u64));

  let keys = ThresholdKeys::<C>::new(
    ThresholdParams::new(t, n, i).unwrap(),
    Interpolation::Constant(c),
    bogus_share,
    vs,
  ).unwrap(); // accepted despite secret_share * G != v1

  // group_key() reports v1 + v2 = 8*G; deposits scanned to it are unspendable
  // because no consistent share set exists for that key.
}
```
The equivalent blob serialized through `ThresholdKeys::write`/`read` (tag byte `0`, two coefficient scalars, bogus `secret_share`, two point encodings) is accepted identically by `ThresholdKeys::read` (`crypto/dkg/src/lib.rs:574-632`), demonstrating the reachable deserialization path.