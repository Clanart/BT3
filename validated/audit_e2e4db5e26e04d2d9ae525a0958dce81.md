### Title
Forged `ReceivedOutput` deserialization reports nonexistent Bitcoin funds - (File: `networks/bitcoin/src/wallet/mod.rs`)

### Summary

`ReceivedOutput::read` accepts independently serialized `offset`, `TxOut`, and `OutPoint` fields without validating that the outpoint exists or references the supplied output. A malicious byte stream can therefore create a `ReceivedOutput` claiming ownership of an unspendable or nonexistent UTXO. `SignableTransaction` subsequently trusts both halves of that forged tuple when constructing and signing a transaction.

### Finding Description

`ReceivedOutput::read` deserializes the three fields independently:

```rust
let offset = Secp256k1::read_F(r)?;
output = TxOut::consensus_decode(&mut buf_r)?;
outpoint = OutPoint::consensus_decode(&mut buf_r)?;
Ok(ReceivedOutput { offset, output, outpoint })
``` [1](#0-0) 

`SignableTransaction::new` then uses `input.outpoint` as the transaction input and separately uses `input.output` as the claimed prevout:

```rust
previous_output: input.outpoint,
``` [2](#0-1) 

```rust
prevouts: inputs.drain(..).map(|input| input.output).collect(),
``` [3](#0-2) 

`SignableTransaction::multisig` validates only that `key + offset * G` produces the claimed prevout's `script_pubkey`; it does not validate that the referenced outpoint contains that output or exists at all:

```rust
if p2tr_script_buf(offset.group_key())? != self.prevouts[i].script_pubkey {
  None?;
}
``` [4](#0-3) 

This violates the integrity boundary established by `Scanner`, where `outpoint` and `output` are both derived from the same confirmed transaction output. [5](#0-4) 

### Impact Explanation

An attacker who can supply serialized `ReceivedOutput` bytes can fabricate an apparent deposit. The output can use a script belonging to the threshold key so the offset validation succeeds, while the outpoint references a nonexistent transaction output or another UTXO.

Downstream code can then construct and produce signatures for a transaction spending that fake input. The resulting transaction will be rejected by Bitcoin consensus, but Serai's wallet layer has already reported or accepted value that cannot actually be spent.

### Likelihood Explanation

Likelihood depends on whether serialized `ReceivedOutput` values cross a trust boundary. The format itself provides no authenticity check, so any storage, synchronization, coordinator, peer, or integrator path that treats `ReceivedOutput::read` as parsing untrusted data is affected. The malicious encoding requires no cryptographic break and is straightforward to construct.

### Recommendation

Treat `ReceivedOutput` deserialization as untrusted data and require a UTXO/inclusion proof before using it. At minimum:

- Document that `ReceivedOutput::read` only parses trusted scanner output.
- Preferably expose a checked constructor that verifies `outpoint` against a confirmed transaction or Bitcoin UTXO source.
- Verify that the retrieved on-chain output exactly equals the stored `TxOut`, not merely that its `script_pubkey` matches.
- Continue excluding immature coinbase outputs before marking funds spendable.

### Proof of Concept

Conceptually, for a threshold key whose base group key is `K`:

1. Choose `offset = 0`.
2. Set `output.script_pubkey = p2tr_script_buf(K)` and `output.value = 1 BTC`.
3. Set `outpoint` to a nonexistent transaction ID and `vout = 0`.
4. Serialize these three fields and pass them to `ReceivedOutput::read`.

The reader accepts the object. `SignableTransaction::new` then creates a transaction input spending the fake outpoint and records the fabricated `TxOut` as its prevout. `multisig` accepts the input because the supplied script matches the threshold key, even though no spendable UTXO backs it.

### Citations

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

**File:** networks/bitcoin/src/wallet/mod.rs (L198-213)
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
```

**File:** networks/bitcoin/src/wallet/send.rs (L177-185)
```rust
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

**File:** networks/bitcoin/src/wallet/send.rs (L245-254)
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
