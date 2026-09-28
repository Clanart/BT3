### Title
Forged `ReceivedOutput` records can report unspendable Bitcoin as spendable - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
`ReceivedOutput::read` accepts an attacker-controlled offset, `TxOut`, and `OutPoint` as an opaque tuple, without requiring the output to exist or proving that it is controlled by the expected threshold key. [1](#0-0) 

`SignableTransaction::new` then trusts the supplied outpoint and output value when constructing inputs and calculating available funds. [2](#0-1)  Although `multisig` checks that the claimed `script_pubkey` matches the key derived from the supplied offset, it does not check that the referenced outpoint exists or contains that output. [3](#0-2) 

### Finding Description
A `ReceivedOutput` is documented as “a spendable output,” but deserialization reconstructs three independently supplied fields:

- `offset`
- `output`
- `outpoint`

No consistency or provenance check is performed. [4](#0-3) 

An attacker can therefore serialize an output whose script belongs to the threshold wallet, while pairing it with a nonexistent or unrelated `OutPoint` and an inflated value. `ReceivedOutput::read` accepts the record, `value()` reports the forged amount, and `SignableTransaction::new` treats it as available input value. [5](#0-4) 

The analogous missing-validation flaw is that syntactically valid fields are accepted as semantically valid spendable funds. Unlike the original report, this does not directly transfer existing Serai-controlled bitcoin to the attacker; it creates a false spendable-output record and can cause the wallet to construct and sign a transaction that Bitcoin consensus will reject.

### Impact Explanation
An unprivileged party who can feed crafted bytes to `ReceivedOutput::read` can cause arbitrary BTC value to be reported as received and spendable. [5](#0-4) 

If downstream code consumes the resulting `ReceivedOutput`, it can overstate balances, select nonexistent inputs, calculate an incorrect change amount, and obtain threshold signatures for a transaction that cannot be mined. The forged record is admitted to transaction construction without chain-state verification. [2](#0-1) 

This is a loss-of-funds/accounting-integrity issue rather than direct theft of an existing UTXO: funds may be credited as available even though they are not spendable.

### Likelihood Explanation
The forged bytes only require:

1. A valid scalar encoding for `offset`.
2. A syntactically valid consensus-encoded `TxOut`.
3. A syntactically valid consensus-encoded `OutPoint`.

The `TxOut` can use the threshold wallet’s normal P2TR script, so the later key/script check succeeds for offset zero. [3](#0-2)  The `OutPoint` can name a transaction which does not exist, does not have that output, or has a different value because deserialization does not authenticate it. [6](#0-5) 

### Recommendation
Do not treat `ReceivedOutput::read` as sufficient validation for untrusted input.

Recommended fixes:

- Construct `ReceivedOutput` only through `Scanner::scan_transaction` / `scan_block`, where the outpoint and output are obtained from an authenticated Bitcoin block.
- If untrusted serialization must be supported, include or supply the expected base key and verify `output.script_pubkey == p2tr_script_buf(base_key + GENERATOR * offset)`.
- Before spending, verify that `outpoint` resolves to the exact serialized `TxOut`, including script and value, and that it remains unspent.
- Store authenticated scanner provenance with serialized outputs, or authenticate the serialized record with a MAC/signature.

### Proof of Concept
The following conceptual test constructs a `ReceivedOutput` naming a wallet-controlled P2TR script but a nonexistent outpoint and an arbitrary amount:

```rust
use bitcoin::{
  consensus::Encodable,
  hashes::Hash,
  Amount, OutPoint, ScriptBuf, TxOut, Txid,
};
use frost::{curve::Secp256k1, ThresholdKeys};
use k256::Scalar;
use bitcoin_serai::wallet::{p2tr_script_buf, ReceivedOutput, SignableTransaction};

fn forged_output(keys: &ThresholdKeys<Secp256k1>) -> ReceivedOutput {
  let wallet_script: ScriptBuf = p2tr_script_buf(keys.group_key()).unwrap();

  let mut bytes = Vec::new();
  bytes.extend_from_slice(&Scalar::ZERO.to_bytes());

  TxOut {
    value: Amount::from_sat(100_000),
    script_pubkey: wallet_script,
  }
  .consensus_encode(&mut bytes)
  .unwrap();

  OutPoint {
    txid: Txid::all_zeros(),
    vout: 0,
  }
  .consensus_encode(&mut bytes)
  .unwrap();

  // Accepted even though txid all_zeros():0 was never observed on-chain.
  ReceivedOutput::read(&mut bytes.as_slice()).unwrap()
}
```

The returned object reports `value() == 100_000`. Passing it to `SignableTransaction::new` causes that fabricated amount to be included in `input_sat`, and `multisig` accepts the wallet-controlled script because only the claimed `prevout.script_pubkey` is checked against the offset-derived key. [2](#0-1) [3](#0-2)

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L88-96)
```rust
/// A spendable output.
#[derive(Clone, PartialEq, Eq, Debug)]
pub struct ReceivedOutput {
  // The scalar offset to obtain the key usable to spend this output.
  offset: Scalar,
  // The output to spend.
  output: TxOut,
  // The TX ID and vout of the output to spend.
  outpoint: OutPoint,
```

**File:** networks/bitcoin/src/wallet/mod.rs (L115-133)
```rust
  /// The value of this output.
  pub fn value(&self) -> u64 {
    self.output.value.to_sat()
  }

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

**File:** networks/bitcoin/src/wallet/send.rs (L273-281)
```rust
  pub fn multisig(self, keys: &ThresholdKeys<Secp256k1>) -> Option<TransactionMachine> {
    let mut sigs = vec![];
    for i in 0 .. self.tx.input.len() {
      let offset = keys.clone().offset(self.offsets[i]);
      if p2tr_script_buf(offset.group_key())? != self.prevouts[i].script_pubkey {
        None?;
      }

      sigs.push(AlgorithmMachine::new(Schnorr::new(), keys.clone().offset(self.offsets[i])));
```
