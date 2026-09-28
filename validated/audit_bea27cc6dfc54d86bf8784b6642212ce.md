### Title

Duplicate `ReceivedOutput`s are counted as distinct input value, producing consensus-invalid transactions - (File: networks/bitcoin/src/wallet/send.rs)

### Summary

`SignableTransaction::new` sums every supplied `ReceivedOutput` into `input_sat` and converts every supplied entry into a transaction input without rejecting duplicate `OutPoint`s. [1](#0-0)  `ReceivedOutput::read` independently deserializes the offset, `TxOut`, and `OutPoint`, so untrusted serialized data can repeat the same output. [2](#0-1)  Later, `multisig` checks that each provided prevout script corresponds to the supplied offset, but does not verify that the `OutPoint`s are distinct. [3](#0-2) 

### Finding Description

The bug class is counting the same resource twice because two representations of it are treated as independent funds. For a duplicated `ReceivedOutput`, `input_sat` includes its value once per vector entry, while `tx_ins` includes the same `previous_output` once per vector entry. [1](#0-0)  The insufficient-funds check uses the inflated total, allowing payments that exceed the actual unique input value. [4](#0-3)  The constructed transaction retains both duplicate inputs, making it consensus-invalid because Bitcoin transactions cannot spend the same outpoint twice. [5](#0-4) 

Each duplicate input is nevertheless committed into the Taproot sighash through `Prevouts::All`, and each gets its own signature share. [6](#0-5)  During completion, the resulting Schnorr signatures are installed as witnesses for each duplicated input. [7](#0-6) 

### Impact Explanation

An attacker who supplies serialized `ReceivedOutput` data can cause signers to build and sign a transaction that cannot be accepted by Bitcoin consensus. The inflated `input_sat` may also conceal that the unique inputs cannot fund the requested payments and fee. This causes a signing-round denial of service and can make reported spendable funds unusable in the produced transaction.

### Likelihood Explanation

The input list and serialized `ReceivedOutput` bytes are public-facing inputs, and cloning or repeating one encoded output is sufficient to trigger the flaw. No malformed curve point, leaked secret, malicious validator, or protocol collusion is required. The issue is a deterministic input-validation failure rather than a probabilistic cryptographic break.

### Recommendation

Reject duplicated `OutPoint`s in `SignableTransaction::new` before summing input values or constructing `tx_ins`. For example, insert each `input.outpoint` into a `HashSet` and return a new `TransactionError::DuplicateInput` if insertion fails. Optionally validate that each supplied `ReceivedOutput` corresponds to an existing unspent output at the application boundary before requesting signatures.

### Proof of Concept

```rust
use bitcoin::ScriptBuf;
use bitcoin_serai::wallet::{ReceivedOutput, SignableTransaction};

// `received` is a valid scanned or deserialized ReceivedOutput for a 10_000-sat output.
let received: ReceivedOutput = /* Scanner output or ReceivedOutput::read bytes */;
let duplicated = received.clone();

let payment_script: ScriptBuf = /* destination script */;
let payment_amount = 19_000; // Greater than the real 10_000 sats, less than double-counted funds.

let tx = SignableTransaction::new(
    vec![received, duplicated],
    &[(payment_script, payment_amount)],
    None,
    None,
    1,
).unwrap();

assert_eq!(
    tx.transaction().input[0].previous_output,
    tx.transaction().input[1].previous_output,
);
```

`input_sat` is treated as `20_000`, so the payment passes the funds check even though only one `10_000`-sat outpoint exists. The resulting transaction contains the same previous output twice and is consensus-invalid.

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

**File:** networks/bitcoin/src/wallet/send.rs (L273-283)
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

**File:** networks/bitcoin/src/wallet/send.rs (L413-425)
```rust
  fn complete(
    mut self,
    mut shares: HashMap<Participant, Self::SignatureShare>,
  ) -> Result<Transaction, FrostError> {
    for (input, schnorr) in self.tx.input.iter_mut().zip(self.sigs.drain(..)) {
      let sig = schnorr.complete(
        shares.iter_mut().map(|(l, shares)| (*l, shares.remove(0))).collect::<HashMap<_, _>>(),
      )?;

      let mut witness = Witness::new();
      witness.push(sig);
      input.witness = witness;
    }
```

**File:** networks/bitcoin/src/wallet/mod.rs (L122-133)
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
```
