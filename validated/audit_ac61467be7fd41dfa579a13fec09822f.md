### Title
Untrusted `ReceivedOutput` bytes can register nonexistent or mismatched Bitcoin UTXOs as spendable - ([File: networks/bitcoin/src/wallet/mod.rs](networks/bitcoin/src/wallet/mod.rs))

### Summary
`ReceivedOutput::read` accepts an attacker-supplied scalar offset, `TxOut`, and `OutPoint` without proving that the outpoint exists or that the claimed `TxOut` is the output at that outpoint. [1](#0-0)  The resulting object exposes the attacker-controlled value through `value()` and returns the attacker-controlled outpoint through `outpoint()`. [2](#0-1) 

### Finding Description
The deserialization is a raw claim of ownership rather than a proof of receipt: it reads `offset`, `output`, and `outpoint` independently and returns them as a spendable output. [3](#0-2)  `SignableTransaction::new` then trusts `input.output.value` for the input amount and `input.outpoint` for the transaction input. [4](#0-3)  During signing setup, the code checks only that the supplied `prevouts[i].script_pubkey` equals the script derived from the threshold key plus the supplied offset; it does not verify that this `TxOut` is actually locked in the referenced UTXO. [5](#0-4) 

### Impact Explanation
An unprivileged party who can provide serialized `ReceivedOutput` bytes can cause an arbitrary outpoint and value to be represented as funds controlled by the threshold group. [6](#0-5)  Downstream balance reporting also trusts the embedded `TxOut` value. [7](#0-6)  If the fake output is used to build and sign a transaction, the transaction commits to the claimed prevout data but remains unbroadcastable because the referenced on-chain UTXO is absent or has different contents. [8](#0-7)  This produces the accepted impact of funds reported as received while not being spendable, and can additionally waste signing rounds on an invalid transaction. [9](#0-8) 

### Likelihood Explanation
The attack requires only byte-level control over an input passed to `ReceivedOutput::read`; no validator key, peer privilege, leaked secret, or malformed curve implementation is needed. [10](#0-9)  The exploit is deterministic because all decisive fields—the offset, value, script, transaction ID, and vout—are supplied directly by the attacker. [11](#0-10) 

### Recommendation
Do not treat `ReceivedOutput` as an authenticity-bearing, self-validating record. Either make `ReceivedOutput::read` crate-private or clearly distinguish it as trusted-state deserialization, and provide a separate verified constructor for untrusted data. Before reporting or spending a decoded output, retrieve the referenced outpoint from the Bitcoin chain and require equality between the decoded `TxOut` and the on-chain output, including script and value. [12](#0-11)  Also bind the output to the scanner’s registered script-to-offset map so an attacker cannot pair a valid script with an unrelated outpoint. [13](#0-12) 

### Proof of Concept
```rust
use bitcoin::{
  consensus::Encodable,
  hashes::Hash,
  Amount, OutPoint, ScriptBuf, TxOut, Txid,
};
use bitcoin_serai::{
  bitcoin,
  wallet::{p2tr_script_buf, ReceivedOutput},
};
use frost::curve::Secp256k1;
use frost::ThresholdKeys;
use k256::{ProjectivePoint, Scalar};

fn forged_received_output(keys: &ThresholdKeys<Secp256k1>) -> Vec<u8> {
  let offset = Scalar::ZERO;

  // A script matching the group key passes SignableTransaction::multisig's
  // offset/script consistency check.
  let claimed_output = TxOut {
    value: Amount::from_sat(1_000_000),
    script_pubkey: p2tr_script_buf(keys.group_key()).unwrap(),
  };

  // Attacker-selected outpoint: nonexistent, already spent, or containing a
  // different TxOut.
  let fake_outpoint = OutPoint {
    txid: Txid::from_byte_array([0x42; 32]),
    vout: 0,
  };

  let mut bytes = Vec::new();
  bytes.extend(offset.to_bytes());
  claimed_output.consensus_encode(&mut bytes).unwrap();
  fake_outpoint.consensus_encode(&mut bytes).unwrap();
  bytes
}
```

Deserialization succeeds despite the outpoint being fabricated. [10](#0-9) 

```rust
let bytes = forged_received_output(&keys);
let forged = ReceivedOutput::read(&mut bytes.as_slice()).unwrap();

assert_eq!(forged.value(), 1_000_000);
assert_eq!(forged.outpoint().vout, 0);
```

`SignableTransaction::new` uses the forged `1_000_000`-sat value and fake outpoint to construct transaction inputs. [4](#0-3) 

```rust
let tx = SignableTransaction::new(
  vec![forged],
  &[(destination_script, 900_000)],
  None,
  None,
  10,
).unwrap();

// This returns Some because only offset -> script consistency is checked.
assert!(tx.multisig(&keys).is_some());
```

The resulting transaction is invalid on Bitcoin because its input references an outpoint that does not contain the claimed `TxOut`. [14](#0-13)

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L90-97)
```rust
pub struct ReceivedOutput {
  // The scalar offset to obtain the key usable to spend this output.
  offset: Scalar,
  // The output to spend.
  output: TxOut,
  // The TX ID and vout of the output to spend.
  outpoint: OutPoint,
}
```

**File:** networks/bitcoin/src/wallet/mod.rs (L110-118)
```rust
  /// The outpoint for this output.
  pub fn outpoint(&self) -> &OutPoint {
    &self.outpoint
  }

  /// The value of this output.
  pub fn value(&self) -> u64 {
    self.output.value.to_sat()
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

**File:** networks/bitcoin/src/wallet/mod.rs (L198-211)
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
```

**File:** networks/bitcoin/src/wallet/send.rs (L175-184)
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

**File:** processor/src/networks/bitcoin.rs (L128-130)
```rust
  fn balance(&self) -> ExternalBalance {
    ExternalBalance { coin: ExternalCoin::Bitcoin, amount: Amount(self.output.value()) }
  }
```

**File:** networks/bitcoin/src/rpc.rs (L210-224)
```rust
  /// Get a transaction by its hash.
  pub async fn get_transaction(&self, hash: &[u8; 32]) -> Result<Transaction, RpcError> {
    let hex = self.rpc_call::<String>("getrawtransaction", json!([hex::encode(hash)])).await?;
    let bytes: Vec<u8> = FromHex::from_hex(&hex)
      .map_err(|_| RpcError::InvalidResponse("node didn't use hex to encode the transaction"))?;
    let tx: Transaction = encode::deserialize(&bytes)
      .map_err(|_| RpcError::InvalidResponse("node sent an improperly serialized transaction"))?;

    let mut tx_hash = *tx.compute_txid().as_raw_hash().as_byte_array();
    tx_hash.reverse();
    if hash != &tx_hash {
      Err(RpcError::InvalidResponse("node replied with a different transaction"))?;
    }

    Ok(tx)
```
