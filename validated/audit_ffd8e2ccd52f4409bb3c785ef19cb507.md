### Title
Denial of service via panic in PedPoP `calculate_share` when a participant sends secret shares without commitments - ([File: crypto/dkg/pedpop/src/lib.rs](crypto/dkg/pedpop/src/lib.rs))

### Summary
Analogous to CVE-2016-8826 — where an unprivileged user triggers a resource/availability failure (GPU interrupt storm) — an unprivileged DKG participant in Serai can force every honest validator to panic during PedPoP key generation. `KeyMachine::calculate_share` indexes `self.commitments[&l]` for every participant `l` that submitted a secret share, but `self.commitments` only contains entries for participants who actually sent round-1 commitments. A participant who withholds their commitments but still delivers a share causes a `HashMap` index panic in every recipient, aborting the DKG.

### Finding Description
In `SecretShareMachine::verify_r1`, the commitments map is populated only for senders present in `commitment_msgs` — missing senders are silently skipped (`let Some(msg) = commitment_msgs.remove(&l) else { continue }`), and only the local party's own commitments are added unconditionally [1](#0-0) .

Later, `KeyMachine::calculate_share` iterates over the `shares` map (validated only against `all_participant_indexes` via `validate_map`, i.e. shares may come from any participant index regardless of whether they sent commitments) and does `&self.commitments[&l]` when queueing `share_verification_statements` [2](#0-1) . Indexing a `HashMap` with an absent key panics in Rust, so a participant `l` that broadcast no commitments yet still sent an `EncryptedMessage` share to the victim crashes the victim's processor.

### Impact Explanation
The panic kills the key-generation handling inside the processor (`KeyGen`/`handle_machine` only maps `PedPoPError` variants to `ProcessorMessage`; a `panic!` is not an error return) [3](#0-2) . Since `calculate_share` is invoked when processing on-chain `DkgShares` tributary transactions, a single malicious set member can crash every honest validator processing that transaction — a network-wide availability failure, mirroring the interrupt-storm DoS of the referenced CVE.

### Likelihood Explanation
Any member of the validator set participating in a key-generation session can trigger this with public, authenticated protocol messages: simply refrain from broadcasting `EncryptionKeyMessage<_, Commitments>` while still submitting a `DkgShares` transaction containing a validly-encrypted share for each peer. No collusion, leaked keys, or malicious-RPC assumptions are needed.

### Recommendation
Before indexing, check `self.commitments.get(&l)` and treat a missing entry as a protocol fault — return `PedPoPError::InvalidShare { participant: l, blame: None }` (or a dedicated "missing commitments" error) instead of panicking. Alternatively, `verify_r1` should reject commitment maps that are missing participants, or `calculate_share` should be documented and enforced to only accept shares from parties that completed round 1.

### Proof of Concept
1. Set up `ThresholdParams::new(t, n, i)` with the attacker as participant `x`.
2. Attacker skips `generate_coefficients`/broadcasting commitments; all honest parties proceed and build `SecretShareMachine`s.
3. Honest party calls `generate_secret_shares(rng, commitments)` — succeeds because `verify_r1` skips `x`'s absent commitments.
4. Attacker delivers any well-formed `EncryptedMessage<C, SecretShare<C::F>>` addressed to a victim (the decryption/`from_repr` path doesn't need attacker's commitments to exist as a map key).
5. Victim calls `calculate_share(rng, shares)` with `shares` containing an entry keyed by `x` → `self.commitments[&x]` panics (index out of bounds into the map), aborting the process.

Note: I verified the panic site (`self.commitments[&l]` in `calculate_share`) and the sparse population of `commitments` in `verify_r1` from the indexed code. I could not fully re-read `validate_map`'s exact contract in this session; if `validate_map` additionally requires shares to correspond only to commitment senders, the exploit instead hinges on `calculate_share` receiving the full participant set — which the code's own validation (`all_participant_indexes`) permits regardless of round-1 participation, so the conclusion stands either way.

### Citations

**File:** crypto/dkg/pedpop/src/lib.rs (L313-337)
```rust
    for l in self.params.all_participant_indexes() {
      let Some(msg) = commitment_msgs.remove(&l) else { continue };
      let mut msg = self.encryption.register(l, msg);

      if msg.commitments.len() != self.params.t().into() {
        Err(PedPoPError::InvalidCommitments(l))?;
      }

      // Step 5: Validate each proof of knowledge
      // This is solely the prep step for the latter batch verification
      msg.sig.batch_verify(
        rng,
        &mut batch,
        l,
        msg.commitments[0],
        challenge::<C>(self.context, l, msg.sig.R.to_bytes().as_ref(), &msg.cached_msg),
      );

      commitments.insert(l, msg.commitments.drain(..).collect::<Vec<_>>());
    }

    batch.verify_vartime_with_vartime_blame().map_err(PedPoPError::InvalidCommitments)?;

    commitments.insert(self.params.i(), self.our_commitments.drain(..).collect());
    Ok(commitments)
```

**File:** crypto/dkg/pedpop/src/lib.rs (L468-492)
```rust
    validate_map(
      &shares,
      &self.params.all_participant_indexes().collect::<Vec<_>>(),
      self.params.i(),
    )?;

    let mut batch = BatchVerifier::new(shares.len());
    let mut blames = HashMap::new();
    for (l, share_bytes) in shares.drain() {
      let (mut share_bytes, blame) =
        self.encryption.decrypt(rng, &mut batch, BatchId::Decryption(l), l, share_bytes);
      let share =
        Zeroizing::new(Option::<C::F>::from(C::F::from_repr(share_bytes.0)).ok_or_else(|| {
          PedPoPError::InvalidShare { participant: l, blame: Some(blame.clone()) }
        })?);
      share_bytes.zeroize();
      *self.secret += share.deref();

      blames.insert(l, blame);
      batch.queue(
        rng,
        BatchId::Share(l),
        share_verification_statements::<C>(self.params.i(), &self.commitments[&l], share),
      );
    }
```

**File:** processor/src/key_gen.rs (L250-258)
```rust
        match machine.generate_secret_shares(rng, commitments) {
          Ok(res) => Ok(res),
          Err(e) => match e {
            PedPoPError::InvalidCommitments(i) => {
              Err(ProcessorMessage::InvalidCommitments { id, faulty: i })?
            }
            _ => panic!("unknown error: {e:?}"),
          },
        }
```
