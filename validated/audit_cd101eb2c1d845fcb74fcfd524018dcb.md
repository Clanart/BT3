### Title
Crafted `ReceivedOutput` bypasses the key-ownership check in `SignableTransaction::multisig`, producing valid signatures for transactions spending nonexistent outputs - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`SignableTransaction::multisig` is documented as returning `None` "if the wrong keys are used" — its only guard is comparing `p2tr_script_buf(offset.group_key())` against the attacker-influenced `prevouts[i].script_pubkey`. Because a `ReceivedOutput` carries a freely-chosen `offset` scalar, `output` (`TxOut`), and `outpoint` (all deserializable via the public `ReceivedOutput::read`), an unprivileged party can fabricate an "input" that passes this check while spending an outpoint that was never received by the multisig — or that doesn't exist at all. The FROST signers then produce a valid BIP-340 signature over a digest of the attacker's choosing.

### Finding Description
`ReceivedOutput::read` (networks/bitcoin/src/wallet/mod.rs:122-134) decodes `offset`, `TxOut`, and `OutPoint` from untrusted bytes with no proof that the outpoint was ever observed by `Scanner::scan_transaction` — `Scanner` only matches `script_pubkey` (mod.rs:205) and cannot authenticate the claimed `offset`. In `SignableTransaction::new` (send.rs:150-256) the supplied `prevouts` and `offsets` are stored verbatim; nothing checks that an input corresponds to a real, spendable UTXO. In `multisig` (send.rs:273-285), the check

```rust
if p2tr_script_buf(offset.group_key())? != self.prevouts[i].script_pubkey { None? }
```

is trivially satisfied by an attacker: they choose `offset`, compute `group_key + offset·G` themselves (the group key is public), and set `prevouts[i].script_pubkey` to its P2TR script. `TransactionSignMachine::sign` (send.rs:383-391) then has each signer produce a signature share committing to `taproot_key_spend_signature_hash(i, Prevouts::All(...))` — a sighash fully determined by attacker-controlled fields — and `complete` (send.rs:413-428) emits a valid 64-byte BIP-340 signature under the offset group key.

### Impact Explanation
The intended restriction — that the machine only signs spends of outputs the Scanner actually attributes to the threshold key — is bypassed. The validator set signs an "unintended message": a transaction digest for inputs it never received. The resulting signature is cryptographically valid under `group_key + offset·G`, so it can be presented as a signed authorization (e.g., to off-chain coordinators or protocols interpreting the signature) even though no corresponding on-chain spend exists. Since `Prevouts::All` commits to all inputs, a single fabricated input also controls the digest of every honest input's signature.

### Likelihood Explanation
Any party able to feed bytes to `ReceivedOutput::read` or supply `Vec<ReceivedOutput>` to `SignableTransaction::new` (e.g., via processor networking/deserialization paths that consume untrusted data) can reach this with only public knowledge of the group key. No collusion, key material, or validator position is required; the attacker merely fabricates consistent `(offset, script_pubkey)` pairs. Exploitability is limited by the fact that the forged transaction is consensus-invalid on-chain, capping the impact at Medium.

### Recommendation
Bind `ReceivedOutput` values to scanner provenance: require inputs to `SignableTransaction::new` to originate from `Scanner::scan_transaction`/`scan_block` for the same key (e.g., a type-level distinction or verification that `scripts` contains the output's `script_pubkey` at the recorded offset), and/or verify the claimed outpoint exists on-chain before signing. At minimum, document that `multisig`'s check is only an offset/script consistency check, not a proof of ownership.

### Proof of Concept
```rust
// Attacker knows only the public group key `key` (even-Y).
let fake_offset = Scalar::random(&mut OsRng);
let offset_key = key + (ProjectivePoint::GENERATOR * fake_offset);
let fake_script = p2tr_script_buf(offset_key).unwrap();

// Fabricate a ReceivedOutput for an outpoint the multisig never received.
let forged = ReceivedOutput::read(&mut &{
    let mut buf = fake_offset.to_bytes().to_vec();
    buf.extend(serialize(&TxOut { value: Amount::from_sat(1_000_000),
                                  script_pubkey: fake_script }));
    buf.extend(serialize(&OutPoint::null())); // nonexistent outpoint
    buf
}[..]).unwrap();

// multisig's "wrong keys" check passes: script_pubkey matches group_key + offset*G.
let tx = SignableTransaction::new(vec![forged], &payments, None, None, fee).unwrap();
let machine = tx.multisig(&keys).unwrap(); // returns Some — check bypassed
// sign/complete now yield a valid BIP-340 signature under the offset key
// over the attacker-chosen sighash.
```