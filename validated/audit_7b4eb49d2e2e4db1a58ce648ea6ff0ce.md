### Title
`ReceivedOutput::read()` accepts arbitrary output metadata outside the scanner’s fixed key-to-script association - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
`Scanner` binds a fixed Bitcoin key to recognized Taproot scripts and constructs `ReceivedOutput` only for transaction outputs matching that association. [1](#0-0)  `ReceivedOutput::read()` bypasses this process by accepting an independently supplied scalar offset, `TxOut`, and `OutPoint` without verifying that the output was seen on chain or that its script corresponds to the supplied offset. [2](#0-1) 

### Finding Description
`Scanner::new()` initializes a `scripts` map for one fixed key, while `register_offset()` derives each additional recognized script from `key + offset * G`. [3](#0-2)  `scan_transaction()` only reports outputs whose `script_pubkey` is present in that fixed map. [4](#0-3) 

The deserializer creates the same `ReceivedOutput` type from untrusted bytes, but it does not have access to, nor consult, the scanner’s fixed `key`/`scripts` association. [2](#0-1)  `SignableTransaction::new()` then trusts the supplied `output.value`, `outpoint`, and `offset` when constructing inputs, offsets, and the Taproot `Prevouts::All` commitment. [5](#0-4) [6](#0-5)  `multisig()` validates only that the supplied script equals the script derived from the supplied offset; it does not verify that the supplied outpoint or amount exists on chain. [7](#0-6) 

### Impact Explanation
An unprivileged party can serialize a `ReceivedOutput` claiming that an arbitrary nonexistent, spent, or amount-mismatched outpoint belongs to the wallet, provided the claimed script is derivable from a known offset such as the scanner’s always-registered zero offset. [8](#0-7)  The wallet can consequently report unavailable funds and coordinate threshold signatures over a fabricated `Prevouts::All` commitment. [6](#0-5)  The resulting transaction is not spendable against the actual UTXO set because Bitcoin commits to each claimed previous output and amount. [9](#0-8) [6](#0-5) 

### Likelihood Explanation
No private key material is required; the attacker only needs to supply bytes accepted by the public `ReceivedOutput::read()` API and know the public wallet key or one of its derived scripts. [2](#0-1)  The zero-offset base script is automatically registered by `Scanner::new()`, making a wallet-associated script straightforward to construct. [8](#0-7) 

### Recommendation
Do not deserialize `ReceivedOutput` as three independent trusted fields. Either authenticate scanner-produced values before storage/transmission, or reconstruct the output from `(outpoint, offset)` plus chain-verified `TxOut` data and require `p2tr_script_buf(scanner_key + offset * G) == output.script_pubkey` before treating it as received. The scanner should remain the sole authority establishing the key-offset-script association. [10](#0-9) 

### Proof of Concept
```rust
use bitcoin::{Amount, OutPoint, TxOut};
use networks_bitcoin::wallet::{p2tr_script_buf, ReceivedOutput, SignableTransaction};
use k256::Scalar;

// `group_key` is the public Taproot base key; no private key is needed.
let wallet_script = p2tr_script_buf(group_key).unwrap();

let fake = ReceivedOutput {
    // Offset 0 is automatically associated with the base key by Scanner::new.
    offset: Scalar::ZERO,
    output: TxOut {
        // Fabricated amount.
        value: Amount::from_sat(1_000_000),
        script_pubkey: wallet_script,
    },
    // Nonexistent or already-spent outpoint.
    outpoint: OutPoint::null(),
};

let mut encoded = Vec::new();
fake.write(&mut encoded).unwrap();
let forged = ReceivedOutput::read(&mut encoded.as_slice()).unwrap();

// Accepted as an input and included in `Prevouts::All`.
let tx = SignableTransaction::new(
    vec![forged],
    &[(payment_script, 546)],
    None,
    None,
    1,
).unwrap();

// This passes the implementation's script check even though the claimed
// outpoint/value does not exist as a spendable UTXO.
assert!(tx.multisig(&keys).is_some());
```

`ReceivedOutput`’s private fields mean the direct initializer is illustrative; an attacker obtains the same result by concatenating the canonical scalar, `TxOut`, and `OutPoint` encodings expected by `ReceivedOutput::read()`. [11](#0-10)

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L120-148)
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

  /// Write a ReceivedOutput to a generic satisfying Write.
  pub fn write<W: Write>(&self, w: &mut W) -> io::Result<()> {
    w.write_all(&self.offset.to_bytes())?;
    w.write_all(&serialize(&self.output))?;
    w.write_all(&serialize(&self.outpoint))
  }

  /// Serialize a ReceivedOutput to a `Vec<u8>`.
  pub fn serialize(&self) -> Vec<u8> {
    let mut res = Vec::new();
    self.write(&mut res).unwrap();
    res
  }
```

**File:** networks/bitcoin/src/wallet/mod.rs (L153-196)
```rust
pub struct Scanner {
  key: ProjectivePoint,
  scripts: HashMap<ScriptBuf, Scalar>,
}

impl Scanner {
  /// Construct a Scanner for a key.
  ///
  /// Returns None if this key can't be scanned for.
  pub fn new(key: ProjectivePoint) -> Option<Scanner> {
    let mut scripts = HashMap::new();
    scripts.insert(p2tr_script_buf(key)?, Scalar::ZERO);
    Some(Scanner { key, scripts })
  }

  /// Register an offset to scan for.
  ///
  /// Due to Bitcoin's requirement that points are even, not every offset may be used.
  /// If an offset isn't usable, it will be incremented until it is. If this offset is already
  /// present, None is returned. Else, Some(offset) will be, with the used offset.
  ///
  /// This means offsets are surjective, not bijective, and the order offsets are registered in
  /// may determine the validity of future offsets.
  ///
  /// The offsets registered must be securely generated. Arbitrary offsets may introduce a script
  /// path into the output, allowing the output to be spent by satisfaction of an arbitrary script
  /// (not by the signature of the key).
  pub fn register_offset(&mut self, mut offset: Scalar) -> Option<Scalar> {
    // This loop will terminate as soon as an even point is found, with any point having a ~50%
    // chance of being even
    // That means this should terminate within a very small amount of iterations
    loop {
      match p2tr_script_buf(self.key + (ProjectivePoint::GENERATOR * offset)) {
        Some(script) => {
          if self.scripts.contains_key(&script) {
            None?;
          }
          self.scripts.insert(script, offset);
          return Some(offset);
        }
        None => offset += Scalar::ONE,
      }
    }
  }
```

**File:** networks/bitcoin/src/wallet/mod.rs (L199-211)
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
      }
```

**File:** networks/bitcoin/src/wallet/send.rs (L150-185)
```rust
  pub fn new(
    mut inputs: Vec<ReceivedOutput>,
    payments: &[(ScriptBuf, u64)],
    change: Option<ScriptBuf>,
    data: Option<Vec<u8>>,
    fee_per_vbyte: u64,
  ) -> Result<SignableTransaction, TransactionError> {
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

**File:** networks/bitcoin/src/wallet/send.rs (L270-282)
```rust
  /// Create a multisig machine for this transaction.
  ///
  /// Returns None if the wrong keys are used.
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
