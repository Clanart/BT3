### Title
Cached FROST/MuSig preprocess namespaced only by `(context)` collapses all messages of a DKG attempt into a single nonce — key-share recovery on any second `share` call - (File: coordinator/src/tributary/signing_protocol.rs)

### Summary
`SigningProtocol::preprocess_internal` generates a FROST preprocess once and stores its seed in `CachedPreprocesses`, keyed solely by `self.context` (`(b"DkgConfirmer", attempt)`). Every subsequent call — `share` and any later call under the same attempt — rebuilds the signing machine from that same seed via `AlgorithmSignMachine::from_cache`, producing the identical nonce commitments regardless of the message or participant set. If `share` is ever executed for a second distinct `set_keys_message` (different `key_pair`) under the same attempt, the validator emits two Schnorr shares sharing one nonce, enabling recovery of its private key by any observer of the tributary.

### Finding Description
The bug-class analog to the httpd "directory namespace collapse" is a *nonce namespace collapse*: the cache key collapses every signing session of an attempt into one entry.

- `CachedPreprocesses` is keyed by `context` alone — not by message, `key_pair`, `removed` set, or participant list [1](#0-0) .
- On the first call the seed is generated, XOR-encrypted, and persisted; on every later call the same seed is decrypted and fed to `from_cache`, which deterministically reproduces the same preprocess/nonces [2](#0-1) . Cached preprocesses are seeds and reuse leaks the private key share [3](#0-2) [4](#0-3) .
- `share_internal` then signs `msg` — which is `set_keys_message(set, removed, key_pair)`, i.e., a function of attacker-influenceable `key_pair` — under that reused nonce [5](#0-4) .
- `generated_key_pair` unconditionally overwrites `DkgKeyPair` per attempt and calls `share` — there is no guard preventing a second call with a different `key_pair` for the same `attempt` [6](#0-5) .
- The header comment's safety argument ("upon any complete rebuild, we'd re-decide nonces") is contradicted by the DB-persisted cache: re-execution reloads the same seed rather than re-deciding [7](#0-6) .

### Impact Explanation
FROST signature shares are `s = d + b·e + λ·x·c` (binomial nonce `d + b·e` is fixed by the cached seed). Two shares `s1`, `s2` published on the tributary over the same nonce with different binding factors/challenges (different `msg` ⇒ different `rho` transcript and `c`) allow solving for `λ·x`, and since the MuSig `Interpolation::Constant(binding_factors)` is public, recovery of the validator's raw private key. That key is the root of trust for `set_keys` — an attacker recovers a validator's signing key and can forge `DkgConfirmer` shares/signatures authorizing arbitrary key rotations [8](#0-7) [9](#0-8) .

### Likelihood Explanation
Requires `generated_key_pair`/`share` to execute more than once under one attempt with a differing `key_pair` (or a differing `removed` set changing `msg`). Nothing in `generated_key_pair` guards against a repeat (`DkgKeyPair::set` blindly overwrites), and `share`/`complete` are derived from processor-reported data plus on-chain accumulated preprocesses, so any duplicate or differing key-pair report — or any re-execution path where the finalized data diverges while the DB cache persists — triggers reuse. The nonce is *deterministically* identical (`from_cache` re-derives it), so no probabilistic precondition exists. Reachability depends on the processor-side retry/report semantics which I could not fully verify; the coordinator-side collapse itself is unconditional.

### Recommendation
Include the signed message (or `key_pair` hash) and the participant/`removed` set in the `CachedPreprocesses` key, or store a per-attempt "signed-message" record and reject any `share`/`complete` request whose `set_keys_message` differs from the first signed value for that context. At minimum, guard `generated_key_pair` against a second differing `KeyPair` per attempt instead of overwriting `DkgKeyPair` unconditionally.

### Proof of Concept
1. Attempt `a` of `Topic::Dkg` proceeds; coordinator caches seed `k` under `CachedPreprocesses[(b"DkgConfirmer", a)]` and publishes nonce commitments in `DkgShares.confirmation_nonces`.
2. `generated_key_pair(txn, key, spec, kp1, a)` → `share` emits `s1 = k_derived + λ·x·c1` where `c1` binds `set_keys_message(set, removed, kp1)`.
3. A second call `generated_key_pair(txn, key, spec, kp2, a)` with `kp2 != kp1` (possible since `DkgKeyPair::set` overwrites at handle.rs:54) → `share` emits `s2 = k_derived + λ·x·c2` with identical nonce commitments but `c2 != c1`.
4. Observer reads both shares from the tributary, computes `λ·x = (s1 − s2)/(c1 − c2)`, divides by the public MuSig binding factor `λ`, recovering the validator's `key` — then forges `set_keys` authorizations.

### Uncertainty note
I could not fully trace whether the processor ever legitimately emits a second `generated_key_pair` per attempt, nor whether the DB outlives the "rebuild" scenario the header relies on. If the coordinator guarantees exactly one `key_pair` per attempt and BFT finality holds, the trigger narrows to retry/re-execution edge cases — but the namespace collapse (cache key excludes message and participants) is structurally present and the documented safety invariant is violated by the persistent cache.

### Citations

**File:** coordinator/src/tributary/signing_protocol.rs (L25-48)
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
```

**File:** coordinator/src/tributary/signing_protocol.rs (L86-90)
```rust
create_db!(
  SigningProtocolDb {
    CachedPreprocesses: (context: &impl Encode) -> [u8; 32]
  }
);
```

**File:** coordinator/src/tributary/signing_protocol.rs (L123-145)
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
```

**File:** coordinator/src/tributary/signing_protocol.rs (L288-302)
```rust
  fn share_internal(
    &mut self,
    preprocesses: HashMap<Participant, Vec<u8>>,
    key_pair: &KeyPair,
  ) -> Result<(AlgorithmSignatureMachine<Ristretto, Schnorrkel>, [u8; 32]), Participant> {
    let participants = self.spec.validators().iter().map(|val| val.0).collect::<Vec<_>>();
    let preprocesses =
      threshold_i_map_to_keys_and_musig_i_map(self.spec, &self.removed, self.key, preprocesses).1;
    let msg = set_keys_message(
      &self.spec.set(),
      &self.removed.iter().map(|key| Public::from(key.to_bytes())).collect::<Vec<_>>(),
      key_pair,
    );
    self.signing_protocol().share_internal(&participants, preprocesses, &msg)
  }
```

**File:** crypto/frost/src/sign.rs (L83-92)
```rust
/// A cached preprocess.
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

**File:** crypto/frost/src/sign.rs (L209-224)
```rust
  /// Cache this preprocess for usage later.
  ///
  /// This cached preprocess MUST only be used once. Reuse of it enables recovery of your private
  /// key share. Third-party recovery of a cached preprocess also enables recovery of your private
  /// key share, so this MUST be treated with the same security as your private key share.
  fn cache(self) -> CachedPreprocess;

  /// Create a sign machine from a cached preprocess.
  ///
  /// After this, the preprocess must be deleted so it's never reused. Any reuse will presumably
  /// cause the signer to leak their secret share.
  fn from_cache(
    params: Self::Params,
    keys: Self::Keys,
    cache: CachedPreprocess,
  ) -> (Self, Self::Preprocess);
```

**File:** crypto/frost/src/sign.rs (L361-380)
```rust
      // Re-format into the FROST-expected rho transcript
      let mut rho_transcript = A::Transcript::new(b"FROST_rho");
      rho_transcript.append_message(b"group_key", self.params.keys.group_key().to_bytes());
      rho_transcript.append_message(b"message", C::hash_msg(msg));
      rho_transcript.append_message(
        b"preprocesses",
        C::hash_commitments(self.params.algorithm.transcript().challenge(b"preprocesses").as_ref()),
      );

      // Generate the per-signer binding factors
      B.calculate_binding_factors(&rho_transcript);

      // Merge the rho transcript back into the global one to ensure its advanced, while
      // simultaneously committing to everything
      self
        .params
        .algorithm
        .transcript()
        .append_message(b"rho_transcript", rho_transcript.challenge(b"merge"));
    }
```

**File:** coordinator/src/tributary/handle.rs (L47-60)
```rust
pub fn generated_key_pair<D: Db>(
  txn: &mut D::Transaction<'_>,
  key: &Zeroizing<<Ristretto as Ciphersuite>::F>,
  spec: &TributarySpec,
  key_pair: &KeyPair,
  attempt: u32,
) -> Result<[u8; 32], Participant> {
  DkgKeyPair::set(txn, spec.genesis(), attempt, key_pair);
  KeyToDkgAttempt::set(txn, key_pair.0 .0, &attempt);
  let preprocesses = ConfirmationNonces::get(txn, spec.genesis(), attempt).unwrap();
  DkgConfirmer::new(key, spec, txn, attempt)
    .expect("claiming to have generated a key pair for an unrecognized attempt")
    .share(preprocesses, key_pair)
}
```

**File:** crypto/dkg/musig/src/lib.rs (L155-161)
```rust
  ThresholdKeys::new(
    params,
    Interpolation::Constant(binding_factors),
    private_key,
    verification_shares,
  )
  .map_err(MusigError::DkgError)
```
