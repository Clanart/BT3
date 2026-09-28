### Title
Unauthenticated `ReceivedOutput` bytes reach the threshold signing path, letting an untrusted party get wallet UTXOs signed under attacker-chosen terms — (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`ReceivedOutput::read` deserializes `offset`, `output` (TxOut), and `outpoint` from raw bytes with no authentication or on-chain provenance check. These bytes flow into `SignableTransaction::new` and `SignableTransaction::multisig`, which produce a FROST `TransactionMachine` that signs Taproot key-spend sighashes for the declared inputs. This is analogous to the CodeIgniter4 CLI-via-HTTP flaw: functionality that assumes trusted input (the wallet signer) is reachable from unauthenticated, externally supplied bytes.

### Finding Description
`ReceivedOutput::read` accepts arbitrary bytes — the `offset` scalar, the `TxOut` (script_pubkey + value), and the `OutPoint` are all attacker-controlled. [1](#0-0) 

`SignableTransaction::new` treats these as ground truth: it uses `input.output.value` for the fee/funds math and `input.outpoint` as the TX input, then stores `input.output` into `prevouts`. [2](#0-1) [3](#0-2) 

`SignableTransaction::multisig` performs only one binding check: that `p2tr_script_buf(offset_key) == prevouts[i].script_pubkey`, i.e., the claimed output pays to some key derived from the group key under the claimed offset. [4](#0-3)  The outpoint, the value, and any association with an actually-observed on-chain output are never verified. Signing then commits via `Prevouts::All` to the attacker-supplied prevouts. [5](#0-4) 

Because P2TR outputs paying to the multisig's keys are public on-chain data, an unprivileged party can fabricate a `ReceivedOutput` for any wallet-controlled UTXO they can observe: they know the script_pubkey, can set `offset` to the matching scalar (0 for the untweaked key, or the registered branch/change/forward offsets derivable via `Secp256k1::hash_to_F(KEY_DST, ...)`), and set the correct outpoint/value. The only missing piece — payment destinations — is satisfied by whatever caller passes `payments`, so any pipeline that lets external input drive `SignableTransaction::new` (the documented data flow: scanned/read outputs → `SignableTransaction` → `multisig` → `sign`) lets an attacker substitute which UTXOs get spent, or get the group to sign a transaction they constructed.

### Impact Explanation
Concrete impact: signing of an unintended message. The threshold signers produce a valid BIP-340 signature over a sighash for a transaction the honest participants did not authorize — e.g., spending a wallet UTXO the attacker selected, with attacker-influenced payment outputs, or with a fabricated prevout value that burns the difference as fee. Since the sighash binds `prevouts` byte-for-byte, the attacker fully controls the signed digest; the signers' only defense is the script_pubkey↔offset check, which is satisfiable with public data. This mirrors the advisory class: a route meant for a trusted context (authenticated scanner results) is executable with unauthenticated input.

### Likelihood Explanation
Reachable wherever `ReceivedOutput::read`/serialized outputs cross a trust boundary — e.g., outputs relayed between components, forwarded output claims, or any integrator that hydrates `ReceivedOutput` from peer/network data rather than from its own `Scanner::scan_transaction`/`scan_block` result. No key material, collusion, or validator misbehavior is required; only public blockchain knowledge plus the ability to supply the serialized bytes.

### Recommendation
Bind `ReceivedOutput` to its provenance: only construct `SignableTransaction` from outputs produced by `Scanner` (or verify against the chain before signing — confirm the outpoint exists, is unspent, and its `TxOut` matches the claimed script_pubkey and value). If untrusted `ReceivedOutput` bytes must be accepted, re-derive the expected `script_pubkey` from `offset` and the group key and cross-check the outpoint against a trusted node before creating the multisig machine, rather than relying solely on the internal consistency check in `multisig`.

### Proof of Concept
1. Attacker observes a confirmed P2TR output paying to the Serai group's tweaked key (script_pubkey `Q`, outpoint `P`, value `V`).
2. Attacker constructs bytes: `offset = 0` (or `hash_to_F(KEY_DST, b"branch")` etc. for the known derived keys), followed by consensus-encoded `TxOut{value: V, script_pubkey: Q}`, followed by consensus-encoded `P`.
3. `ReceivedOutput::read` accepts this. `SignableTransaction::new(vec![received], &payments_to_attacker, None, None, fee)` succeeds; `multisig(keys)` succeeds because `p2tr_script_buf(group_key) == Q`.
4. The FROST signers run `TransactionSignMachine::sign`, producing a valid transaction spending the wallet UTXO under attacker-chosen outputs — a signed message the signers never intended to produce.

### Citations

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

**File:** networks/bitcoin/src/wallet/send.rs (L245-255)
```rust
    Ok(SignableTransaction {
      tx: Transaction {
        version: Version(2),
        lock_time: LockTime::ZERO,
        input: tx_ins,
        output: tx_outs,
      },
      offsets,
      prevouts: inputs.drain(..).map(|input| input.output).collect(),
      needed_fee,
    })
```

**File:** networks/bitcoin/src/wallet/send.rs (L273-285)
```rust
  pub fn multisig(self, keys: &ThresholdKeys<Secp256k1>) -> Option<TransactionMachine> {
    let mut sigs = vec![];
    for i in 0 .. self.tx.input.len() {
      let offset = keys.clone().offset(self.offsets[i]);
      if p2tr_script_buf(offset.group_key())? != self.prevouts[i].script_pubkey {
        None?;
      }

      sigs.push(AlgorithmMachine::new(Schnorr::new(), keys.clone().offset(self.offsets[i])));
    }

    Some(TransactionMachine { tx: self, sigs })
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
