### Title
Dust outputs can make maximal spends fail by increasing the fee more than the available input value - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`Scanner::scan_transaction` accepts any output paying a watched Taproot script, without checking whether its value is economically spendable. `SignableTransaction::new` then adds every returned `ReceivedOutput` as an input and recalculates the fee from the total input count. An unprivileged actor can therefore send a dust output to a watched address; when the wallet attempts to spend all or nearly all of its available balance, the dust input contributes less value than the fee required to spend it and causes `TransactionError::NotEnoughFunds`. [1](#0-0) [2](#0-1) [3](#0-2) 

### Finding Description
The wallet’s `DUST` limit is only applied to requested payment outputs, not to incoming outputs accepted by the scanner or to inputs selected for spending. [1](#0-0) [4](#0-3)  Each scanned output unconditionally becomes a `TxIn`, while the transaction weight and required fee are calculated using `tx_ins.len()`. [2](#0-1) [5](#0-4) 

The modeled input has a fixed-size outpoint, empty script signature, maximum sequence, and a 64-byte Taproot witness, so an additional input has a nonzero marginal fee independent of its value. [6](#0-5)  If the incoming output’s value is below that marginal fee, total inputs increase by less than `needed_fee`, and a previously valid maximal payment now fails the strict `input_sat < payment_sat + needed_fee` check. [7](#0-6) 

### Impact Explanation
An attacker can repeatedly send mined dust outputs to a watched Serai Bitcoin address and prevent wallet flows which spend all or nearly all of the scanned balance. The wallet must either lower the payment, exclude the dust manually, or remain unable to construct the transaction. [7](#0-6) 

At sufficiently high fee rates, even outputs near the 546-satoshi wallet dust limit are net-negative to spend because the additional input weight costs more than the output contributes. Enough dust inputs can also push the constructed transaction over `MAX_STANDARD_TX_WEIGHT`, producing `TooLargeTransaction` even for non-maximal payments. [8](#0-7) [9](#0-8) 

### Likelihood Explanation
The attacker only needs to send ordinary Bitcoin outputs to the watched Taproot script; `scan_transaction` selects outputs solely by matching `script_pubkey` and does not impose a minimum value. [1](#0-0)  Dust outputs may require miner cooperation rather than default relay, but the attack is reachable through public Bitcoin transactions and becomes effective whenever the wallet consumes newly scanned outputs before constructing a maximal spend. [10](#0-9) 

### Recommendation
Apply an input-value/economic-spendability threshold before accepting a scanned output as a spendable `ReceivedOutput`, or implement coin selection in `SignableTransaction::new` which excludes inputs whose value is below their marginal fee. [10](#0-9) [2](#0-1)  For sweep-style calls, add explicit semantics such as `send_max` or `min_amount`, subtract the actual fee from the requested payment, and cap selected inputs below the standard transaction weight limit rather than blindly consuming every scanned output. [11](#0-10) 

### Proof of Concept
Conceptual execution against the in-scope wallet API:

1. Create a `Scanner` for the wallet’s even Taproot group key. [12](#0-11) 
2. Obtain a legitimate UTXO worth `V`.
3. Determine the maximal payment `P` such that `V >= P + needed_fee` for a transaction containing only that UTXO.
4. Before constructing the final spend, the attacker mines a transaction containing an output worth `545` satoshis to the same `p2tr_script_buf(group_key)` script. [13](#0-12) [1](#0-0) 
5. `scanner.scan_transaction(attacker_tx)` returns the dust as a `ReceivedOutput`; no amount check rejects it. [10](#0-9) 
6. Calling `SignableTransaction::new(vec![legitimate, attacker_dust], &[(destination, P)], None, None, 20)` includes both outpoints as inputs, recalculates weight for two inputs, and returns `TransactionError::NotEnoughFunds` because the additional input adds only `545` sats while its marginal fee at 20 sat/vB exceeds that amount. [2](#0-1) [3](#0-2) 

The relevant checks are visible in the production flow: payment dust is rejected, but input dust is not; every supplied `ReceivedOutput` becomes an input; and the requested amount is compared against the recalculated fee without reducing the payment to the still-available amount. [14](#0-13) [3](#0-2)

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L80-85)
```rust
pub fn p2tr_script_buf(key: ProjectivePoint) -> Option<ScriptBuf> {
  if key.to_encoded_point(true).tag() != Tag::CompressedEvenY {
    return None;
  }

  Some(ScriptBuf::new_p2tr_tweaked(TweakedPublicKey::dangerous_assume_tweaked(x_only(&key))))
```

**File:** networks/bitcoin/src/wallet/mod.rs (L162-165)
```rust
  pub fn new(key: ProjectivePoint) -> Option<Scanner> {
    let mut scripts = HashMap::new();
    scripts.insert(p2tr_script_buf(key)?, Scalar::ZERO);
    Some(Scanner { key, scripts })
```

**File:** networks/bitcoin/src/wallet/mod.rs (L199-210)
```rust
  pub fn scan_transaction(&self, tx: &Transaction) -> Vec<ReceivedOutput> {
    let mut res = Vec::new();
    for (vout, output) in tx.output.iter().enumerate() {
      // If the vout index exceeds 2**32, stop scanning outputs
      let Ok(vout) = u32::try_from(vout) else { break };

      if let Some(offset) = self.scripts.get(&output.script_pubkey) {
        res.push(ReceivedOutput {
          offset: *offset,
          output: output.clone(),
          outpoint: OutPoint::new(tx.compute_txid(), vout),
        });
```

**File:** networks/bitcoin/src/wallet/send.rs (L27-32)
```rust
#[rustfmt::skip]
// https://github.com/bitcoin/bitcoin/blob/306ccd4927a2efe325c8d84be1bdb79edeb29b04/src/policy/policy.cpp#L26-L63
// As the above notes, a lower amount may not be considered dust if contained in a SegWit output
// This doesn't bother with delineation due to how marginal these values are, and because it isn't
// worth the complexity to implement differentation
pub const DUST: u64 = 546;
```

**File:** networks/bitcoin/src/wallet/send.rs (L68-83)
```rust
    let mut tx = Transaction {
      version: Version(2),
      lock_time: LockTime::ZERO,
      input: vec![
        TxIn {
          // This is a fixed size
          // See https://developer.bitcoin.org/reference/transactions.html#raw-transaction-format
          previous_output: OutPoint::default(),
          // This is empty for a Taproot spend
          script_sig: ScriptBuf::new(),
          // This is fixed size, yet we do use Sequence::MAX
          sequence: Sequence::MAX,
          // Our witnesses contains a single 64-byte signature
          witness: Witness::from_slice(&[vec![0; 64]])
        };
        inputs
```

**File:** networks/bitcoin/src/wallet/send.rs (L165-185)
```rust
    for (_, amount) in payments {
      if *amount < DUST {
        Err(TransactionError::DustPayment)?;
      }
    }

    if data.as_ref().map_or(0, Vec::len) > 80 {
      Err(TransactionError::TooMuchData)?;
    }

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

**File:** networks/bitcoin/src/wallet/send.rs (L204-243)
```rust
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

    if tx_outs.is_empty() {
      Err(TransactionError::NoOutputs)?;
    }

    if weight > u64::from(bitcoin::policy::MAX_STANDARD_TX_WEIGHT) {
      Err(TransactionError::TooLargeTransaction)?;
    }
```
