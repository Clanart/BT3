### Title
Unauthenticated `ReceivedOutput` deserialization permits phantom Bitcoin deposits - (File: `networks/bitcoin/src/wallet/mod.rs`)

### Summary
`ReceivedOutput::read` accepts an offset, arbitrary `TxOut`, and arbitrary `OutPoint` without proving that the referenced UTXO exists or that it corresponds to the claimed output. An attacker who knows a wallet’s Taproot script can supply bytes describing a wallet-controlled script at a fabricated or unrelated outpoint, causing the wallet to treat nonexistent funds as spendable. [1](#0-0) 

### Finding Description
`ReceivedOutput` is documented as “a spendable output,” but its deserializer only decodes three attacker-controlled fields: `offset`, `output`, and `outpoint`. [2](#0-1)  It performs no consistency check against the scanner’s registered scripts and no Bitcoin-chain check that the outpoint references a real UTXO containing the supplied `TxOut`. [1](#0-0) 

The trusted creation path obtains the offset by matching an on-chain output’s `script_pubkey` and derives the outpoint from the actual transaction being scanned. [3](#0-2)  Deserialization bypasses both checks, allowing an attacker to claim those relationships without the referenced transaction output existing.

`SignableTransaction::new` then trusts the supplied value when calculating available input funds and copies the supplied outpoint into the transaction input. [4](#0-3)  During signing setup, `multisig` verifies only that the stored `TxOut` script matches the wallet key adjusted by the supplied offset; it cannot verify that this `TxOut` actually exists at the supplied outpoint. [5](#0-4) 

### Impact Explanation
An attacker can cause an application to report or account for Bitcoin funds that are not actually spendable. If the claimed script corresponds to the wallet and an accepted offset, the transaction builder can create and sign a transaction spending a fabricated or mismatched UTXO. [6](#0-5) 

The resulting transaction will be rejected by Bitcoin consensus or fail Taproot verification when the real prevout differs, but the wallet has already accepted the forged `ReceivedOutput` as spendable. This can corrupt balances, trigger invalid withdrawals, or conceal unavailable liquidity until broadcast fails.

### Likelihood Explanation
The attacker needs only a known wallet address and an input path that feeds attacker-controlled bytes to `ReceivedOutput::read`; no private key, validator compromise, or malformed cryptographic encoding is required. Because `read` is a public constructor for the object while proving no chain-backed ownership, this is directly reachable wherever serialized outputs are accepted from an untrusted peer, database, backup, or network service. [7](#0-6) 

### Recommendation
Do not expose `ReceivedOutput::read` as an authority-producing constructor for untrusted data. Separate raw decoding from trusted construction:

- Keep scanning-derived construction restricted to `Scanner::scan_transaction` / `scan_block`, or return an explicitly unauthenticated `SerializedReceivedOutput` type.
- Bind the offset to a specific scanner or base key and reject scripts not registered for that scanner.
- Before accounting for a decoded output, verify the outpoint on Bitcoin and compare the chain-returned `TxOut` byte-for-byte with the supplied output.
- Persist enough authenticated metadata to prove the output originated from trusted chain scanning.

### Proof of Concept
For a wallet with base key `K`, an attacker can construct bytes equivalent to:

```rust
let script = p2tr_script_buf(K).unwrap();
let txout = TxOut {
  value: Amount::from_sat(1_000_000),
  script_pubkey: script,
};
let fake_outpoint = OutPoint::new(nonexistent_txid, 0);

let mut forged = Scalar::ZERO.to_bytes().to_vec();
forged.extend(serialize(&txout));
forged.extend(serialize(&fake_outpoint));

let output = ReceivedOutput::read(&mut forged.as_slice()).unwrap();
```

`output.value()` reports 1,000,000 sats despite the outpoint being fabricated. `SignableTransaction::new` then counts that value as input funds and inserts `fake_outpoint` into the transaction. [4](#0-3)  `multisig` accepts the output because the stored script matches the zero-offset wallet key, and the FROST participants can produce a syntactically signed transaction. [5](#0-4)  Bitcoin still rejects the transaction because the claimed prevout does not exist or does not contain the claimed `TxOut`, proving that the deserialized output was reported spendable when it was not.

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L88-97)
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
}
```

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
