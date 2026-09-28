### Title
Panic on out-of-range `Participant` in blame handling crashes the DKG host (denial of service) - (File: crypto/dkg/pedpop/src/lib.rs)

### Summary
`BlameMachine::blame_internal` indexes the `commitments` HashMap with an attacker-supplied `sender` `Participant` that is never bounds-checked against the DKG's `1 ..= n` set. `Participant::new` accepts any non-zero `u16`, so a blame request naming `Participant > n` reaches `self.commitments[&sender]` (and, depending on evaluation order, the per-sender ECDH key lookup inside `Decryption::decrypt_with_proof`), which panics on a missing `HashMap` key. This is the Serai analog of CVE-2020-1597's unauthenticated remote DoS: an unprivileged party supplies input that the service mishandles, crashing the process.

### Finding Description
`AdditionalBlameMachine::new` populates `commitments` only for `i in 1 ..= n`: [1](#0-0) 

`Participant` is a thin non-zero `u16` wrapper (`Participant::new(u16)` rejects only zero), so callers of `blame` may pass any `Participant` in `1 ..= 65535`. `blame_internal` then performs the share-validity check: [2](#0-1) 

`self.commitments[&sender]` uses `HashMap`'s `Index` impl, which panics when `sender` has no entry — guaranteed whenever `u16::from(sender) > n`. Before that line, `self.encryption.decrypt_with_proof(sender, recipient, msg, proof)` resolves the sender's registered encryption key; I could not read the remainder of `encryption.rs` (lines ~300+) in the available iterations, but the per-sender key store there is likewise keyed by `Participant` and populated only for `1 ..= n`, so a missing-key panic may occur even earlier in the same call path. No validation of `sender`/`recipient` against `params` or the commitment set exists anywhere in `blame`, `blame_internal`, or `AdditionalBlameMachine::blame`: [3](#0-2) 

The reachable trigger is a blame/accusation message — exactly the path exercised by `processor/src/key_gen.rs`, where `.blame(accuser, accused, ...)` is invoked with an `accused` participant taken from an incoming protocol message: [4](#0-3) 

### Impact Explanation
A panic in this code path unwinds (or aborts) the host handling DKG blame. Anyone able to submit a blame accusation naming a `Participant` outside `1 ..= n` — a public, unauthenticated protocol input — crashes the processor/validator evaluating blame, denying service to the key-generation and signing pipeline. This mirrors the advisory's remotely triggerable, no-authentication denial of service.

### Likelihood Explanation
The trigger requires only that the attacker reach a blame evaluation with a `Participant` value greater than `n`. `Participant::new(255)` succeeds regardless of the actual multisig size, and the blame path performs no membership check before indexing. If `decrypt_with_proof` requires a well-formed `EncryptedMessage`, the attacker can produce one themselves (they control the ephemeral key and can generate a valid Schnorr PoP); if the ECDH key lookup panics on the unregistered sender first, the same crash is reached with even less work. The only uncertainty is which lookup panics first — both are missing-key `HashMap` indexes on the same unchecked input.

### Recommendation
In `BlameMachine::blame_internal` (and `AdditionalBlameMachine::new`/`blame`), validate that `sender` and `recipient` are members of `self.commitments` (i.e., `Participant` in `1 ..= n`) before indexing, returning the appropriate faulty-party result or an error instead of relying on `HashMap::index`. Replace `self.commitments[&sender]` with a `get`/`ok_or` pattern, and mirror the check inside `Decryption::decrypt_with_proof` for its per-sender key map.

### Proof of Concept
```rust
// crypto/dkg/pedpop: after a KeyMachine::calculate_share produces a BlameMachine,
// or via AdditionalBlameMachine::new(context, n, commitment_msgs) with n = 3:
let machine = AdditionalBlameMachine::<Secp256k1>::new(context, 3, commitment_msgs).unwrap();
// commitments only contains keys for Participant(1..=3)
let attacker_claimed_sender = Participant::new(255).unwrap(); // valid non-zero Participant
// Any EncryptedMessage; if decryption rejects it, try a self-constructed
// EncryptedMessage with a valid PoP so the map indexing at line 599 is reached.
machine.blame(attacker_claimed_sender, recipient, msg, proof);
// thread 'main' panicked: HashMap index on absent key
// (at Decryption's per-sender key lookup or at self.commitments[&sender], lib.rs:599)
```
A `Participant` of `255` passes `Participant::new` (non-zero), has no entry in `commitments` for a `1 ..= 3` multisig, and turns a routine blame evaluation into a process-killing panic.

### Citations

**File:** crypto/dkg/pedpop/src/lib.rs (L596-605)
```rust
    if !bool::from(
      multiexp_vartime(&share_verification_statements::<C>(
        recipient,
        &self.commitments[&sender],
        Zeroizing::new(share),
      ))
      .is_identity(),
    ) {
      return sender;
    }
```

**File:** crypto/dkg/pedpop/src/lib.rs (L654-661)
```rust
    let mut commitments = HashMap::new();
    let mut encryption = Decryption::new(context);
    for i in 1 ..= n {
      let i = Participant::new(i).unwrap();
      let Some(msg) = commitment_msgs.remove(&i) else { Err(PedPoPError::MissingParticipant(i))? };
      commitments.insert(i, encryption.register(i, msg).commitments);
    }
    Ok(AdditionalBlameMachine(BlameMachine { commitments, encryption, result: None }))
```

**File:** crypto/dkg/pedpop/src/lib.rs (L674-682)
```rust
  pub fn blame(
    &self,
    sender: Participant,
    recipient: Participant,
    msg: EncryptedMessage<C, SecretShare<C::F>>,
    proof: Option<EncryptionKeyProof<C>>,
  ) -> Participant {
    self.0.blame_internal(sender, recipient, msg, proof)
  }
```

**File:** processor/src/key_gen.rs (L543-556)
```rust
        let substrate_blame = AdditionalBlameMachine::new(
          context(&id, SUBSTRATE_KEY_CONTEXT),
          params.n(),
          substrate_commitment_msgs,
        )
        .unwrap()
        .blame(accuser, accused, substrate_share, substrate_blame);
        let network_blame = AdditionalBlameMachine::new(
          context(&id, NETWORK_KEY_CONTEXT),
          params.n(),
          network_commitment_msgs,
        )
        .unwrap()
        .blame(accuser, accused, network_share, network_blame);
```
