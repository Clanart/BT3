### Title
FROST secret nonces are left in heap memory when `AlgorithmSignMachine::sign()` aborts on attacker-controlled input - (File: crypto/frost/src/sign.rs)

### Summary
The kernel bug class is an asymmetric cleanup: a `tmio_mmc_host_free()` balancing call exists in the probe error path but is missing in the remove/teardown path, leaving resources allocated. Serai has the same asymmetry in `AlgorithmSignMachine::sign()`. The struct carries `nonces: Vec<Nonce<C>>` — secret FROST nonces the library itself documents as private-key-share-equivalent — and derives `Zeroize`, which only provides a `zeroize()` method, not automatic wiping on drop. On the success path the nonces are consumed via `self.nonces.drain(..)`, but on every early error return (all reachable from untrusted bytes supplied through `read_preprocess`) the machine is dropped without ever calling `zeroize()`, leaving the raw nonce scalars in freed heap memory.

### Finding Description
`AlgorithmSignMachine` is defined with `#[derive(Zeroize)]` at `crypto/frost/src/sign.rs:245-255`:

```rust
#[derive(Zeroize)]
pub struct AlgorithmSignMachine<C: Curve, A: Algorithm<C>> {
  params: Params<C, A>,
  seed: CachedPreprocess,
  pub(crate) nonces: Vec<Nonce<C>>,
  #[zeroize(skip)]
  pub(crate) preprocess: Preprocess<C, A::Addendum>,
  pub(crate) blame_entropy: [u8; 32],
}
```

`derive(Zeroize)` only emits `fn zeroize(&mut self)`; it does not emit `Drop`. There is no `impl Drop` or `ZeroizeOnDrop` for this type anywhere in `crypto/frost`. The only place the nonces are cleared is implicit consumption on the success path: `self.nonces.drain(..)` at `crypto/frost/src/sign.rs:386-396`. All preceding `?` early-returns drop `self` with the nonce vector intact and never wiped:

- `Err(FrostError::InvalidSigningSet)` — `sign.rs:298-300`
- `Err(FrostError::InvalidParticipant)` — `sign.rs:302-304` (attacker picks a participant index > n)
- `Err(FrostError::DuplicatedParticipant)` — `sign.rs:306-310` (attacker cannot duplicate a map key, but an attacker-influenced `i()` collision or local mis-set is not required; the more reliable trigger is below)
- `self.params.algorithm.process_addendum(&view, *l, addendum)?` — `sign.rs:346` and `sign.rs:357`, where `addendum` comes from `preprocesses.remove(l)`, i.e. bytes the attacker serialized and the victim parsed via `read_preprocess` (`sign.rs:276-281`)
- `validate_map(&preprocesses, &included, multisig_params.i())?` — `sign.rs:313`, which fails on attacker-missing or attacker-extraneous participants

Every one of these is reachable purely from the `HashMap<Participant, Preprocess>` the caller builds out of `read_preprocess`-decoded bytes — exactly the allowed attack surface ("untrusted bytes fed to `read_preprocess` ... or to a sign / complete API").

The secrecy of the leaked material is established by the library's own comments: a preprocess/nonce "MUST be handled with the same security as your private key share, as knowledge of it also enables recovery" (`sign.rs:84-87`), and `Nonce<C>` itself is `[Zeroizing<C::F>; 2]` (`crypto/frost/src/nonce.rs:27-28`) — the inner scalars are `Zeroizing`, but the `Vec<Nonce<C>>` heap buffer and each `Nonce` are only wiped if `zeroize()` is invoked, which nothing does on drop. Contrast with `SecretShare`, where the codebase explicitly hand-implements `Drop` + `ZeroizeOnDrop` "to ensure these don't stick around" (`crypto/dkg/pedpop/src/lib.rs:252-261`), confirming the intended invariant that share-equivalent material must not linger after teardown.

This is the direct analog: cleanup (`tmio_mmc_host_free` / nonce zeroization) is performed on one path but omitted on the teardown path that is actually exercised by the attacker.

### Impact Explanation
A recovered nonce `d` or `e` for a preprocess whose commitments `D = d·G`, `E = e·G` and signature share `z_i = d + ρ·e + λ_i·x_i·c` are public yields the victim's threshold secret share directly: `x_i = (z_i − d − ρ·e) / (λ_i·c)`. That is full key-share recovery — one of the enumerated acceptable impacts. The primitive left behind is exactly the one the API docs say enables "recovery of your private key share". The attacker only needs to feed a malformed or inconsistent preprocess set (e.g., an addendum that makes `process_addendum` return `Err`, or a `preprocesses` map failing `validate_map`) to make the victim abort `sign()` after the nonces were already generated in `seeded_preprocess` (`sign.rs:121-144`). Confidentiality loss matches the original CVE's `C:H` and Medium severity.

### Likelihood Explanation
Triggering the abort is trivially reachable: any peer in a signing session submits a preprocess that deserializes but fails `validate_map` or `process_addendum`. Exploiting the residue requires a subsequent memory-disclosure primitive (core dump, heap inspection, `/proc` read, co-resident process) on the signer's host — a local precondition consistent with the source advisory's `AV:L` vector and the documented operational reality that signers run on hosts an attacker may partially observe. Because `sign()` consumes `self`, there is no way for the caller to scrub the nonces after an `Err` — the omission is structural, not a misuse, so every aborted session deterministically leaves the material behind.

### Recommendation
Give `AlgorithmSignMachine` (and `Nonce`, `Params`, `AlgorithmSignatureMachine` for defense in depth) a `Drop` implementation that calls `zeroize()`, or wrap the nonce vector in `Zeroizing<Vec<Nonce<C>>>`, mirroring the `SecretShare` `Drop`/`ZeroizeOnDrop` pattern in `crypto/dkg/pedpop/src/lib.rs:256-261`. Additionally, move `self.nonces.drain(..)` ahead of the fallible parsing/validation block so the secret material is consumed-and-bound (into `actual` scalars) or explicitly wiped before any attacker-influenced `?` can fire, and add an explicit `self.nonces.zeroize()` (or `drop(Zeroizing::new(...))`) inside each error branch.

### Proof of Concept
```rust
// Victim: preprocess() then sign() against a malicious peer's preprocess set.
use frost::{curve::Ed25519, algorithm::Schnorr, PreprocessMachine, SignMachine};
use std::collections::HashMap;
use rand_core::OsRng;

let keys: ThresholdKeys<Ed25519> = /* victim's threshold keys */;
let machine = AlgorithmMachine::<Ed25519, Schnorr<Ed25519>>::new(Schnorr::new(b"proto"), keys);
let (sign_machine, _our_preprocess) = machine.preprocess(&mut OsRng);

// Attacker-controlled input, decoded via read_preprocess and inserted into the map:
// either a Preprocess for a Participant index > params.n() (hits InvalidParticipant,
// sign.rs:302-304), or an addendum byte-string that makes process_addendum return
// Err (sign.rs:357). No key material is required to construct either.
let mut preprocesses: HashMap<Participant, Preprocess<_, _>> = attacker_supplied_map();

// Err path: `self` is dropped; `#[derive(Zeroize)]` never runs, there is no Drop impl,
// and `self.nonces.drain(..)` (sign.rs:386) is never reached.
let res = sign_machine.sign(preprocesses, b"message");
assert!(res.is_err());

// The heap pages backing `Vec<Nonce<Ed25519>>` still contain the raw (d, e) scalars.
// A subsequent local memory disclosure recovers them; combined with the public
// SignatureShare z_i, commitments (D, E), binding factor ρ, challenge c, and the
// public Lagrange coefficient λ_i, the attacker computes:
//   x_i = (z_i - d - ρ·e) · (λ_i·c)^-1
// yielding the victim's private key share — the asset the docs equate to the
// preprocess (sign.rs:84-87).
```