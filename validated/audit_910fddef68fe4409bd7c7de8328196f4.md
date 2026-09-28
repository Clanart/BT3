### Title
Untrusted `ReceivedOutput` bytes let an attacker inject phantom inputs with arbitrary offset/outpoint/prevout, producing signatures over forged prevout data - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
The path-traversal bug class — untrusted container contents redirecting an operation to a location outside its intended domain — maps onto `bitcoin-serai`'s `ReceivedOutput` deserialization: a `ReceivedOutput` is the "path" telling the wallet *which on-chain output* to spend and *which key offset* controls it, yet `ReceivedOutput::read` accepts all three fields (`offset`, `TxOut`, `outpoint`) verbatim from attacker-controlled bytes with no binding between them and no verification against the chain.

### Finding Description
`ReceivedOutput::read` reads an arbitrary scalar `offset`, an arbitrary `TxOut` (script_pubkey and value), and an arbitrary `OutPoint` directly from the input stream, and returns the result unconditionally [1](#0-0) . `SignableTransaction::new` then trusts these fields completely: it sums `input.output.value` for balance/fee accounting, uses `input.outpoint` as the txin, copies `input.offset` into `offsets`, and stores the attacker-supplied `TxOut` into `prevouts` [2](#0-1) [3](#0-2) . During signing, the only consistency check is that `p2tr_script_buf(keys.offset(offset).group_key())` equals the *claimed* `prevouts[i].script_pubkey` — which the attacker sets to whatever script they need [4](#0-3) . The transaction is then signed with `Prevouts::All(&self.tx.prevouts)`, committing the threshold signature to the forged prevout values [5](#0-4) . Nothing ever checks that `outpoint` exists, is unspent, or actually pays to the claimed script.

### Impact Explanation
An unprivileged party able to feed serialized bytes to `ReceivedOutput::read` (the crate's own untrusted-input boundary) can:

- Report funds as received that are not spendable: fabricate a `ReceivedOutput` claiming an outpoint that doesn't exist, is already spent, or pays to an unrelated script, with a forged `TxOut` whose `script_pubkey` matches `p2tr(key + offset·G)` so the multisig consistency check passes. The wallet's balance/fee accounting treats the phantom value as real.
- Cause the threshold group to produce a valid FROST signature over attacker-chosen `Prevouts::All` data (value and script), i.e., the signers attest to a transaction spending outputs that were never theirs — the analog of a crafted archive writing outside the intended directory.
- Alternatively supply a mismatching/odd-key offset so `multisig` returns `None` [6](#0-5) , aborting every signing attempt that consumes the poisoned input and blocking legitimate spends.

### Likelihood Explanation
Reachable wherever `ReceivedOutput`s are deserialized from storage, network peers, or coordinator messages rather than produced solely by the local `Scanner` (which does bind offset→script via `self.scripts` [7](#0-6) ). The public `read`/`serialize` API exists precisely for transport, so any deployment that round-trips these structures through a less-trusted party is exposed. Exploitation requires only crafting bytes; no key material is needed since the attacker chooses the `TxOut` to satisfy the only check.

### Recommendation
Authenticate the triple: either restrict `ReceivedOutput` construction to `Scanner::scan_transaction` (make `read` emit a verified binding, e.g. recompute `p2tr_script_buf(group_key + offset·G)` inside `read` and reject mismatches), or have `SignableTransaction::new`/`multisig` confirm each `outpoint` resolves on-chain to a `TxOut` equal to the claimed `prevout` before signing.

### Proof of Concept
1. Observe/derive any valid registered `offset` `o` and compute `S = p2tr_script_buf(group_key + o·G)`.
2. Craft bytes for `ReceivedOutput::read`: `offset = o`, `output = TxOut{ value: <inflated>, script_pubkey: S }`, `outpoint = OutPoint{ txid: <nonexistent or victim-unrelated>, vout: 0 }`.
3. Pass it to `SignableTransaction::new` — it is accepted and its value counted; `multisig()` passes the script check; `sign()` produces threshold signature shares over `Prevouts::All` containing the forged `TxOut`.
4. Result: the wallet reports a received balance that cannot be spent, and the multisig signs a transaction claiming inputs the group does not own.

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

**File:** networks/bitcoin/src/wallet/mod.rs (L205-211)
```rust
      if let Some(offset) = self.scripts.get(&output.script_pubkey) {
        res.push(ReceivedOutput {
          offset: *offset,
          output: output.clone(),
          outpoint: OutPoint::new(tx.compute_txid(), vout),
        });
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

**File:** networks/bitcoin/src/wallet/send.rs (L275-283)
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
