### Title
`ReceivedOutput::read` deserializes attacker-controlled offset/output/outpoint with no binding between the claimed prevout and the referenced UTXO, letting `SignableTransaction::multisig` produce signatures for an unintended spend - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
The external report's bug class is "value is marked as transferred/received based on unchecked attacker-influenced data, so the recorded state does not correspond to spendable funds." The Serai analog lives in the Bitcoin wallet layer: `ReceivedOutput` is the unit by which the system represents "funds we received and can spend," yet `ReceivedOutput::read` and `SignableTransaction::new` trust every field of it. The only consistency check performed anywhere is that the deserialized `offset` yields a script equal to the deserialized `TxOut.script_pubkey` — no check binds `output`/`outpoint` to any real on-chain UTXO. An untrusted party who feeds bytes to `ReceivedOutput::read` can therefore cause the threshold multisig to sign a transaction spending a different UTXO than the one the signer believes it is spending, or a UTXO that does not exist at all.

### Finding Description
`ReceivedOutput::read` at `networks/bitcoin/src/wallet/mod.rs:122-134` parses three independent attacker-controlled fields — a `Scalar` offset, a `TxOut`, and an `OutPoint` — and returns them as a valid `ReceivedOutput` with no relationship enforced between `outpoint` and `output`. `SignableTransaction::new` (`networks/bitcoin/src/wallet/send.rs:150-256`) then uses `input.outpoint` as the transaction input, `input.output` as the committed prevout for both fee accounting (`fee()` at send.rs:138-141 and `NotEnoughFunds` at send.rs:215) and the BIP-341 sighash (`Prevouts::All(&self.tx.prevouts)` at send.rs:375-386). `multisig` (send.rs:273-285) only verifies `p2tr_script_buf(offset.group_key()) == prevouts[i].script_pubkey`, i.e., self-consistency of the *attacker-supplied* tuple, never that `outpoint` actually resolves to `output` on chain.

### Impact Explanation
An unprivileged participant supplying `ReceivedOutput` bytes can produce two concrete harms:

1. **Signing an unintended spend.** Set `outpoint` to any real UTXO paying to the multisig key (e.g., a consolidated wallet UTXO that was never meant to be spent in this plan) and set `output`/`offset` to its true script and value. The consistency check at send.rs:277 passes, `Prevouts::All` commits to the correct prevout, and the threshold produces a valid signature spending that UTXO to attacker-chosen `payments` — an unauthorized transfer of funds.
2. **Funds recorded as spendable that are not.** Set `outpoint` to a nonexistent or already-spent outpoint with a forged `output` value; the transaction is signed (wasting the multisig's signing session and any batched plans) but is consensus-invalid, so the amount counted in `input_sat`/`fee()` accounting was never real — the exact "reported received but not spendable" shape of the reference bug.

### Likelihood Explanation
Reachable whenever serialized `ReceivedOutput`s cross a trust boundary: the struct is explicitly designed for wire serialization (`read`/`write`/`serialize`, mod.rs:120-148) and is enumerated in scope as an untrusted-bytes entry point. Any coordinator/peer able to supply or relay these bytes to a `SignableTransaction::new` + `multisig` + `sign` flow controls the outpoint, prevout amount, and payments. No key material, collusion, or validator misbehavior is required — only the ability to submit crafted bytes. The only mitigating check (script-vs-offset) is satisfied by construction of the malicious tuple.

### Recommendation
Bind the `ReceivedOutput` to chain reality before it reaches signing. Concretely: in `SignableTransaction::new` or `multisig`, verify each `outpoint` against a trusted source (e.g., require the `ReceivedOutput` to originate from `Scanner::scan_transaction`/`scan_block`, which derives `outpoint` and `output` from the same observed transaction at mod.rs:199-213), or re-derive `prevouts[i]` by fetching `outpoint`'s UTXO rather than trusting the embedded `TxOut`. At minimum, document that `ReceivedOutput::read` must only be used on bytes produced by the local `Scanner` and treat externally supplied bytes as untrusted.

### Proof of Concept
```rust
// Attacker crafts bytes: real multisig-owned UTXO's outpoint,
// its true TxOut (public on chain), and offset Scalar::ZERO.
let mut buf = Vec::new();
buf.extend(Scalar::ZERO.to_bytes());                    // offset
buf.extend(serialize(&real_utxo_txout));                // output (true prevout)
buf.extend(serialize(&real_utxo_outpoint));             // outpoint (not the intended input)

let received = ReceivedOutput::read(&mut buf.as_slice()).unwrap();

// SignableTransaction::new accepts it; multisig's script check passes
// because offset/script are self-consistent.
let tx = SignableTransaction::new(
    vec![received],
    &[(attacker_script_buf, attacker_amount)],
    None, None, FEE,
).unwrap();
// tx.multisig(&keys) -> Some(..); sign() produces a valid signature
// spending real_utxo_outpoint to attacker_script_buf — a spend the
// signer never intended.
```

Relevant code: `ReceivedOutput::read` [1](#0-0) , fields [2](#0-1) , trusted consumption [3](#0-2) , sole consistency check [4](#0-3) , prevout commitment [5](#0-4) .

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L89-97)
```rust
#[derive(Clone, PartialEq, Eq, Debug)]
pub struct ReceivedOutput {
  // The scalar offset to obtain the key usable to spend this output.
  offset: Scalar,
  // The output to spend.
  output: TxOut,
  // The TX ID and vout of the output to spend.
  outpoint: OutPoint,
}
```

**File:** networks/bitcoin/src/wallet/mod.rs (L122-134)
```rust
  pub fn read<R: Read>(r: &mut R) -> io::Result<ReceivedOutput> {
    let offset = Secp256k1::read_F(r)?;
    let output;
    let outpoint;
    {
      let mut buf_r = BufReader::with_capacity(0, r);
      output =
        TxOut::consensus_decode(&mut buf_r).map_err(|_| io::Error::other("invalid TxOut"))?;
      outpoint =
        OutPoint::consensus_decode(&mut buf_r).map_err(|_| io::Error::other("invalid OutPoint"))?;
    }
    Ok(ReceivedOutput { offset, output, outpoint })
  }
```

**File:** networks/bitcoin/src/wallet/send.rs (L175-185)
```rust
    let input_sat = inputs.iter().map(|input| input.output.value.to_sat()).sum::<u64>();
    let offsets = inputs.iter().map(|input| input.offset).collect();
    let tx_ins = inputs
      .iter()
      .map(|input| TxIn {
        previous_output: input.outpoint,
        script_sig: ScriptBuf::new(),
        sequence: Sequence::MAX,
        witness: Witness::new(),
      })
      .collect::<Vec<_>>();
```

**File:** networks/bitcoin/src/wallet/send.rs (L275-282)
```rust
    for i in 0 .. self.tx.input.len() {
      let offset = keys.clone().offset(self.offsets[i]);
      if p2tr_script_buf(offset.group_key())? != self.prevouts[i].script_pubkey {
        None?;
      }

      sigs.push(AlgorithmMachine::new(Schnorr::new(), keys.clone().offset(self.offsets[i])));
    }
```

**File:** networks/bitcoin/src/wallet/send.rs (L373-390)
```rust
    let mut cache = SighashCache::new(&self.tx.tx);
    // Sign committing to all inputs
    let prevouts = Prevouts::All(&self.tx.prevouts);

    let mut shares = Vec::with_capacity(self.sigs.len());
    let sigs = self
      .sigs
      .drain(..)
      .enumerate()
      .map(|(i, sig)| {
        let (sig, share) = sig.sign(
          commitments[i].clone(),
          cache
            .taproot_key_spend_signature_hash(i, &prevouts, TapSighashType::Default)
            // This should never happen since the inputs align with the TX the cache was
            // constructed with, and because i is always < prevouts.len()
            .expect("taproot_key_spend_signature_hash failed to return a hash")
            .as_ref(),
```
