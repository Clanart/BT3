### Title
Cached DKG-confirmation preprocess seed is never cleared and collides across sessions, causing deterministic nonce reuse and eventual validator key recovery - (File: coordinator/src/tributary/signing_protocol.rs)

### Summary
`SigningProtocol::preprocess_internal` stores the FROST preprocess seed in `CachedPreprocesses` keyed only by `context = (b"DkgConfirmer", attempt)`. The entry is written once and **never deleted or rotated**; every subsequent call (`preprocess`, `share`, `complete`, and any later DKG confirmation using the same attempt number) reloads the identical seed and deterministically regenerates the same FROST nonces via `ChaCha20Rng::from_seed` in `seeded_preprocess`. Because the DB key carries no session, genesis, or network identifier, a coordinator participating in multiple validator-set DKGs (each starting at `attempt = 0`) silently reuses the same nonce seed for distinct `set_keys_message` signatures. [1](#0-0) [2](#0-1) [3](#0-2) 

### Finding Description
The analog to the SCTP use-after-free is a stale cached value that survives the logical end of its protocol and is dereferenced again:

- `CachedPreprocesses::set` writes the XOR-encrypted ChaCha20 seed at lines 123–134 only when the key is absent. No code path ever calls a delete/clear on `CachedPreprocesses` (the symbol appears only in this file).
- `from_cache` → `seeded_preprocess` regenerates `nonces` and `commitments` purely from the seed (`crypto/frost/src/sign.rs:121-144`), so reloading the same seed yields the same nonce pair `(d, e)`.
- `DkgConfirmer::signing_protocol` builds the context as `(b"DkgConfirmer", self.attempt)` — no session/genesis/set binding. `dkg_confirmation_nonces` (`handle.rs:42`), `generated_key_pair` (`handle.rs:57-59`), and `DkgConfirmed` completion (`handle.rs:533-535`) all re-enter `preprocess_internal`/`share_internal` for that same context.
- The file's own safety argument (lines 25–48) only justifies reuse *within* a single attempt under BFT-fixed messages. It does not account for the cache persisting across distinct DKG sessions, where both the message (`set_keys_message` over a new `KeyPair`/set) and the MuSig participant list differ while the seed — and therefore the raw nonces — stay identical.

Each FROST signature share has the form `s = d + ρ·e + λ·a·c`. Reused `(d, e)` across `k` distinct sessions yields `k` linear equations in the three unknowns `d`, `e`, and the signer's secret share `a` (which is a fixed function of the validator's static key). Two sessions give an underdetermined system; three or more DKG confirmations at the same `attempt` index — the normal case, since most DKGs succeed on attempt 0 — make the system solvable. [4](#0-3) [5](#0-4) [6](#0-5) 

### Impact Explanation
The shares are published in plaintext in `Transaction::DkgConfirmed` on the tributary, so any observer — including an unprivileged party reading tributary blocks — collects the equations. Recovering `a` recovers the validator's MuSig secret share / Ristretto key, enabling forgery of DKG confirmation shares, `set_keys` confirmations for attacker-chosen key pairs, `RemoveParticipantDueToDkg` votes, and any other tributary transaction signed by that validator. This is full key-share recovery, the exact consequence `CachedPreprocess`'s own documentation warns about ("reuse will enable third-party recovery of your private key share", `crypto/frost/src/sign.rs:85-87, 209-213`).

### Likelihood Explanation
Triggering requires no attacker action beyond waiting for — or nudging — repeated DKG confirmations at a repeated attempt index. Session rotation performs a fresh DKG per set; a long-lived validator key participates in many of these, almost always at `attempt = 0`. The cache entry persists indefinitely in the coordinator DB, so reuse is automatic rather than exceptional. The only mitigating factor is that ≥3 colliding sessions are needed for a fully determined system (or 2 if the attacker can additionally influence a preprocess set so `ρ` collides), which keeps this out of the single-shot category but squarely in realistic reach. Severity: **High**.

### Recommendation
- Delete the `CachedPreprocesses` entry after first consumption (e.g., in `share_internal`/`complete`), matching the `from_cache` contract that "the preprocess must be deleted so it's never reused" (`crypto/frost/src/sign.rs:216-219`).
- Bind the DB context to the specific signing instance: include `spec.genesis()` (or the full `ValidatorSet` and network) in the `context` tuple so attempt numbers from distinct sessions can never collide.
- Optionally assert at `share`/`complete` time that the regenerated preprocess commitments match the ones already published on-chain for this attempt, as the file's own TODO (line 51) suggests.

### Proof of Concept
1. Coordinator C with validator key `k` participates in DKG confirmation for set/session A at `attempt = 0`. `preprocess_internal` stores seed `S` under context `(b"DkgConfirmer", 0)`; share `s_A = d + ρ_A·e + λ_A·a·c_A` is published in `Transaction::DkgConfirmed`.
2. Sessions B and C later run DKGs that also reach confirmation at `attempt = 0` (different genesis/set/key_pair). `CachedPreprocesses::get` returns `S` — the `is_none()` branch is skipped — so `seeded_preprocess` regenerates identical `(d, e)` while `ρ`, `λ`, `c`, and the message differ.
3. An observer collects `s_A`, `s_B`, `s_C` from the public tributary transactions, knows all public `ρ`, `λ`, `c` values, and solves the 3×3 linear system for `d`, `e`, `a`. From `a` and the known MuSig coefficient, the validator's secret key `k` is recovered.

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

**File:** coordinator/src/tributary/signing_protocol.rs (L274-277)
```rust
  fn signing_protocol(&mut self) -> DkgConfirmerSigningProtocol<'_, T> {
    let context = (b"DkgConfirmer", self.attempt);
    SigningProtocol { key: self.key, spec: self.spec, txn: self.txn, context }
  }
```

**File:** crypto/frost/src/sign.rs (L121-144)
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

**File:** coordinator/src/tributary/handle.rs (L526-535)
```rust
            let preprocesses = ConfirmationNonces::get(self.txn, genesis, attempt).unwrap();
            // TODO: This can technically happen under very very very specific timing as the txn
            // put happens before DkgConfirmed, yet the txn commit isn't guaranteed to
            let key_pair = DkgKeyPair::get(self.txn, genesis, attempt).expect(
              "in DkgConfirmed handling, which happens after everyone \
              (including us) fires DkgConfirmed, yet no confirming key pair",
            );
            let mut confirmer = DkgConfirmer::new(self.our_key, self.spec, self.txn, attempt)
              .expect("confirming DKG for unrecognized attempt");
            let sig = match confirmer.complete(preprocesses, &key_pair, shares) {
```
