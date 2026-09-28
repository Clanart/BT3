### Title
Unchecked Bitcoin value arithmetic allows malformed transactions and denial of service - ([File: networks/bitcoin/src/wallet/send.rs](networks/bitcoin/src/wallet/send.rs))

### Summary
`SignableTransaction::new` performs unchecked `u64` arithmetic on attacker-influenced input values, payment values, and fee calculations. A crafted `ReceivedOutput` can declare a `TxOut` with an arbitrary `u64` value because deserialization does not validate that the claimed value corresponds to an actual confirmed UTXO. These values are summed into `input_sat`, while payment values are summed into `payment_sat` and then added to `needed_fee` without checked arithmetic. [1](#0-0) [2](#0-1) [3](#0-2) 

### Finding Description
`ReceivedOutput::read` accepts a scalar offset, a consensus-decoded `TxOut`, and an `OutPoint` from untrusted bytes. [1](#0-0)  The transaction builder subsequently sums every claimed input amount into `input_sat` and every requested payment into `payment_sat` using `Iterator::sum::<u64>()`. [2](#0-1) 

The solvency check evaluates `input_sat < (payment_sat + needed_fee)` directly. [3](#0-2)  Both the `payment_sat + needed_fee` addition and the earlier summations can overflow `u64`. [2](#0-1) [3](#0-2)  In builds with integer-overflow checks enabled, this produces a panic; in normal wrapping arithmetic, it can make an economically impossible transaction appear funded. [4](#0-3) 

After the check, the attacker-controlled payment amounts are placed directly into transaction outputs. [5](#0-4)  `multisig` then creates one FROST signing machine per input, and `sign` calculates a Taproot signature hash for each input over the resulting transaction. [6](#0-5) [7](#0-6) 

### Impact Explanation
An unprivileged party can cause the wallet to construct and threshold-sign a transaction containing output amounts whose sum exceeds the available input value. [5](#0-4) [7](#0-6)  Such a transaction is invalid or unusable, while the overflow can also panic under checked-arithmetic builds. [4](#0-3)  This is a reachable availability failure in an external-input handling path and can induce signing work on an unintended, malformed transaction. [1](#0-0) [8](#0-7) 

### Likelihood Explanation
The trigger only requires a serialized `ReceivedOutput` or payment request carrying very large `u64` amounts, followed by construction and signing of a `SignableTransaction`. [1](#0-0) [9](#0-8)  The code validates dust and output presence but does not use checked summation or checked addition for the security-sensitive balance equation. [10](#0-9) [2](#0-1) [4](#0-3) 

### Recommendation
Use `checked_add` or `checked_sum` for `input_sat`, `payment_sat`, `payment_sat + needed_fee`, `fee_per_vbyte * vbytes`, and `input_sat - payment_sat - fee`. [2](#0-1) [3](#0-2) [11](#0-10)  Reject any overflow as `TransactionError::NotEnoughFunds` or a new invalid-amount error rather than permitting wrapping or panicking. [12](#0-11)  Ideally, also authenticate or otherwise bind deserialized `ReceivedOutput` amounts to scanner-observed transactions before using them for wallet construction. [1](#0-0) [13](#0-12) 

### Proof of Concept
Conceptually, provide two serialized `ReceivedOutput` values whose `TxOut` amounts are each `u64::MAX`, or request a single payment of `u64::MAX` with a nonzero fee rate. [1](#0-0) [2](#0-1)  The input summation wraps in the first case, while `payment_sat + needed_fee` wraps in the second. [3](#0-2) 

```rust
// The output script can target the wallet's P2TR script; only the
// TxOut amount and OutPoint need to be attacker-controlled here.
let crafted_1 = received_output_with_amount_and_script(u64::MAX, wallet_script.clone());
let crafted_2 = received_output_with_amount_and_script(u64::MAX, wallet_script.clone());

// input_sat wraps to u64::MAX - 1 instead of rejecting the impossible inputs.
let result = SignableTransaction::new(
    vec![crafted_1, crafted_2],
    &[(payment_script, 1_000)],
    None,
    None,
    1,
);
```

Alternatively:

```rust
// payment_sat is u64::MAX and needed_fee is nonzero, so their unchecked
// addition wraps before the solvency comparison.
let result = SignableTransaction::new(
    vec![valid_dust_received_output],
    &[(payment_script, u64::MAX)],
    None,
    None,
    1,
);
```

With overflow checks enabled, either expression panics; otherwise, the wrapped comparison permits the malformed transaction to proceed into output construction and Taproot signing. [5](#0-4) [4](#0-3) [7](#0-6)

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L120-133)
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
```

**File:** networks/bitcoin/src/wallet/mod.rs (L198-214)
```rust
  /// Scan a transaction.
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
      }
    }
    res
  }
```

**File:** networks/bitcoin/src/wallet/send.rs (L34-50)
```rust
#[derive(Clone, PartialEq, Eq, Debug, Error)]
pub enum TransactionError {
  #[error("no inputs were specified")]
  NoInputs,
  #[error("no outputs were created")]
  NoOutputs,
  #[error("a specified payment's amount was less than bitcoin's required minimum")]
  DustPayment,
  #[error("too much data was specified")]
  TooMuchData,
  #[error("fee was too low to pass the default minimum fee rate")]
  TooLowFee,
  #[error("not enough funds for these payments")]
  NotEnoughFunds { inputs: u64, payments: u64, fee: u64 },
  #[error("transaction was too large")]
  TooLargeTransaction,
}
```

**File:** networks/bitcoin/src/wallet/send.rs (L150-156)
```rust
  pub fn new(
    mut inputs: Vec<ReceivedOutput>,
    payments: &[(ScriptBuf, u64)],
    change: Option<ScriptBuf>,
    data: Option<Vec<u8>>,
    fee_per_vbyte: u64,
  ) -> Result<SignableTransaction, TransactionError> {
```

**File:** networks/bitcoin/src/wallet/send.rs (L165-173)
```rust
    for (_, amount) in payments {
      if *amount < DUST {
        Err(TransactionError::DustPayment)?;
      }
    }

    if data.as_ref().map_or(0, Vec::len) > 80 {
      Err(TransactionError::TooMuchData)?;
    }
```

**File:** networks/bitcoin/src/wallet/send.rs (L175-191)
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

    let payment_sat = payments.iter().map(|payment| payment.1).sum::<u64>();
    let mut tx_outs = payments
      .iter()
      .map(|payment| TxOut { value: Amount::from_sat(payment.1), script_pubkey: payment.0.clone() })
      .collect::<Vec<_>>();
```

**File:** networks/bitcoin/src/wallet/send.rs (L204-221)
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
```

**File:** networks/bitcoin/src/wallet/send.rs (L224-234)
```rust
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
```

**File:** networks/bitcoin/src/wallet/send.rs (L273-284)
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
