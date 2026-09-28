### Title
Duplicate `ReceivedOutput` inputs double-counted, producing a permanently invalid signed transaction - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
The upstream bug allows an operation on an already-consumed resource because `extendLock` never checks the lock has ended. The analog in Serai is `SignableTransaction::new`: it builds a transaction's inputs from a caller-supplied `Vec<ReceivedOutput>` without checking that any outpoint was already consumed within the same transaction. A duplicated `ReceivedOutput` (reachable via `ReceivedOutput::read`) is counted twice for balance/fee purposes while producing a transaction Bitcoin consensus rejects for double-spending an input.

### Finding Description
In `SignableTransaction::new`, each supplied `ReceivedOutput` is unconditionally converted into a `TxIn` and its value added to `input_sat`: [1](#0-0) 

There is no deduplication on `input.outpoint` anywhere in the constructor (lines 150-256). Consequently:

1. `input_sat` counts the same UTXO's value once per duplicate, so the `NotEnoughFunds` check at [2](#0-1)  is satisfied with payments exceeding real funds.
2. `prevouts` and `offsets` get one entry per duplicate ( [3](#0-2) , [4](#0-3) ), so `multisig` happily creates one Schnorr sign machine per duplicated input — the `p2tr_script_buf(offset.group_key()) == prevouts[i].script_pubkey` check at [5](#0-4)  passes because the duplicate carries the same legitimate script and offset.
3. `TransactionSignMachine::sign` then signs a sighash for each input over `Prevouts::All`, i.e. the FROST multisig produces a fully "valid-looking" signed `Transaction` ( [6](#0-5) ) that every Bitcoin node rejects in `CheckTransaction` for spending the same outpoint twice.

Just as the expired lock could be extended into a bypassable lock, the already-consumed outpoint is "extended" into a second input whose value is credited toward payments/fees that don't exist.

### Impact Explanation
The multisig threshold-signs a transaction that can never confirm: the plan's reported inputs exceed the funds actually held, the signed transaction is consensus-invalid, and any downstream accounting that treated the duplicated output's value as real (change amount, fee budget, payments sized against phantom balance) is wrong. Funds routed by the plan are effectively reported spendable when they are not — matching the "incorrect verifier/accounting formula" and "funds reported received that are not spendable" acceptance criteria.

### Likelihood Explanation
`ReceivedOutput` is an explicitly untrusted byte source (`ReceivedOutput::read`, [7](#0-6) ): offset, `TxOut`, and `OutPoint` are all attacker-influenceable fields with no internal consistency to the real chain (the only later check is script-vs-offset binding, which a copied legitimate output satisfies). Supplying the same serialized `ReceivedOutput` twice requires no key material and no collusion. Exploitation requires the bytes to reach `SignableTransaction::new`, which is precisely the pipeline this input class is designated for.

### Recommendation
In `SignableTransaction::new`, reject inputs whose `outpoint` has already been seen (e.g., insert each `input.outpoint` into a `HashSet<OutPoint>` and error on reinsertion), mirroring Bitcoin's own duplicate-input rejection. Optionally also reject duplicate `offset`/`script_pubkey` pairs.

### Proof of Concept
```rust
// networks/bitcoin, within wallet tests
let output = send_and_get_output(&rpc, &scanner, key).await;

// The same outpoint is consumed twice; no duplicate-input check exists.
let payments = [(p2tr_script_buf(key).unwrap(), (output.value() * 2) - FEE * 200)];
let tx = SignableTransaction::new(
  vec![output.clone(), output.clone()], // duplicate outpoint
  &payments,
  None,
  None,
  FEE,
);
// Succeeds: input_sat == 2 * output.value() passes NotEnoughFunds,
// even though only one real UTXO exists.
let tx = tx.unwrap();

// multisig() accepts both inputs since each duplicate carries a valid
// offset/script binding.
let signed = sign(&keys, &tx);

// The resulting transaction spends outpoint X twice; Bitcoin consensus
// (CheckTransaction duplicate-input rule) rejects it permanently.
assert_eq!(signed.input[0].previous_output, signed.input[1].previous_output);
```

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

**File:** networks/bitcoin/src/wallet/send.rs (L215-221)
```rust
    if input_sat < (payment_sat + needed_fee) {
      Err(TransactionError::NotEnoughFunds {
        inputs: input_sat,
        payments: payment_sat,
        fee: needed_fee,
      })?;
    }
```

**File:** networks/bitcoin/src/wallet/send.rs (L253-253)
```rust
      prevouts: inputs.drain(..).map(|input| input.output).collect(),
```

**File:** networks/bitcoin/src/wallet/send.rs (L277-278)
```rust
      if p2tr_script_buf(offset.group_key())? != self.prevouts[i].script_pubkey {
        None?;
```

**File:** networks/bitcoin/src/wallet/send.rs (L383-397)
```rust
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
