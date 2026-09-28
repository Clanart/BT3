### Title

Forged `ReceivedOutput` deserialization lets nonexistent Bitcoin outputs be treated as wallet funds - (File: `networks/bitcoin/src/wallet/mod.rs`)

### Summary

Severity: Medium

`ReceivedOutput::read` accepts an offset, `TxOut`, and `OutPoint` without establishing that the outpoint exists or was discovered by `Scanner`. A crafted serialized object can use the wallet’s known base P2TR script with a zero offset and an arbitrary nonexistent outpoint, causing the wallet to report and attempt to spend funds that were never received.

### Finding Description

`ReceivedOutput` represents a scanned spendable output by combining the scalar `offset`, Bitcoin `TxOut`, and claimed `OutPoint`. [1](#0-0) 

The trusted construction path in `Scanner::scan_transaction` only creates this object after matching `output.script_pubkey` to a registered script and derives the outpoint from the containing transaction’s actual TXID and output index. [2](#0-1) 

The deserialization path bypasses those guarantees: it reads any canonical scalar, then consensus-decodes an arbitrary `TxOut` and `OutPoint`, and immediately returns the object. [3](#0-2) 

When the object is later used for signing, `SignableTransaction::multisig` only checks that `keys.offset(offset).group_key()` maps to the supplied `prevouts[i].script_pubkey`; it does not verify that the outpoint exists on-chain or belongs to a confirmed transaction. [4](#0-3) 

An attacker can therefore set `offset` to zero and `script_pubkey` to the wallet’s publicly known base P2TR script, while specifying a fabricated TXID/vout and arbitrary value. [5](#0-4) 

### Impact Explanation

A forged serialized `ReceivedOutput` causes downstream code to account for funds which do not exist. Because the fabricated script matches the wallet key when the zero offset is used, `SignableTransaction::multisig` accepts the forged output and constructs signing machines for it. [6](#0-5) 

The resulting transaction commits to the fabricated prevout in its Taproot sighash, but cannot be validly accepted by Bitcoin because the claimed input was never created. [7](#0-6) 

This creates incorrect balance reporting and may stall spending flows if forged inputs are selected before valid outputs.

### Likelihood Explanation

The attacker only needs the ability to submit serialized `ReceivedOutput` bytes and knowledge of the wallet’s public P2TR script. No private key, validator privilege, malformed encoding, or control of a real Bitcoin output is required.

Exploitability depends on the deployment accepting these serialized objects across a trust boundary, such as from a peer, imported wallet state, or an unauthenticated storage/queue entry. If the bytes are always produced locally by `Scanner` and protected from modification, the issue is not reachable.

### Recommendation

Do not deserialize `ReceivedOutput` as an authoritative proof of receipt from untrusted input. Either authenticate serialized scanner outputs at the storage/transport boundary or make the constructor require the expected wallet key and verify:

```rust
p2tr_script_buf(wallet_key + (ProjectivePoint::GENERATOR * offset))
    == Some(output.script_pubkey.clone())
```

Independently confirm that `outpoint` references a confirmed transaction output before exposing the object as spendable. Scanner results should be regenerated or cryptographically authenticated rather than accepting arbitrary serialized combinations of `offset`, `TxOut`, and `OutPoint`.

### Proof of Concept

Conceptually, using the wallet’s already-tweaked `ThresholdKeys<Secp256k1>`:

```rust
use bitcoin::{Amount, OutPoint, TxOut};
use k256::Scalar;

// The wallet's public base P2TR script.
let wallet_script =
    p2tr_script_buf(tweaked_keys.group_key()).expect("tweaked key is even");

// Forge a received output:
// offset = 0, arbitrary value, wallet script, nonexistent outpoint.
let mut forged = Scalar::ZERO.to_bytes().to_vec();
forged.extend(bitcoin::consensus::encode::serialize(&TxOut {
    value: Amount::from_sat(100_000),
    script_pubkey: wallet_script,
}));
forged.extend(bitcoin::consensus::encode::serialize(&OutPoint::default()));

// Accepted without checking that the outpoint exists.
let received = ReceivedOutput::read(&mut forged.as_slice()).unwrap();

// The forged output is accepted as spendable under the wallet key.
let tx = SignableTransaction::new(
    vec![received],
    &[(payment_script, 50_000)],
    None,
    None,
    2,
)
.unwrap();

assert!(tx.multisig(&tweaked_keys).is_some());
```

The call succeeds because the fabricated script matches `offset = 0`, while `OutPoint::default()` need not correspond to any real Bitcoin transaction. [6](#0-5)

### Citations

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

**File:** networks/bitcoin/src/wallet/mod.rs (L158-165)
```rust
impl Scanner {
  /// Construct a Scanner for a key.
  ///
  /// Returns None if this key can't be scanned for.
  pub fn new(key: ProjectivePoint) -> Option<Scanner> {
    let mut scripts = HashMap::new();
    scripts.insert(p2tr_script_buf(key)?, Scalar::ZERO);
    Some(Scanner { key, scripts })
```

**File:** networks/bitcoin/src/wallet/mod.rs (L199-210)
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
