### Title
Duplicate `ReceivedOutput` inputs double-count funds and produce a consensus-invalid signed Bitcoin transaction - (File: `networks/bitcoin/src/wallet/send.rs`)

### Summary
`SignableTransaction::new` accepts a vector of `ReceivedOutput` values but never checks that their `OutPoint` identifiers are unique. A duplicated input contributes its value repeatedly to `input_sat` and creates multiple `TxIn`s referencing the same previous output. The resulting transaction can pass the funding checks and proceed through the FROST signing path, producing signatures for a transaction Bitcoin consensus rejects because it attempts to spend the same UTXO more than once.

### Finding Description
`SignableTransaction::new` takes `Vec<ReceivedOutput>` as public input. [1](#0-0)  The constructor sums every supplied `ReceivedOutput`'s value into `input_sat` without deduplicating `input.outpoint`. [2](#0-1)  It then converts every duplicate `ReceivedOutput` into a distinct `TxIn`, even though all duplicates refer to the same `previous_output`. [3](#0-2)  The solvency check compares the double-counted `input_sat` against payments and fees. [4](#0-3) 

A `ReceivedOutput` deserializer exists and directly accepts the serialized `TxOut` and `OutPoint` supplied by an untrusted byte stream. [5](#0-4)  No uniqueness property is enforced on deserialized `OutPoint` values before they are supplied to `SignableTransaction::new`. [6](#0-5)  Once constructed, `multisig` creates one Schnorr signing machine per duplicated input. [7](#0-6)  The signing path calculates a Taproot sighash for each input and generates a signature share for each duplicate. [8](#0-7) 

### Impact Explanation
An attacker or broken upstream caller can make the wallet believe it controls more spendable Bitcoin than it actually does by submitting the same UTXO multiple times. [2](#0-1)  This can cause the threshold signers to spend one signing session producing valid-looking witness signatures for a transaction that can never confirm. [8](#0-7)  If the returned transaction/eventuality is treated as the intended payment operation, the attempted transfer stalls and the actual UTXO remains unspent, while any accounting based on the inflated `input_sat` is incorrect. [4](#0-3) 

### Likelihood Explanation
The trigger only requires supplying the same `ReceivedOutput` twice to a public constructor. [1](#0-0)  The object is freely cloneable and deserializable, so duplication does not require validator compromise, malformed elliptic-curve data, or control of another signer. [9](#0-8)  The constructor has checks for empty inputs, missing outputs, dust, excessive data, low fees, insufficient funds, and transaction size, but no duplicate-outpoint check. [10](#0-9) [11](#0-10) 

### Recommendation
Reject duplicate outpoints before summing balances or constructing `tx_ins`. Add a `DuplicateInput`/similar variant to `TransactionError` and track `input.outpoint` in a `HashSet`; alternatively, sort and compare adjacent outpoints. The uniqueness check should use only `OutPoint`, not `(OutPoint, TxOut, offset)`, so a duplicate cannot bypass validation by changing non-identifier fields. Add a regression test asserting `SignableTransaction::new(vec![output.clone(), output], ...)` fails even when the doubled nominal balance would satisfy the payment.

### Proof of Concept
```rust
use std::collections::HashSet;

let output: ReceivedOutput = obtain_one_serai_utxo();
let duplicated_inputs = vec![output.clone(), output.clone()];

let tx = SignableTransaction::new(
  duplicated_inputs,
  &[(payment_script, output.value() + 1_000)],
  None,
  None,
  adequate_fee_per_vbyte,
)
.expect("duplicate outpoint is currently accepted");

assert_eq!(tx.transaction().input.len(), 2);
assert_eq!(
  tx.transaction().input[0].previous_output,
  tx.transaction().input[1].previous_output,
);

// The transaction proceeds to per-input FROST/Schnorr signing even though
// consensus forbids spending the same outpoint twice.
let machine = tx.multisig(&threshold_keys).unwrap();
```

The decisive invariant violation is visible before signing: both generated `TxIn`s carry the same `previous_output`, while `input_sat` has counted `output.value()` twice. [2](#0-1)

### Citations

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

**File:** networks/bitcoin/src/wallet/send.rs (L157-173)
```rust
    if inputs.is_empty() {
      Err(TransactionError::NoInputs)?;
    }

    if payments.is_empty() && change.is_none() && data.is_none() {
      Err(TransactionError::NoOutputs)?;
    }

    for (_, amount) in payments {
      if *amount < DUST {
        Err(TransactionError::DustPayment)?;
      }
    }

    if data.as_ref().map_or(0, Vec::len) > 80 {
      Err(TransactionError::TooMuchData)?;
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

**File:** networks/bitcoin/src/wallet/send.rs (L215-243)
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

    if tx_outs.is_empty() {
      Err(TransactionError::NoOutputs)?;
    }

    if weight > u64::from(bitcoin::policy::MAX_STANDARD_TX_WEIGHT) {
      Err(TransactionError::TooLargeTransaction)?;
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
