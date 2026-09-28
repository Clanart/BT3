### Title
Unbounded `Participant` indexes in blame evaluation panic via `HashMap` indexing, crashing the node - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
CVE-2016-5439 is a privilege-related availability flaw: a party able to exercise a privileged code path crashes the service because access/authorization validation is incomplete. The Serai analog lives in PedPoP's blame arbitration: `AdditionalBlameMachine::blame` / `BlameMachine::blame` take `sender` and `recipient` `Participant` values supplied by an accusing party and index `HashMap`s keyed only by the legitimate DKG set (`1 ..= n`) without first checking the supplied indexes are in range. An accuser that names an out-of-set participant triggers a `HashMap` index panic, aborting blame evaluation and crashing the process — an availability failure reachable from unauthenticated, attacker-controlled input.

### Finding Description
`Decryption::register` only inserts keys for actual participants (in `AdditionalBlameMachine::new`, strictly `1 ..= n`): [1](#0-0) 

`decrypt_with_proof` then indexes `self.enc_keys[&decryptor]` directly when a proof is provided, with no bounds check on `decryptor`: [2](#0-1) 

`blame_internal` additionally indexes `self.commitments[&sender]`: [3](#0-2) 

Neither `BlameMachine::blame` nor `AdditionalBlameMachine::blame` validates that `sender`/`recipient` are within `1 ..= n` before this indexing: [4](#0-3) 

`Participant::new` only rejects `0` — any `u16` in `1 ..= u16::MAX` is accepted, so `recipient = n + 1` (or any index absent from `enc_keys`) is representable. The preconditions for reaching the panic are fully attacker-constructible:

- `msg.pop` must verify under `pop_challenge(context, msg.pop.R, msg.key, sender, msg.msg)` — the accuser knows the scalar behind `msg.key` and can produce a valid Schnorr PoP over a message of their choosing (`encryption.rs` lines 374–379).
- `proof` must be `Some` — the accuser supplies any syntactically valid `EncryptionKeyProof` (`DLEqProof` is only *verified* after the panicking index on line 388 is evaluated while building the argument list).

At that point `self.enc_keys[&decryptor]` panics (`HashMap` index operator), unwinding through `blame_internal` → `AdditionalBlameMachine::blame` → the caller's blame handler.

### Impact Explanation
The panic kills the task/process evaluating the blame proof (Serai's relayer/processor binaries install panic handlers that exit the process). Instead of the protocol identifying a faulty party and continuing, the node evaluating the accusation halts — a remote, unauthenticated-party-triggerable denial of service of the DKG/blame-arbitration path, matching the CVE's availability impact class (a privileged operation — determining who is faulty — crashes on inputs outside the authorized participant set).

### Likelihood Explanation
Likelihood depends on whether the caller bounds-checks `accuser`/`accused` before invoking `blame`. In `processor/src/key_gen.rs` `VerifyBlame` handling, `accused` and `accuser` originate from the `InvalidDkgShare` tributary transaction, where `Participant::new(u16::from_le_bytes(...))` validates only non-zero — no `<= n` check is performed at deserialization (`coordinator/src/tributary/transaction.rs` lines 346–353), and `AdditionalBlameMachine::new(...).blame(accuser, accused, ...)` is called with those raw values (`processor/src/key_gen.rs` lines 504–549). If no upstream range check rejects `accused > n`, a single crafted blame message deterministically panics every honest node that evaluates it. Caveat: I did not fully verify the coordinator-side `fatal_slash` validation path for `InvalidDkgShare`; if the coordinator rejects out-of-range participant indexes before dispatching `VerifyBlame`, reachability is reduced to processors evaluating blame from an untrusted coordinator or other callers of the public `blame` API.

### Recommendation
In `blame_internal` (or at the top of `BlameMachine::blame` and `AdditionalBlameMachine::blame`), reject `sender`/`recipient` not present in `self.commitments`/`self.encryption.enc_keys` — e.g., `if !self.commitments.contains_key(&sender) || !self.encryption.enc_keys.contains_key(&recipient) { return sender_or_error; }` — rather than indexing. Replace `self.enc_keys[&decryptor]` and `self.commitments[&sender]` with `.get()` + early error, and have callers (`VerifyBlame` in `processor/src/key_gen.rs`) pre-validate `accuser`/`accused` against `params.n()`.

### Proof of Concept
```rust
// pedpop crate, Secp256k1 or Ristretto ciphersuite, n = 2 DKG.
// Build a valid AdditionalBlameMachine over two real commitment msgs.
let machine = AdditionalBlameMachine::<C>::new(context, 2, commitment_msgs).unwrap();

// Attacker (accuser) crafts an EncryptedMessage with a valid PoP under `sender = 1`,
// and a syntactically valid EncryptionKeyProof for a key they control.
let msg: EncryptedMessage<C, SecretShare<C::F>> = craft_with_valid_pop(context, from: 1);
let proof: EncryptionKeyProof<C> = prove_dleq_for_own_key();

// accuser names a `recipient` (accused/decryptor) outside 1..=n
let ghost = Participant::new(3).unwrap(); // valid non-zero Participant, but never registered

// decrypt_with_proof evaluates self.enc_keys[&ghost] while building the DLEq
// argument list -> HashMap index panic -> process abort.
machine.blame(Participant::new(1).unwrap(), ghost, msg, Some(proof));
```

Root cause lines: `crypto/dkg/pedpop/src/encryption.rs:388` (`self.enc_keys[&decryptor]`) and `crypto/dkg/pedpop/src/lib.rs:599` (`self.commitments[&sender]`), both reached via `blame_internal` at `crypto/dkg/pedpop/src/lib.rs:582` with attacker-supplied participant indexes that are never range-checked against the registered set `1 ..= n` established at `crypto/dkg/pedpop/src/lib.rs:656-660`.

### Citations

**File:** crypto/dkg/pedpop/src/lib.rs (L595-605)
```rust
    // If this isn't a valid share, the sender is faulty
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

**File:** crypto/dkg/pedpop/src/lib.rs (L623-632)
```rust
  pub fn blame(
    self,
    sender: Participant,
    recipient: Participant,
    msg: EncryptedMessage<C, SecretShare<C::F>>,
    proof: Option<EncryptionKeyProof<C>>,
  ) -> (AdditionalBlameMachine<C>, Participant) {
    let faulty = self.blame_internal(sender, recipient, msg, proof);
    (AdditionalBlameMachine(self), faulty)
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

**File:** crypto/dkg/pedpop/src/encryption.rs (L381-390)
```rust
    if let Some(proof) = proof {
      // Verify this is the decryption key for this message
      proof
        .dleq
        .verify(
          &mut encryption_key_transcript(self.context),
          &[C::generator(), msg.key],
          &[self.enc_keys[&decryptor], *proof.key],
        )
        .map_err(|_| DecryptionError::InvalidProof)?;
```
