### Title
Deterministic Cached Preprocess Reuse Enables FROST Nonce Reuse and Secret Share / Group Key Recovery - (File: coordinator/src/tributary/signing_protocol.rs)

### Summary
The external report concerns an API that exposed internally mutable state through seemingly immutable/shared handles, letting shared state be silently reused where uniqueness was assumed. The Serai analog lives in `SigningProtocol::preprocess_internal`: a FROST preprocess (nonce seed) is deterministically regenerated from a DB entry keyed solely by `context` and is never invalidated after `sign` is called, so the same signing nonces can be reused across distinct signing attempts — a class the code comments themselves acknowledge is "explicitly unsafe."

### Finding Description
`preprocess_internal` loads or creates a 32-byte `CachedPreprocess` under `CachedPreprocesses::get(self.txn, &self.context)` and reconstructs the `AlgorithmSignMachine` via `AlgorithmSignMachine::from_cache`, which calls `seeded_preprocess`. `seeded_preprocess` derives all FROST nonces deterministically with `ChaCha20Rng::from_seed(*seed.0)` [1](#0-0) . The cache entry is keyed only by `context` and is never deleted or rotated after `share_internal` consumes it; the same call path in `preprocess_internal` returns an identical machine (identical nonces and commitments) on every invocation for that context [2](#0-1) .

The nonce is then used in `sign` as `actual = base + rho * actual_nonce`, and the share is `sign_share(&view, &Rs, nonces, msg)` [3](#0-2) . Two shares (or two final signatures, which are public on-chain) produced under the same nonce seed but different `msg`/different participant preprocess sets yield the standard Schnorr key-recovery equation `x = (s1 − s2)/(c1 − c2)`, recovering the signer's secret share — and via Lagrange aggregation the group key.

The file's own header admits the safety argument is incomplete: correctness depends on BFT ordering and on "complete rebuilds" re-deciding nonces, while explicitly flagging that the on-chain-preprocess-matches-presumed-preprocess check is a `TODO` and that Processor preprocess handling still needs review [4](#0-3) . A partial rebuild (DB retained, in-memory attempt state lost), a crash between `preprocess` publication and `share` publication that causes `share_internal` to be re-entered with reassembled preprocesses, or any logical flaw letting distinct messages/preprocess sets reach `sign` under one context all produce nonce reuse. `Spec` documentation likewise warns that reusing a cached preprocess enables third-party private key share recovery [5](#0-4) , yet the coordinator persists it indefinitely rather than consuming it once.

### Impact Explanation
Nonce reuse in FROST/Schnorr leaks the signing party's secret share with two shares/signatures over distinct challenges. For the coordinator path this exposes a validator's MuSig key share (root-of-trust material used to confirm DKG results); for the same pattern in processor signing it can expose the threshold group's key, enabling forgery of signatures on arbitrary transactions — total loss of funds controlled by the multisig.

### Likelihood Explanation
An unprivileged party cannot directly pick the context, but they can cause messages/transactions to be signed (in-scope public inputs), and the triggering condition — `sign` executed twice under one `context` with differing inputs — only requires a crash/retry path, partial rebuild, or any logic flaw delivering distinct preprocess sets for the same context. The code itself documents these conditions as unresolved (`TODO` at line 51 and the review note at lines 53–54), so the unsafe state reuse is a real, not hypothetical, exposure. As with the renderdoc flaw, the shared/persisted state was treated as safe-by-construction while its reuse semantics were unsound.

### Recommendation
Delete (or version/rotate) the `CachedPreprocesses` entry atomically when `share_internal`/`sign` first consumes it, so a second `sign` under the same context cannot re-derive the same nonces; alternatively bind the nonce seed to a monotonically increasing attempt counter or to the hash of all received commitments and the message. Implement the noted TODO: verify that the commitments generated from the cached seed match the commitments actually published on-chain before publishing any share, and apply equivalent once-only semantics to Processor preprocess caching.

### Proof of Concept
1. A signing session under `context` C runs `share_internal`, deriving seed `S` via `CachedPreprocesses::get`/`from_cache`, publishing commitments `Com(S)` and emitting share `s1 = k + c1·x` for message `m1`.
2. The node crashes or the attempt is retried after the share is broadcast but before finality; on re-execution `preprocess_internal` finds the still-present `CachedPreprocesses[C]`, regenerates the identical machine (`ChaCha20Rng::from_seed(S)`), and signs message `m2` (or a different preprocess set, changing `rho` and `c`) producing `s2 = k + c2·x`.
3. An observer of the two shares/signatures computes `x = (s1 − s2)·(c1 − c2)⁻¹`, recovering the secret share; for the processor path this yields the threshold group key.

### Citations

**File:** crypto/frost/src/sign.rs (L121-143)
```rust
  fn seeded_preprocess(
    self,
    seed: CachedPreprocess,
  ) -> (AlgorithmSignMachine<C, A>, Preprocess<C, A::Addendum>) {
    let mut params = self.params;

    let mut rng = ChaCha20Rng::from_seed(*seed.0);
    let (nonces, commitments) = Commitments::new::<_>(
      &mut rng,
      params.keys.original_secret_share(),
      &params.algorithm.nonces(),
    );
    let addendum = params.algorithm.preprocess_addendum(&mut rng, &params.keys);

    let preprocess = Preprocess { commitments, addendum };

    // Also obtain entropy to randomly sort the included participants if we need to identify blame
    let mut blame_entropy = [0; 32];
    rng.fill_bytes(&mut blame_entropy);
    (
      AlgorithmSignMachine { params, seed, nonces, preprocess: preprocess.clone(), blame_entropy },
      preprocess,
    )
```

**File:** crypto/frost/src/sign.rs (L386-398)
```rust
    let nonces = self
      .nonces
      .drain(..)
      .enumerate()
      .map(|(n, nonces)| {
        let [base, mut actual] = nonces.0;
        *actual *= our_binding_factors[n];
        *actual += base.deref();
        actual
      })
      .collect::<Vec<_>>();

    let share = self.params.algorithm.sign_share(&view, &Rs, nonces, msg);
```

**File:** coordinator/src/tributary/signing_protocol.rs (L25-55)
```rust
  As for safety, it is explicitly unsafe to reuse nonces across signing sessions. This raises
  concerns regarding our re-execution which is dependent on fixed nonces. Safety is derived from
  the nonces being context-bound under a BFT protocol. The flow is as follows:

  1) Decide the nonce.
  2) Publish the nonces' commitments, receiving everyone elses *and potentially the message to be
     signed*.
  3) Sign and publish the signature share.

  In order for nonce re-use to occur, the received nonce commitments (or the message to be signed)
  would have to be distinct and sign would have to be called again.

  Before we act on any received messages, they're ordered and finalized by a BFT algorithm. The
  only way to operate on distinct received messages would be if:

  1) A logical flaw exists, letting new messages over write prior messages
  2) A reorganization occurred from chain A to chain B, and with it, different messages

  Reorganizations are not supported, as BFT is assumed by the presence of a BFT algorithm. While
  a significant amount of processes may be byzantine, leading to BFT being broken, that still will
  not trigger a reorganization. The only way to move to a distinct chain, with distinct messages,
  would be by rebuilding the local process (this time following chain B). Upon any complete
  rebuild, we'd re-decide nonces, achieving safety. This does set a bound preventing partial
  rebuilds which is accepted.

  Additionally, to ensure a rebuilt service isn't flagged as malicious, we have to check the
  commitments generated from the decided nonces are in fact its commitments on-chain (TODO).

  TODO: We also need to review how we're handling Processor preprocesses and likely implement the
  same on-chain-preprocess-matches-presumed-preprocess check before publishing shares.
*/
```

**File:** coordinator/src/tributary/signing_protocol.rs (L123-147)
```rust
    if CachedPreprocesses::get(self.txn, &self.context).is_none() {
      let (machine, _) =
        AlgorithmMachine::new(algorithm.clone(), keys.clone()).preprocess(&mut OsRng);

      let mut cache = machine.cache();
      assert_eq!(cache.0.len(), 32);
      #[allow(clippy::needless_range_loop)]
      for b in 0 .. 32 {
        cache.0[b] ^= encryption_key_slice[b];
      }

      CachedPreprocesses::set(self.txn, &self.context, &cache.0);
    }

    let cached = CachedPreprocesses::get(self.txn, &self.context).unwrap();
    let mut cached: Zeroizing<[u8; 32]> = Zeroizing::new(cached);
    #[allow(clippy::needless_range_loop)]
    for b in 0 .. 32 {
      cached[b] ^= encryption_key_slice[b];
    }
    encryption_key_slice.zeroize();
    let (machine, preprocess) =
      AlgorithmSignMachine::from_cache(algorithm, keys, CachedPreprocess(cached));

    (machine, preprocess.serialize().try_into().unwrap())
```

**File:** spec/cryptography/FROST.md (L45-62)
```markdown
# Caching

modular-frost supports caching a preprocess. This is done by having all
preprocesses use a seeded RNG. Accordingly, the entire preprocess can be derived
from the RNG seed, making the cache just the seed.

Reusing preprocesses would enable a third-party to recover your private key
share. Accordingly, you MUST not reuse preprocesses. Third-party knowledge of
your preprocess would also enable their recovery of your private key share.
Accordingly, you MUST treat cached preprocesses with the same security as your
private key share.

Since a reused seed will lead to a reused preprocess, seeded RNGs are generally
frowned upon when doing multisignature operations. This isn't an issue as each
new preprocess obtains a fresh seed from the specified RNG. Assuming the
provided RNG isn't generating the same seed multiple times, the only way for
this seeded RNG to fail is if a preprocess is loaded multiple times, which was
already a failure point.
```
