### Title
Deterministic CachedPreprocess reuse across `share`/`complete` re-executions enables nonce reuse and validator key-share recovery - (File: coordinator/src/tributary/signing_protocol.rs)

### Summary
The analog of CVE-2023-0135's use-after-free class is *reuse of state that must be consumed exactly once*: `SigningProtocol::preprocess_internal` deterministically regenerates a FROST `AlgorithmSignMachine` from a seed cached in the database (`CachedPreprocesses`, keyed only by `(&'static [u8; 12], u32)` context). The file's own header admits nonce reuse is unsafe and that safety depends on "the received nonce commitments (or the message to be signed)" being identical on every call — with an explicit `TODO` noting the on-chain-preprocess-matches-presumed-preprocess check is not implemented. `DkgConfirmer::share` and `DkgConfirmer::complete` each invoke `share_internal`, which re-derives the *same* nonce from the same cached seed, while the preprocesses map and `key_pair` (and therefore `msg` via `set_keys_message`) come from external, finalizable-but-distinct inputs.

### Finding Description
- `preprocess_internal` loads or creates a 32-byte seed under `CachedPreprocesses::get(self.txn, &self.context)` and rebuilds an identical machine via `AlgorithmSignMachine::from_cache(algorithm, keys, CachedPreprocess(cached))` — the same nonce `d` every time for a given `(b"DkgConfirmer", attempt)` context. `coordinator/src/tributary/signing_protocol.rs:123-147` [1](#0-0) 
- `share()` calls `share_internal`, which signs `msg` derived from `key_pair` and a caller-supplied `preprocesses` map. `complete()` then calls `share_internal` **again** with a potentially different `preprocesses` map/`key_pair`, producing a second signature share under the same nonce. `coordinator/src/tributary/signing_protocol.rs:288-327` [2](#0-1) 
- The binding factors `rho` are computed over `group_key`, `hash_msg(msg)`, and a challenge over all participant preprocesses (`crypto/frost/src/sign.rs:361-379`), so any change to the preprocess set, the participant set composition (via `threshold_i_map_to_keys_and_musig_i_map` remapping), or `key_pair` yields a different effective aggregate nonce `D + Σρ·B` and a different challenge `c` — while our `d` is fixed. [3](#0-2) 
- The header explicitly documents that safety rests on messages never being distinct, and marks the required check as `TODO` / not implemented (lines 34-54). [4](#0-3) 
- FROST itself documents that a cached preprocess "MUST only be used once. Reuse will enable third-party recovery of your private key share." `crypto/frost/src/sign.rs:84-87` [5](#0-4) 

### Impact Explanation
Two shares `s1 = d + c1·x` and `s2 = d + c2·x` under the same nonce `d` with distinct challenges yield `x = (s1 − s2)/(c1 − c2)` — recovery of the coordinator's MuSig secret share, i.e. the validator's root-of-trust signing key. `s1` is the published share from `share()`; `s2` is recoverable from the aggregate signature returned by `complete()` minus the other participants' shares, which the attacker supplied and therefore knows. This converts a single reused cached seed into full compromise of a validator key — concrete key-share recovery, matching the "unintended use of stale state" class of the original UAF (state dereferenced after its single-use lifetime was presumed over).

### Likelihood Explanation
Reachable by an unprivileged participant: the `preprocesses` and `shares` maps are deserialized from untrusted bytes (`read_preprocess`, `read_share`) delivered through tributary transactions, and `key_pair` selects the signed message. A distinct `complete` call — different participant subset, mutated addendum/preprocess bytes, or a second confirmation attempt with the same `attempt` context but different `KeyPair` — forces `share_internal` to re-sign under the identical nonce. No validator compromise, collusion, or BFT break is required: only the ability to have two distinct finalizable input sets reach `share`/`complete` under one context, which the missing TODO check fails to prevent. Severity: High.

### Recommendation
- Before signing in `share_internal`, verify the locally regenerated preprocess commitments equal the ones already published on-chain for this context (the explicit TODO at line 51), and abort if they differ.
- Bind the cached seed to the full signing context: mix `msg` (or its hash) and the sorted participant set into `CachedPreprocesses` key or into the seed, so distinct inputs can never regenerate the same nonce.
- Store a "consumed" flag per context after the first successful `sign`, refusing any second `share_internal` for the same `(context, attempt)` rather than re-deriving the nonce.

### Proof of Concept
1. Context `ctx = (b"DkgConfirmer", attempt)`; seed `k` stored encrypted under `CachedPreprocesses`.
2. Call `share(preprocesses_A, key_pair_A)`: internally `from_cache(alg, keys, k)` → nonce `d`; publishes `s1 = d + c1·x` where `c1` derives from `rho_transcript(group_key, hash_msg(set_keys_message(key_pair_A)), commitments_A)`.
3. Call `complete(preprocesses_B, key_pair_B, shares_B)` with `preprocesses_B ≠ preprocesses_A` (e.g., drop one participant so `threshold_i_map_to_keys_and_musig_i_map` remaps indices, or alter one preprocess's addendum bytes): `share_internal` regenerates the same `d`, signs `msg_B`/`rho_B` → share `s2 = d + c2·x` folded into aggregate signature `σ`.
4. Attacker computes `s2 = σ.s − Σ_{p≠us} share_p` (all shares in `shares_B` are attacker-known), then `x = (s1 − s2)·(c1 − c2)⁻¹`, recovering the validator's MuSig secret share. All inputs (`preprocesses_B`, `key_pair_B`, `shares_B`) are public, attacker-influenceable bytes; no check in `share_internal`/`complete_internal` compares them against the first round's values.

### Citations

**File:** coordinator/src/tributary/signing_protocol.rs (L34-54)
```rust
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

**File:** coordinator/src/tributary/signing_protocol.rs (L288-327)
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
  // Get the share for this confirmation, if the preprocesses are valid.
  pub(crate) fn share(
    &mut self,
    preprocesses: HashMap<Participant, Vec<u8>>,
    key_pair: &KeyPair,
  ) -> Result<[u8; 32], Participant> {
    self.share_internal(preprocesses, key_pair).map(|(_, share)| share)
  }

  pub(crate) fn complete(
    &mut self,
    preprocesses: HashMap<Participant, Vec<u8>>,
    key_pair: &KeyPair,
    shares: HashMap<Participant, Vec<u8>>,
  ) -> Result<[u8; 64], Participant> {
    let shares =
      threshold_i_map_to_keys_and_musig_i_map(self.spec, &self.removed, self.key, shares).1;

    let machine = self
      .share_internal(preprocesses, key_pair)
      .expect("trying to complete a machine which failed to preprocess")
      .0;

    DkgConfirmerSigningProtocol::<'_, T>::complete_internal(machine, shares)
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

**File:** crypto/frost/src/sign.rs (L361-379)
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
```
