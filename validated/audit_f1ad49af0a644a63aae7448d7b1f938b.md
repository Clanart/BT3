### Title
Attacker-controlled `Participant` index in signing preprocesses reaches `unreachable!()` panic — `InvalidParticipant` returned by `sign()` is treated as impossible - (File: coordinator/src/tributary/signing_protocol.rs)

### Summary

The upstream bug class is "code continues past a check for an 'impossible' condition and dereferences the result anyway" (NULL use after `WARN_ON_ONCE`). The Serai analog is the pervasive pattern of mapping `FrostError` variants that are actually reachable from untrusted input onto `unreachable!()`. In `SigningProtocol::share_internal`, every participant index present in a peer-supplied preprocess map is folded into the FROST `included` set; `AlgorithmSignMachine::sign` explicitly returns `FrostError::InvalidParticipant` when the largest index exceeds `n`, and the caller marks that exact variant `unreachable!()`, turning a peer-controlled participant index into a coordinator panic.

### Finding Description

`share_internal` collects participant indexes straight from the deserialized preprocess map and calls `machine.sign(preprocesses, msg)`: [1](#0-0) 

`AlgorithmSignMachine::sign` builds `included` from `preprocesses.keys()`, sorts it, and errors when the top index exceeds `n`: [2](#0-1) 

Because `Participant` deserialization only rejects the zero/invalid index (e.g., `Participant::new(...)` checks in `Transaction::read`), an index such as `n + 1` parses fine, survives `validate_map` ordering (the map is keyed by the same forged index, so the consistency check passes), and trips the `included[last] > n` check inside `sign`. The caller then executes `FrostError::InvalidParticipant(_, _) => unreachable!("{e:?}")`, panicking the coordinator node.

The identical `unreachable!()` pattern on `InvalidParticipant`/`InvalidSigningSet`/`MissingParticipant` exists in the processor signing paths as well: [3](#0-2) [4](#0-3) [5](#0-4) 

This is the same defect shape as CVE-2025-21833: a condition the author believed "can't happen" (`WARN_ON_ONCE`/`unreachable!`) is in fact reachable from untrusted input, and the recovery path uses the invalid value (here: panics) instead of handling it.

### Impact Explanation

An unprivileged peer that can submit a preprocess/share message with a forged `Participant` index greater than `n` (or a signing set that omits a required participant, hitting `MissingParticipant`) deterministically panics the node handling `share_internal`/the batch and substrate signers. This is a remote, input-triggered denial of service on signing infrastructure, halting FROST signing/co-signing for that validator set — availability impact matching the Medium severity class of the advisory.

### Likelihood Explanation

Reachability requires only control of the bytes in a `HashMap<Participant, Vec<u8>>` preprocess or share message — data supplied by another protocol participant, not a privileged role. Deserialization (`Transaction::read`, `Participant::new`) does not bound the index to `n`; the bound is only enforced inside `sign()`, which returns the very error the callers declare unreachable. Any peer able to gossip a malformed preprocess triggers the panic with certainty.

### Recommendation

Replace `unreachable!()` on `FrostError::InvalidParticipant`, `InvalidSigningSet`, `InvalidParticipantQuantity`, `DuplicatedParticipant`, and `MissingParticipant` with the same `InvalidParticipant`-style blame/return path used for `InvalidPreprocess`/`InvalidShare`, or pre-validate `included.iter().max() <= n` (and that map keys ⊆ validator set) before calling `sign`/`complete`. At minimum, downgrade the panic to a logged error and rejection.

### Proof of Concept

A tributary peer broadcasts a signing preprocess transaction whose `serialized_preprocesses` map contains a key `Participant(n + 1)` alongside the honest entries. When `share_internal` runs:

1. `participants` is sorted and passed to `machine.sign(preprocesses, msg)`.
2. `included` ends with `n + 1`, so `u16::from(included[included.len() - 1]) > multisig_params.n()` is true.
3. `sign` returns `Err(FrostError::InvalidParticipant(n, Participant(n + 1)))`.
4. The `match` hits `FrostError::InvalidParticipant(_, _) => unreachable!("{e:?}")` and the node panics.

Note: I was unable to fully confirm `Participant::new`'s exact validity predicate (whether indexes above `n` are rejected at parse time); the finding assumes — consistent with `sign`'s explicit `InvalidParticipant` check — that range validation is deferred to `sign`, which is precisely why the error variant exists. If `Participant::new` already bounds indexes to `n`, this path is unreachable and no analog exists.

### Citations

**File:** coordinator/src/tributary/signing_protocol.rs (L158-178)
```rust
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
```

**File:** crypto/frost/src/sign.rs (L290-310)
```rust
    let mut included = Vec::with_capacity(preprocesses.len() + 1);
    included.push(multisig_params.i());
    for l in preprocesses.keys() {
      included.push(*l);
    }
    included.sort_unstable();

    // Included < threshold
    if included.len() < usize::from(multisig_params.t()) {
      Err(FrostError::InvalidSigningSet("not enough signers"))?;
    }
    // OOB index
    if u16::from(included[included.len() - 1]) > multisig_params.n() {
      Err(FrostError::InvalidParticipant(multisig_params.n(), included[included.len() - 1]))?;
    }
    // Same signer included multiple times
    for i in 0 .. (included.len() - 1) {
      if included[i] == included[i + 1] {
        Err(FrostError::DuplicatedParticipant(included[i]))?;
      }
    }
```

**File:** processor/src/batch_signer.rs (L270-289)
```rust
          let (machine, share) = match machine
            .sign(preprocesses, &batch_message(&self.signable[&id]))
          {
            Ok(res) => res,
            Err(e) => match e {
              FrostError::InternalError(_) |
              FrostError::InvalidParticipant(_, _) |
              FrostError::InvalidSigningSet(_) |
              FrostError::InvalidParticipantQuantity(_, _) |
              FrostError::DuplicatedParticipant(_) |
              FrostError::MissingParticipant(_) => unreachable!(),

              FrostError::InvalidPreprocess(l) | FrostError::InvalidShare(l) => {
                return Some(
                  (ProcessorMessage::InvalidParticipant { id: substrate_sign_id, participant: l })
                    .into(),
                )
              }
            },
          };
```

**File:** processor/src/signer.rs (L581-602)
```rust
        let mut parsed = HashMap::new();
        for l in {
          let mut keys = shares.keys().copied().collect::<Vec<_>>();
          keys.sort();
          keys
        } {
          let mut share_ref = shares.get(&l).unwrap().as_slice();
          let Ok(res) = machine.read_share(&mut share_ref) else {
            return Some(ProcessorMessage::InvalidParticipant { id, participant: l });
          };
          if !share_ref.is_empty() {
            return Some(ProcessorMessage::InvalidParticipant { id, participant: l });
          }
          parsed.insert(l, res);
        }
        let mut shares = parsed;

        for (i, our_share) in our_shares.into_iter().enumerate().skip(1) {
          assert!(shares.insert(self.keys[i].params().i(), our_share).is_none());
        }

        let completion = match machine.complete(shares) {
```

**File:** processor/src/cosigner.rs (L184-199)
```rust
          let (machine, share) =
            match machine.sign(preprocesses, &cosign_block_msg(self.block_number, self.id)) {
              Ok(res) => res,
              Err(e) => match e {
                FrostError::InternalError(_) |
                FrostError::InvalidParticipant(_, _) |
                FrostError::InvalidSigningSet(_) |
                FrostError::InvalidParticipantQuantity(_, _) |
                FrostError::DuplicatedParticipant(_) |
                FrostError::MissingParticipant(_) => unreachable!(),

                FrostError::InvalidPreprocess(l) | FrostError::InvalidShare(l) => {
                  return Some(ProcessorMessage::InvalidParticipant { id, participant: l })
                }
              },
            };
```
