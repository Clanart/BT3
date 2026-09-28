### Title
Cached FROST preprocess seed is reused across `sign()` calls within a DKG-confirmation attempt, leaking the validator's private key share — (File: coordinator/src/tributary/signing_protocol.rs)

### Summary
`SigningProtocol::preprocess_internal` persists a deterministic preprocess seed in `CachedPreprocesses` keyed by `context`, never deletes it, and regenerates identical nonces every time it runs. Both `DkgConfirmer::share` and `DkgConfirmer::complete` drive `share_internal` → `preprocess_internal` → `AlgorithmSignMachine::sign` with externally-supplied preprocess maps for the same `(b"DkgConfirmer", attempt)` context. Two `sign()` executions under the same seed but different preprocess sets/messages reuse the same FROST nonce — the exact "use after release" class from the report — enabling algebraic recovery of the MuSig validator key share.

### Finding Description
`preprocess_internal` derives the sign machine from a 32-byte seed stored in the DB: [1](#0-0) 

The cache is *set if absent* and then always read back; nothing ever removes it after a signing round consumes it. `AlgorithmSignMachine::from_cache` → `seeded_preprocess` regenerates the identical `nonces` and `commitments` from `ChaCha20Rng::from_seed(*seed.0)`: [2](#0-1) 

`share_internal` calls `preprocess_internal` again, so every call to `DkgConfirmer::share` and to `complete` (which internally re-runs `share_internal`) produces a signature share with the *same* secret nonce `d + b·e`: [3](#0-2) 

The inputs to `share`/`complete` — `preprocesses: HashMap<Participant, Vec<u8>>` — arrive from tributary messages (public inputs an unprivileged validator controls). If any two executions for the same `attempt` see different preprocess sets (different participants included, or an added/removed entry), `sign()` computes different binding factors `b_i`, different group nonces, and different challenges `c_i`, while reusing the identical local nonce. The FROST spec and `CachedPreprocess` docs explicitly state reuse enables third-party recovery of the private key share: [4](#0-3) 

The file's own header concedes this is the known hazard — "it is explicitly unsafe to reuse nonces across signing sessions" — and relies on BFT ordering plus a `TODO` ("check the commitments generated from the decided nonces are in fact its commitments on-chain") rather than deleting consumed nonces. The race-condition analog (consumed resource reused) is realized here because the consumed seed is retained indefinitely and reused on every re-execution. [5](#0-4) 

### Impact Explanation
Each emitted share is `s = d + b·e + c·λ_i·share_i`. Two shares produced under the same nonce seed but different preprocess sets yield different `b`, `c`, `λ` and potentially different `msg` (`set_keys_message` changes with `removed`/`key_pair`), giving two linear equations over `{d, e, share_i}`. With binding factors and challenges known publicly, an observer of the published shares recovers the validator's MuSig secret share — the private key underlying the validator-set confirmation signature. With enough co-signers similarly exposed (every validator's preprocess is seeded deterministically the same way), the threshold key confirming DKG results on-chain is compromised, permitting forged `set_keys` confirmations.

### Likelihood Explanation
`share()` and `complete()` are separate externally-driven entry points; `complete()` re-executes `share_internal` unconditionally, and nothing enforces that the preprocess map at `complete` time equals the one at `share` time, nor that `share` runs only once per attempt. An unprivileged validator influences the preprocess map by broadcasting (or withholding/altering) its own preprocess bytes — inputs they legitimately supply. Re-execution after reboot, RPC retry, or a second finalized batch of preprocesses under the same `attempt` suffices to trigger the second `sign()` on reused nonces. No collusion threshold, malicious node, or leaked key is required — only the deterministic nonce reuse the code itself guarantees.

### Recommendation
Delete `CachedPreprocesses` entry upon first consumption in `share_internal` (or refuse to `sign()` twice per context), and implement the noted TODO: before publishing a share, verify the regenerated commitments match what was actually published/finalized for this attempt, aborting rather than reusing nonces when they diverge.

### Proof of Concept
1. In attempt `N`, coordinator calls `DkgConfirmer::share(preprocesses_A, key_pair)` → publishes share `s₁` computed with seed-derived nonce `d + b_A·e` under challenge `c₁` over `set_keys_message` `m₁`.
2. Any subsequent call — `share` retry, or `complete(preprocesses_B, key_pair, shares)` where `preprocesses_B ≠ preprocesses_A` (e.g., an extra validator's preprocess arrives) — reloads the identical `CachedPreprocesses[(b"DkgConfirmer", N)]` seed, regenerates `d, e`, and emits `s₂ = d + b_B·e + c₂·λ₂·share_i`.
3. `s₁ - s₂ = (b_A - b_B)·e + (c₁λ₁ - c₂λ₂)·share_i`; combined with the commitment equations, the secret share `share_i` is solved linearly. The nonce reuse is guaranteed by `from_seed(*seed.0)` in `seeded_preprocess` and the never-cleared DB row in `preprocess_internal`.

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

**File:** coordinator/src/tributary/signing_protocol.rs (L150-180)
```rust
  fn share_internal(
    &mut self,
    participants: &[<Ristretto as Ciphersuite>::G],
    mut serialized_preprocesses: HashMap<Participant, Vec<u8>>,
    msg: &[u8],
  ) -> Result<(AlgorithmSignatureMachine<Ristretto, Schnorrkel>, [u8; 32]), Participant> {
    let machine = self.preprocess_internal(participants).0;

    let mut participants = serialized_preprocesses.keys().copied().collect::<Vec<_>>();
    participants.sort();
    let mut preprocesses = HashMap::new();
    for participant in participants {
      preprocesses.insert(
        participant,
        machine
          .read_preprocess(&mut serialized_preprocesses.remove(&participant).unwrap().as_slice())
          .map_err(|_| participant)?,
      );
    }

    let (machine, share) = machine.sign(preprocesses, msg).map_err(|e| match e {
      FrostError::InternalError(e) => unreachable!("FrostError::InternalError {e}"),
      FrostError::InvalidParticipant(_, _) |
      FrostError::InvalidSigningSet(_) |
      FrostError::InvalidParticipantQuantity(_, _) |
      FrostError::DuplicatedParticipant(_) |
      FrostError::MissingParticipant(_) => unreachable!("{e:?}"),
      FrostError::InvalidPreprocess(p) | FrostError::InvalidShare(p) => p,
    })?;

    Ok((machine, share.serialize().try_into().unwrap()))
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

**File:** crypto/frost/src/sign.rs (L121-145)
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
  }

```
