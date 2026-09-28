### Title
Attacker-controlled blame bytes are parsed under two incompatible types, producing divergent blame verdicts between the substrate and network machines - (File: processor/src/key_gen.rs)

### Summary
The CVE's bug class is a field whose bytes are interpreted with a mismatched type/semantic such that the intended security property (TLS on RPC) is silently not what was configured. In Serai, the same shape exists in `CoordinatorMessage::VerifyBlame` handling: a single accuser-supplied `blame` byte string is deserialized independently as `EncryptionKeyProof<Ristretto>` and as `EncryptionKeyProof<N::Curve>`. The two curves have different `read_G`/`read_F` encodings (Ristretto points are 32 bytes; e.g. secp256k1 compressed points are 33 bytes), so a proof valid for one type is a type mismatch under the other — it either fails to parse (yielding `None`) or parses into a different value. Each result is then fed to a separate `AdditionalBlameMachine::blame(...)` call, yielding inconsistent guilty-party verdicts on the two machines for one accusation.

### Finding Description
In `processor/src/key_gen.rs` (`VerifyBlame` handler), the identical `blame` blob is read twice under different types:

```rust
let substrate_blame =
  blame.clone().and_then(|blame| EncryptionKeyProof::read(&mut blame.as_slice()).ok());
let network_blame =
  blame.clone().and_then(|blame| EncryptionKeyProof::read(&mut blame.as_slice()).ok());
```

`EncryptionKeyProof::read` is monomorphic over the ciphersuite (`crypto/dkg/pedpop/src/encryption.rs`), so the same bytes are interpreted as a Ristretto proof and as an `N::Curve` proof. Because the encodings differ in length, a blame crafted to be valid on one curve will be a mismatched encoding on the other: it parses to `None` (or to garbage that fails verification). The code comments confirm the stakes: "this being called means *someone* is getting fatally slashed" — yet the two machines can now disagree about whom, driven purely by a data-type mismatch in parsing the accuser's bytes. The substrate share and network share reads immediately above (`EncryptedMessage::read` on `share_ref`) share the same pattern but correctly enforce `!share_ref.is_empty()`; the blame reads do not correlate the two parses at all.

### Impact Explanation
An accuser (any validator submitting an `InvalidDkgShare`/`VerifyBlame` path) can supply blame bytes that are a valid `EncryptionKeyProof` on one curve and a type mismatch on the other. The substrate blame machine and the network blame machine then reach contradictory verdicts — e.g., the sender is proven faulty on the substrate side while the missing/invalid network proof causes the accuser to be blamed on the network side (or vice versa). The result is fatally slashing an innocent party or letting a faulty party escape, i.e., an incorrect blame verdict driven entirely by parsing the same public bytes under mismatched types — the same "field interpreted as a different type than intended, producing a broken security outcome" shape as the rpk TLS field bug.

### Likelihood Explanation
Reachable by any participant able to trigger a blame verification with attacker-chosen `blame` bytes — this is untrusted transaction data fed to a `read`/verify API, which is in scope. Whether a concrete contradictory verdict occurs depends on the length/encoding mismatch between `Ristretto` and `N::Curve` proofs, which exists for any non-Ristretto network curve, and on how the processor reconciles the two blame results (the code does not appear to check they agree). Severity: Medium — it corrupts a slashing verdict rather than key material directly.

### Recommendation
Parse the blame blob once as a concatenated pair `(EncryptionKeyProof<Ristretto>, EncryptionKeyProof<N::Curve>)` (mirroring how the shares and commitments are handled with both curves in sequence), require it to fully consume the buffer, and reject the transaction if either side fails — rather than `and_then(...).ok()`-flattening each parse to `None` independently. At minimum, assert the two blame machines return the same guilty participant and treat divergence as a malformed transaction.

### Proof of Concept
1. A validator produces an `InvalidDkgShare` transaction accusing participant `f`, attaching `blame` bytes that encode a valid `EncryptionKeyProof<Ristretto>` (32-byte scalar + 32-byte Ristretto point) — e.g., obtained by running `blame()` legitimately on the substrate machine.
2. In `VerifyBlame`, `substrate_blame` parses successfully and `AdditionalBlameMachine::<Ristretto>::blame(...)` correctly identifies `f` as faulty.
3. `network_blame` re-parses the same bytes as `EncryptionKeyProof<N::Curve>`; since `N::Curve::read_G` expects a 33-byte encoding, the read fails or shifts, yielding `None`/invalid, and `AdditionalBlameMachine::<N::Curve>::blame(...)` identifies the *accuser* as faulty for failing to substantiate the claim.
4. The two verdicts disagree: `f` guilty on substrate, accuser guilty on the network. Whichever verdict the coordinator applies, an innocent party can be slashed or a guilty one spared — a concrete divergent-verdict outcome from a type-mismatched parse of attacker-supplied bytes. [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** processor/src/key_gen.rs (L504-549)
```rust
      CoordinatorMessage::VerifyBlame { id, accuser, accused, share, blame } => {
        let params = ParamsDb::get(txn, &id.session, id.attempt).unwrap().0;

        let mut share_ref = share.as_slice();
        let Ok(substrate_share) = EncryptedMessage::<
          Ristretto,
          SecretShare<<Ristretto as Ciphersuite>::F>,
        >::read(&mut share_ref, params) else {
          return ProcessorMessage::Blame { id, participant: accused };
        };
        let Ok(network_share) = EncryptedMessage::<
          N::Curve,
          SecretShare<<N::Curve as Ciphersuite>::F>,
        >::read(&mut share_ref, params) else {
          return ProcessorMessage::Blame { id, participant: accused };
        };
        if !share_ref.is_empty() {
          return ProcessorMessage::Blame { id, participant: accused };
        }

        let mut substrate_commitment_msgs = HashMap::new();
        let mut network_commitment_msgs = HashMap::new();
        let commitments = CommitmentsDb::get(txn, &id).unwrap();
        for (i, commitments) in commitments {
          let mut commitments = commitments.as_slice();
          substrate_commitment_msgs
            .insert(i, EncryptionKeyMessage::<_, _>::read(&mut commitments, params).unwrap());
          network_commitment_msgs
            .insert(i, EncryptionKeyMessage::<_, _>::read(&mut commitments, params).unwrap());
        }

        // There is a mild DoS here where someone with a valid blame bloats it to the maximum size
        // Given the ambiguity, and limited potential to DoS (this being called means *someone* is
        // getting fatally slashed) voids the need to ensure blame is minimal
        let substrate_blame =
          blame.clone().and_then(|blame| EncryptionKeyProof::read(&mut blame.as_slice()).ok());
        let network_blame =
          blame.clone().and_then(|blame| EncryptionKeyProof::read(&mut blame.as_slice()).ok());

        let substrate_blame = AdditionalBlameMachine::new(
          context(&id, SUBSTRATE_KEY_CONTEXT),
          params.n(),
          substrate_commitment_msgs,
        )
        .unwrap()
        .blame(accuser, accused, substrate_share, substrate_blame);
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

**File:** crypto/dkg/pedpop/src/encryption.rs (L170-177)
```rust
impl<C: Ciphersuite, E: Encryptable> EncryptedMessage<C, E> {
  pub fn read<R: io::Read>(reader: &mut R, params: ThresholdParams) -> io::Result<Self> {
    Ok(Self {
      key: C::read_G(reader)?,
      pop: SchnorrSignature::<C>::read(reader)?,
      msg: Zeroizing::new(E::read(reader, params)?),
    })
  }
```
