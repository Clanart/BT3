### Title
Untrusted `ReceivedOutput` deserialization bypasses scanner-derived ownership and UTXO validation - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
`Scanner::scan_transaction` only produces a `ReceivedOutput` after matching an on-chain output’s `script_pubkey` to a registered offset and recording the transaction’s real `OutPoint`. `ReceivedOutput::read` accepts an attacker-supplied offset, `TxOut`, and `OutPoint` without checking that the offset derives the output script or that the outpoint identifies a real UTXO. If those bytes are subsequently treated as spendable wallet input, `SignableTransaction` will account for the forged output and create a transaction for an unspendable input.

### Finding Description
`Scanner` maintains the trusted association between wallet offsets and spendable Taproot scripts. During scanning, it looks up each transaction output’s `script_pubkey`, copies the actual output, and binds it to the containing transaction ID and output index. [1](#0-0) 

`ReceivedOutput::read` has no equivalent validation. It independently deserializes the scalar offset, claimed previous output, and claimed outpoint, then returns the composite object directly. [2](#0-1) 

The spending path trusts that object in two stages:

- `SignableTransaction::new` sums `input.output.value` as available funds and uses `input.outpoint` as the transaction input. [3](#0-2) 
- `SignableTransaction::multisig` checks only that the claimed offset produces the claimed `script_pubkey`; it does not check that `outpoint` references an existing UTXO or that the supplied `TxOut` matches chain state. [4](#0-3) 

Consequently, the scanner path enforces provenance while the deserialization path acts as a direct object-retrieval path without that provenance check.

### Impact Explanation
An attacker who can supply untrusted `ReceivedOutput` bytes can cause a wallet or coordination layer to report a non-existent, already-spent, or incorrectly-valued UTXO as received funds. The object can pass `SignableTransaction::multisig` if its `script_pubkey` is constructed from the wallet key and claimed offset, while its outpoint or amount is false. The resulting signed transaction either references a non-existent input or commits to a `Prevouts::All` value inconsistent with consensus state, making it invalid. This can corrupt balance accounting, reserve wallet capacity, or cause an intended payment to fail after signing.

### Likelihood Explanation
Exploitation requires the application to deserialize `ReceivedOutput` from attacker-controlled or unauthenticated storage/messages and then treat the result as wallet-owned input. The vulnerable primitive is a public deserialization API, and the only cryptographic consistency check occurs later and covers the offset-to-script relationship, not UTXO authenticity or output-value provenance. An attacker does not need validator privileges, key material, or a valid blockchain output to trigger the false accounting path.

### Recommendation
Do not expose `ReceivedOutput::read` as an ownership-establishing API for untrusted input. Require received-output construction through `Scanner`, or introduce an authenticated deserialization path that takes the scanner/key context and verifies:

1. `p2tr_script_buf(key + G * offset) == output.script_pubkey`.
2. The claimed `OutPoint` resolves to an unspent chain UTXO.
3. The resolved consensus `TxOut` exactly equals the supplied `TxOut`.

For untrusted network input, transmit only the outpoint and resolve the previous output from an authenticated chain/RPC source before constructing `ReceivedOutput`.

### Proof of Concept
A source-level reproduction is:

```rust
use bitcoin::{OutPoint, TxOut, Transaction, Txid};
use k256::Scalar;
use std::io::Cursor;

use bitcoin_serai::wallet::{
  p2tr_script_buf, ReceivedOutput, Scanner, SignableTransaction,
};

fn forged_received_output(
  scanner_key: k256::ProjectivePoint,
) -> Vec<u8> {
  // A valid offset/script pair may be obtained through Scanner::register_offset.
  // The outpoint and amount below are attacker-controlled and are not checked
  // against the blockchain by ReceivedOutput::read.
  let offset = Scalar::ZERO;
  let script = p2tr_script_buf(scanner_key).unwrap();

  let fake_output = TxOut {
    value: bitcoin::Amount::from_sat(100_000),
    script_pubkey: script,
  };
  let fake_outpoint = OutPoint {
    txid: Txid::all_zeros(),
    vout: 0,
  };

  let mut bytes = Vec::new();
  bytes.extend(offset.to_bytes());
  bytes.extend(bitcoin::consensus::encode::serialize(&fake_output));
  bytes.extend(bitcoin::consensus::encode::serialize(&fake_outpoint));
  bytes
}
```

Passing those bytes to `ReceivedOutput::read` succeeds because the function performs no scanner or UTXO lookup. [5](#0-4) 

The returned object is then accepted by `SignableTransaction::new`, which counts its claimed value and embeds its claimed outpoint. [3](#0-2)  If the script was formed from the wallet key and offset, `multisig` accepts the input because that check validates only the script/offset relationship. [6](#0-5)  The transaction is therefore constructed and signed for a `Txid::all_zeros():0` input that was never proven to exist or be spendable.

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

**File:** networks/bitcoin/src/wallet/mod.rs (L198-210)
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
