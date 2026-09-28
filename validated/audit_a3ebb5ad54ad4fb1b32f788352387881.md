### Title
Secret scalars and interpolated shares retained in heap after drop due to missing `ZeroizeOnDrop` — improper clearing of secret material (File: crypto/frost/src/sign.rs, crypto/dkg/src/lib.rs)

### Summary
CVE-2025-33101 describes sensitive-information disclosure caused by improper clearing of heap memory. The Serai analog lives in the FROST/DKG state machines: several structures holding secret scalars (the interpolated secret share's `scalar`/`offset` tweaks, `Interpolation::Constant` coefficient vectors, the final signature-share `sum`, and per-signer `responses`) implement `Zeroize` but not `ZeroizeOnDrop`, or are stored in types with no `Drop` implementation at all. They are dropped in normal signing paths without being cleared, leaving secret-derived scalar bytes resident in freed heap memory.

### Finding Description
`ThresholdKeys` derives `Zeroize` but annotates its `core` field with `#[zeroize(skip)]`, so an explicit `keys.zeroize()` never clears `secret_share`, and the type has no `Drop` impl — the `scalar`/`offset` tweak scalars are only wiped if a caller manually invokes `zeroize()` [1](#0-0) . `ThresholdView` likewise implements `Zeroize` but not `ZeroizeOnDrop`; its `scalar`, `offset`, `included`, and `interpolation` fields — including `Interpolation::Constant(Vec<F>)`, a heap `Vec` of secret interpolation coefficients — are only cleared if `.zeroize()` is called explicitly [2](#0-1) [3](#0-2) .

In the signing path, `ThresholdView::secret_share` produces the interpolated, tweak-adjusted share in a `Zeroizing`, but the `view` itself is moved into `AlgorithmSignatureMachine`, which has **no** `Zeroize`/`ZeroizeOnDrop` derive and no `Drop` impl — so `scalar`, `offset`, `interpolation`, and `blame_entropy` are abandoned in memory when `complete()` consumes `self` [4](#0-3) [5](#0-4) . Inside `complete()`, `sum` and the `responses` map hold every participant's signature share as plain `C::F` values in a heap `HashMap`, and `self.share` is copied into `responses` — none of these are zeroized on function exit [6](#0-5) . Similarly, `EncryptedMessage` derives `Clone, Zeroize` but not `ZeroizeOnDrop`, so the PoP `pop` Schnorr signature's `s` scalar (which covers the ephemeral ECDH key) and clones of the ciphertext key are not cleared on drop [7](#0-6) .

### Impact Explanation
Under the same threat model as CVE-2025-33101 (disclosure of residual heap contents to an attacker, e.g. via a memory-read/MITM-adjacent primitive), freed memory retains: (a) the secret `scalar`/`offset` tweaks and `Constant` interpolation coefficients — `offset` is the per-key additive tweak used by bitcoin-serai for BIP-32-style/TapTweak derivation, so recovering it alongside a captured signature share yields the underlying untweaked share; (b) the full `responses` map of signature shares — with `t` shares and knowledge of the preprocesses, an attacker can interpolate to the group secret's contribution or recover an individual `secret_share` from `s_i = nonce_i + c_i * share_i` if any nonce residue also survives. The codebase itself documents that nonce/share knowledge "enables recovery" of the private key share [8](#0-7) .

### Likelihood Explanation
An unprivileged party cannot trigger this purely over the wire; exploitation needs a memory-disclosure primitive on the signing process (the same precondition as the referenced heap-clearing CVE). The residues are created on every `sign()`/`complete()` and every `view()`/`recover_key()` call, so the secret material is reliably present in freed/reallocatable heap rather than only on error paths. Rust reallocation within the process or a core dump/OS-level read makes this recoverable.

### Recommendation
Derive or manually implement `ZeroizeOnDrop` for `ThresholdKeys`, `ThresholdView`, `Params`, `AlgorithmSignMachine`, `AlgorithmSignatureMachine`, and `EncryptedMessage`; wrap `scalar`/`offset`/`share`/`sum`/`responses` scalars in `Zeroizing<C::F>`; remove the `#[zeroize(skip)]` on `ThresholdKeys::core` or make `zeroize()` zeroize the `ThresholdCore` in place; zeroize the `responses` map and `sum` at the end of `complete()`.

### Proof of Concept
```rust
// conceptual: any signing run leaves residuals
let keys: ThresholdKeys<Ed25519> = /* via musig() or PedPoP */;
let machine = AlgorithmMachine::new(IetfSchnorr::ietf(), keys);
let (sign_machine, _pp) = machine.preprocess(&mut OsRng);
let (sig_machine, _share) = sign_machine.sign(preprocesses, msg).unwrap();
let _sig = sig_machine.complete(shares).unwrap();
// At this point:
//  - ThresholdView.scalar / offset / interpolation (heap Vec<F>) are freed unzeroed
//  - responses HashMap of all C::F signature shares and `sum` are freed unzeroed
//  - any prior keys.zeroize() call skipped core.secret_share (#[zeroize(skip)])
// A heap scan/core dump recovers tweak scalars and signature shares,
// enabling private key-share recovery per the crate's own CachedPreprocess warning.
```
Key residual sites: `crypto/frost/src/sign.rs:454-460` (`responses`, `sum`), `crypto/frost/src/sign.rs:430-438` (`AlgorithmSignatureMachine` with no Drop/ZeroizeOnDrop holding `view`), `crypto/dkg/src/lib.rs:294` (`#[zeroize(skip)] core`), `crypto/dkg/src/lib.rs:331-345` (`Zeroize`-only `ThresholdView`).

### Citations

**File:** crypto/dkg/src/lib.rs (L291-301)
```rust
#[derive(Clone, Debug, Zeroize)]
pub struct ThresholdKeys<C: Ciphersuite> {
  // Core keys.
  #[zeroize(skip)]
  core: Arc<Zeroizing<ThresholdCore<C>>>,

  // Scalar applied to these keys.
  scalar: C::F,
  // Offset applied to these keys.
  offset: C::F,
}
```

**File:** crypto/dkg/src/lib.rs (L304-314)
```rust
#[derive(Clone)]
pub struct ThresholdView<C: Ciphersuite> {
  interpolation: Interpolation<C::F>,
  scalar: C::F,
  offset: C::F,
  group_key: C::G,
  included: Vec<Participant>,
  secret_share: Zeroizing<C::F>,
  original_verification_shares: HashMap<Participant, C::G>,
  verification_shares: HashMap<Participant, C::G>,
}
```

**File:** crypto/dkg/src/lib.rs (L331-345)
```rust
impl<C: Ciphersuite> Zeroize for ThresholdView<C> {
  fn zeroize(&mut self) {
    self.scalar.zeroize();
    self.offset.zeroize();
    self.group_key.zeroize();
    self.included.zeroize();
    self.secret_share.zeroize();
    for share in self.original_verification_shares.values_mut() {
      share.zeroize();
    }
    for share in self.verification_shares.values_mut() {
      share.zeroize();
    }
  }
}
```

**File:** crypto/dkg/src/lib.rs (L494-498)
```rust
    let secret_share_scaled = Zeroizing::new(self.scalar * self.original_secret_share().deref());
    let mut secret_share = Zeroizing::new(
      self.core.interpolation.interpolation_factor(self.params().i(), &included) *
        secret_share_scaled.deref(),
    );
```

**File:** crypto/frost/src/sign.rs (L84-92)
```rust
///
/// A preprocess MUST only be used once. Reuse will enable third-party recovery of your private
/// key share. Additionally, this MUST be handled with the same security as your private key share,
/// as knowledge of it also enables recovery.
// Directly exposes the [u8; 32] member to void needing to route through std::io interfaces.
// Still uses Zeroizing internally so when users grab it, they have a higher likelihood of
// appreciating how to handle it and don't immediately start copying it just by grabbing it.
#[derive(Zeroize)]
pub struct CachedPreprocess(pub Zeroizing<[u8; 32]>);
```

**File:** crypto/frost/src/sign.rs (L430-438)
```rust
#[allow(non_snake_case)]
pub struct AlgorithmSignatureMachine<C: Curve, A: Algorithm<C>> {
  params: Params<C, A>,
  view: ThresholdView<C>,
  B: BindingFactor<C>,
  Rs: Vec<Vec<C::G>>,
  share: C::F,
  blame_entropy: [u8; 32],
}
```

**File:** crypto/frost/src/sign.rs (L454-460)
```rust
    let mut responses = HashMap::new();
    responses.insert(params.i(), self.share);
    let mut sum = self.share;
    for (l, share) in shares.drain() {
      responses.insert(l, share.0);
      sum += share.0;
    }
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L80-97)
```rust
#[derive(Clone, Zeroize)]
pub struct EncryptedMessage<C: Ciphersuite, E: Encryptable> {
  key: C::G,
  // Also include a proof-of-possession for the key.
  // If this proof-of-possession wasn't here, Eve could observe Alice encrypt to Bob with key X,
  // then send Bob a message also claiming to use X.
  // While Eve's message would fail to meaningfully decrypt, Bob would then use this to create a
  // blame argument against Eve. When they do, they'd reveal bX, revealing Alice's message to Bob.
  // This is a massive side effect which could break some protocols, in the worst case.
  // While Eve can still reuse their own keys, causing Bob to leak all messages by revealing for
  // any single one, that's effectively Eve revealing themselves, and not considered relevant.
  pop: SchnorrSignature<C>,
  msg: Zeroizing<E>,
}

fn ecdh<C: Ciphersuite>(private: &Zeroizing<C::F>, public: C::G) -> Zeroizing<C::G> {
  Zeroizing::new(public * private.deref())
}
```
