### Title
`SignableTransaction::new` accepts duplicate inputs, letting one UTXO back multiple inputs and producing an unspendable transaction — (File: networks/bitcoin/src/wallet/send.rs)

### Summary
The Lavarage bug is an invalid-validation flaw: `borrow` checked that *a* collateral deposit existed, but never checked it belonged to *that* position, so one collateral underwrote two borrows. `SignableTransaction::new` in `bitcoin-serai` exhibits the same shape: it sums `input.output.value` over the caller-supplied `Vec<ReceivedOutput>` to prove the transaction is funded, yet never verifies that each `previous_output` outpoint is distinct. A single UTXO listed twice is counted twice in `input_sat`, passes the `NotEnoughFunds` check, and is signed twice — yielding a transaction that is consensus-invalid (Bitcoin rejects transactions with duplicate inputs), so the "funded" payments can never execute.

### Finding Description
`SignableTransaction::new` builds `tx_ins` by mapping each `ReceivedOutput` to a `TxIn` keyed by `input.outpoint`, with no duplicate detection: [1](#0-0) 

The solvency check then trusts the summed value of those inputs: [2](#0-1) 

Because `input_sat` double-counts the duplicated UTXO, `input_sat < payment_sat + needed_fee` may be false even though the true spendable balance only covers the payments once. `multisig` then verifies each input's script matches the offset group key — which the duplicate passes trivially — and builds one `AlgorithmMachine` per input: [3](#0-2) 

`TransactionSignMachine::sign` produces a valid BIP-340 signature for every input including the duplicated one (`Prevouts::All` commits to the duplicated prevout twice, which is perfectly consistent): [4](#0-3) 

and `complete` assembles a fully-signed transaction that full nodes will reject as a duplicate-input double spend. `ReceivedOutput` is constructible from untrusted bytes via `ReceivedOutput::read`, so the malformed input vector is within the reachable-input surface.

### Impact Explanation
The protocol's threshold signers produce a valid signature over a transaction that can never confirm. Any accounting that treats `txid()`/`needed_fee()` as authoritative will believe `payment_sat` was sent while the UTXO only ever funded it once; the spend silently fails, stranding the "sent" funds and any downstream bookkeeping (payments presumed completed, inputs presumed consumed). For a multisig wallet driving protocol payouts, this is a per-input accounting violation exactly like the Lavarage case: one collateral/UTXO validated as backing for two logical obligations.

### Likelihood Explanation
Triggering requires a `Vec<ReceivedOutput>` containing the same outpoint twice — e.g., a duplicated entry sourced from untrusted `ReceivedOutput::read` bytes or an upstream scan returning a repeated output. It does not require key compromise, malicious validators, or a broken transcript; it requires only that the input list not be deduplicated before construction, which the library itself does not enforce.

### Recommendation
In `SignableTransaction::new`, reject duplicate `previous_output` outpoints (e.g., insert into a `HashSet<OutPoint>` and error on collision) before computing `input_sat` and building `tx_ins`, so that each claimed input provably corresponds to a distinct UTXO.

### Proof of Concept
1. Obtain a `ReceivedOutput` `o` (e.g., via `Scanner::scan_transaction` or `ReceivedOutput::read`).
2. Call `SignableTransaction::new(vec![o.clone(), o], &payments, change, None, fee)` where `payments` sums to just under `2 * o.value()` but more than `o.value() + needed_fee`.
3. Construction succeeds (no `NotEnoughFunds`), `multisig(&keys)` succeeds, and `preprocess`/`sign`/`complete` produce a fully signed `Transaction` whose two inputs share `o.outpoint`.
4. `send_raw_transaction` rejects the transaction with a duplicate-input error: the payments were "funded" by a UTXO that only exists once — one collateral, two obligations.

### Citations

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

**File:** networks/bitcoin/src/wallet/send.rs (L187-221)
```rust
    let payment_sat = payments.iter().map(|payment| payment.1).sum::<u64>();
    let mut tx_outs = payments
      .iter()
      .map(|payment| TxOut { value: Amount::from_sat(payment.1), script_pubkey: payment.0.clone() })
      .collect::<Vec<_>>();

    // Add the OP_RETURN output
    if let Some(data) = data {
      tx_outs.push(TxOut {
        value: Amount::ZERO,
        script_pubkey: ScriptBuf::new_op_return(
          PushBytesBuf::try_from(data)
            .expect("data didn't fit into PushBytes depsite being checked"),
        ),
      })
    }

    let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);

    let mut needed_fee = fee_per_vbyte * vbytes;
    // Technically, if there isn't change, this TX may still pay enough of a fee to pass the
    // minimum fee. Such edge cases aren't worth programming when they go against intent, as the
    // specified fee rate is too low to be valid
    // bitcoin::policy::DEFAULT_MIN_RELAY_TX_FEE is in sats/kilo-vbyte
    if needed_fee < ((u64::from(bitcoin::policy::DEFAULT_MIN_RELAY_TX_FEE) * vbytes) / 1000) {
      Err(TransactionError::TooLowFee)?;
    }

    if input_sat < (payment_sat + needed_fee) {
      Err(TransactionError::NotEnoughFunds {
        inputs: input_sat,
        payments: payment_sat,
        fee: needed_fee,
      })?;
    }
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

**File:** networks/bitcoin/src/wallet/send.rs (L373-397)
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
        shares.push(share);
        Ok(sig)
      })
      .collect::<Result<_, _>>()?;

    Ok((TransactionSignatureMachine { tx: self.tx.tx, sigs }, shares))
```
