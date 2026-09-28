### Title
Untrusted `ReceivedOutput` declared value is trusted for input sums and the Taproot sighash, letting a forged output produce an unspendable signed transaction with fabricated balance - ([File: networks/bitcoin/src/wallet/send.rs])

### Summary
The bug class in the external report is "a declared deposit amount is trusted instead of the amount actually received," enabling balance-sheet discrepancies. The analog in Serai is `ReceivedOutput`, which bundles a self-declared `TxOut` (value + script_pubkey), an `OutPoint`, and a key `offset`, and is deserializable from untrusted bytes via `ReceivedOutput::read`. `SignableTransaction::new` trusts `input.output.value` for the input sum and fee/change math, and `TransactionSignMachine::sign` commits those declared `TxOut`s into the Taproot sighash via `Prevouts::All`, with no check that the declared value/script matches the actual on-chain UTXO the outpoint references.

### Finding Description
`ReceivedOutput::read` accepts an arbitrary offset, `TxOut`, and `OutPoint` from the wire with no validation [1](#0-0) . `SignableTransaction::new` then computes `input_sat` purely from the declared `input.output.value` fields, using it for the `NotEnoughFunds` check and for sizing the change output [2](#0-1) , [3](#0-2) . The declared `TxOut`s are stored as `prevouts` [4](#0-3)  and hashed into every input's signature via `Prevouts::All` [5](#0-4) .

The only consistency check performed is in `SignableTransaction::multisig`, which verifies `p2tr_script_buf(offset.group_key()) == prevouts[i].script_pubkey` [6](#0-5) . An attacker can satisfy this trivially — the script is derived from the public group key plus a self-chosen offset — while declaring an arbitrary `value` and an arbitrary `outpoint`. Nothing verifies the declared `TxOut` against the real UTXO set (which is impossible offline), so the discrepancy is analogous to fee-on-transfer bridging: the "deposited" amount recorded by the protocol differs from what actually exists on chain.

### Impact Explanation
An unprivileged party who can feed bytes to `ReceivedOutput::read` (an in-scope attack surface per the engagement rules) or otherwise inject a `ReceivedOutput` into a signing plan can cause the validator set to threshold-sign a transaction whose sighash commits to a fabricated prevout amount. Per BIP-341, nodes verify `sha_amounts` against the real spent outputs, so any mismatch yields an invalid signature and a transaction that can never confirm. Meanwhile Serai's internal accounting treats the declared inputs, computed change, and fee as real:

- Inflating the declared value inflates `input_sat`, bypassing `NotEnoughFunds` and minting a change output (or fee) backed by funds that do not exist — the protocol reports funds received/sent that are not spendable.
- The resulting transaction is permanently invalid, so the real UTXOs referenced remain unspent while the plan is considered executed, and outputs may be marked consumed in scheduler/scanner state — a funds-availability DoS against the multisig's treasury until the fraudulent inputs are identified and the plan rebuilt.

This matches the accepted impact class "funds reported received that are not spendable" and "accounting discrepancies" from the source report.

### Likelihood Explanation
Reachability requires that a `ReceivedOutput` reach `SignableTransaction::new`/`multisig` from attacker-influenced bytes rather than solely from `Scanner::scan_transaction` (which clones real chain outputs and is safe). The serialization path exists precisely for cross-node/plan transport of outputs, so a party supplying malformed plan data can trigger it. Exploitation requires no key material — only the ability to submit bytes deserialized by `ReceivedOutput::read` into a signing flow. Since detection only occurs when the transaction is broadcast and rejected, and each attempt burns a FROST signing session on the affected inputs, severity is Medium: it cannot steal funds (invalid signatures cannot move coins), but it corrupts accounting and locks funds behind an invalid signed transaction.

### Recommendation
- Treat `ReceivedOutput` as untrusted until its `outpoint` has been confirmed against the chain: before constructing `SignableTransaction`, verify via RPC (`gettxout`/block lookup) that the outpoint exists, is unspent, and its `TxOut` (value and script_pubkey) exactly equals the declared `output`. This is the analog of the report's `balanceAfter - balanceBefore != _depositAmount` check — verify actual rather than declared.
- Alternatively/additionally, carry a merkle/block inclusion proof with `ReceivedOutput` so signers can self-verify, or restrict `ReceivedOutput` construction for signing to scanner-produced values only (making the fields private with no `read`-then-sign path absent verification).
- At minimum, document at `ReceivedOutput::read` and `SignableTransaction::new` that the caller MUST have independently verified the output against confirmed chain state, and add a debug assertion path in `multisig()` checking `output.script_pubkey` (already done) plus a caller-supplied confirmation flag.

### Proof of Concept
```rust
// Conceptual PoC (regtest); shows a forged ReceivedOutput passes all checks
// and yields a signed transaction that consensus rejects.

// Real UTXO: outpoint P with 10_000 sats paying to p2tr(key).
// Attacker crafts bytes declaring the same outpoint/script but 10_000_000 sats:
let real_output: TxOut = fetch_txout(P); // value 10_000, script = p2tr(key)
let forged = ReceivedOutput {
  offset: Scalar::ZERO,
  output: TxOut { value: Amount::from_sat(10_000_000), script_pubkey: real_output.script_pubkey.clone() },
  outpoint: P,
};
let bytes = forged.serialize();
let ro = ReceivedOutput::read(&mut bytes.as_slice()).unwrap(); // accepted

// SignableTransaction::new succeeds: input_sat = 10_000_000
let stx = SignableTransaction::new(
  vec![ro],
  &[(dest_script, 5_000_000)],       // payment backed by nonexistent funds
  Some(change_script),               // change = 10_000_000 - 5_000_000 - fee
  None,
  FEE_PER_VBYTE,
).unwrap();

// multisig() passes: declared script_pubkey == p2tr(key + 0*G)
let machine = stx.multisig(&keys).unwrap();
// sign() commits the forged 10_000_000 value into Prevouts::All;
// the produced signature is invalid because the real prevout is 10_000.
let tx = run_frost_signing(machine);
// rpc.send_raw_transaction(&tx) -> rejected: "bad-txns-in-ne belowout" / sighash
// failure, while Serai already believes a 5_000_000 sat payment and ~5_000_000
// sat change output exist.
```

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L120-134)
```rust
  /// Read a ReceivedOutput from a generic satisfying Read.
  #[cfg(feature = "std")]
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

**File:** networks/bitcoin/src/wallet/send.rs (L175-176)
```rust
    let input_sat = inputs.iter().map(|input| input.output.value.to_sat()).sum::<u64>();
    let offsets = inputs.iter().map(|input| input.offset).collect();
```

**File:** networks/bitcoin/src/wallet/send.rs (L215-235)
```rust
    if input_sat < (payment_sat + needed_fee) {
      Err(TransactionError::NotEnoughFunds {
        inputs: input_sat,
        payments: payment_sat,
        fee: needed_fee,
      })?;
    }

    // If there's a change address, check if there's change to give it
    if let Some(change) = change {
      let (weight_with_change, vbytes_with_change) =
        Self::calculate_weight_vbytes(tx_ins.len(), payments, Some(&change));
      let fee_with_change = fee_per_vbyte * vbytes_with_change;
      if let Some(value) = input_sat.checked_sub(payment_sat + fee_with_change) {
        if value >= DUST {
          tx_outs.push(TxOut { value: Amount::from_sat(value), script_pubkey: change });
          weight = weight_with_change;
          needed_fee = fee_with_change;
        }
      }
    }
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

**File:** networks/bitcoin/src/wallet/send.rs (L273-282)
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
```

**File:** networks/bitcoin/src/wallet/send.rs (L373-391)
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
        )?;
```
