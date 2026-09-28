### Title
Untrusted `ReceivedOutput` deserialization accepts outputs not bound to the wallet key or claimed offset - (File: `networks/bitcoin/src/wallet/mod.rs`)

### Summary

`ReceivedOutput::read` accepts an attacker-controlled scalar offset, `TxOut`, and `OutPoint` without proving that the output's `script_pubkey` belongs to the wallet key adjusted by that offset. The normal scanner path validates this binding by looking up the output's `script_pubkey` in a map containing only scripts derived from the wallet key and registered offsets. Deserialization bypasses that registry and can therefore report an unrelated or incorrectly offset output as spendable wallet funds.

### Finding Description

`Scanner` maintains `scripts: HashMap<ScriptBuf, Scalar>`, initialized with the wallet's untweaked P2TR script and populated only through `register_offset`. Each registered script is derived as `p2tr_script_buf(self.key + GENERATOR * offset)`. [1](#0-0) [2](#0-1) 

When scanning an actual transaction, `scan_transaction` only constructs a `ReceivedOutput` if the transaction output's exact `script_pubkey` exists in that map. This binds the returned `offset` to the script which was observed on-chain. [3](#0-2) 

`ReceivedOutput` documents `offset` as “the scalar offset to obtain the key usable to spend this output.” However, `ReceivedOutput::read` merely reads the scalar, consensus-decodes an arbitrary `TxOut`, and consensus-decodes an arbitrary `OutPoint`; it never verifies the supplied offset against the supplied `script_pubkey` or a scanner/base key. [4](#0-3) [5](#0-4) 

This is analogous to accepting an otherwise well-formed credential without checking that it was issued for this verifier. A serialized `ReceivedOutput` is accepted as representing wallet ownership even though its critical authorization binding—wallet script ↔ offset—is absent from the object and unchecked by the reader.

### Impact Explanation

An attacker can provide a `ReceivedOutput` claiming an output is spendable by the wallet when it is not. Two concrete mismatches are possible:

1. The `script_pubkey` locks funds to an unrelated public key while claiming a wallet offset.
2. The `script_pubkey` is a legitimate wallet-derived script, but the serialized offset is different from the offset actually required to spend it.

In either case, consumers can expose `value()` as received wallet funds while attempting to spend with the key represented by `ReceivedOutput::offset()` cannot authorize the claimed output. [6](#0-5) 

This can cause incorrect deposit accounting and transactions that reference inputs the wallet cannot actually spend. Where the parsed object is persisted or relayed, a single crafted encoding can poison later balance and transaction-construction logic even though no matching scanner result ever occurred.

### Likelihood Explanation

Any interface, database restore path, synchronization protocol, or peer message which feeds attacker-controlled bytes into `ReceivedOutput::read` reaches the issue. The attacker only needs to produce canonical field and consensus encodings; no private keys, validator privileges, malformed points, or invalid Bitcoin consensus objects are required.

The attack is constrained by deployment behavior: bytes read from a private database populated exclusively through `Scanner::scan_transaction` are already implicitly trusted, while bytes accepted from an untrusted source are directly exploitable. Because the type itself represents the offset as the spending credential, callers have no local way to discover the missing binding unless they repeat scanner validation.

### Recommendation

Do not treat `ReceivedOutput::read` as sufficient validation for wallet ownership.

Add a scanner-bound decode path, such as `Scanner::read_received_output`, which:

1. Reads the claimed offset and `TxOut`.
2. Recomputes or looks up the expected script for `(self.key, offset)`.
3. Rejects the object unless `output.script_pubkey` equals the scanner-registered script.
4. Preserves the requirement that the referenced transaction/outpoint be confirmed through an actual `Scanner::scan_transaction` result before treating the output as received.

Alternatively, serialize the wallet key or a scanner identifier alongside `ReceivedOutput` and verify the derivation on deserialization. Merely documenting callers' obligations leaves a portable object whose claimed spending authority is not self-authenticating.

### Proof of Concept

Conceptually, for a wallet key `K_wallet` and any unrelated even Taproot internal key `K_attacker`:

```rust
let claimed_offset = Scalar::ONE;

let attacker_output = TxOut {
  value: Amount::from_sat(1_000_000),
  script_pubkey: ScriptBuf::new_p2tr_tweaked(
    TweakedPublicKey::dangerous_assume_tweaked(x_only(&K_attacker))
  ),
};

let attacker_outpoint = OutPoint {
  txid: attacker_controlled_txid,
  vout: 0,
};

let mut encoded = vec![];
encoded.extend(claimed_offset.to_bytes());
encoded.extend(serialize(&attacker_output));
encoded.extend(serialize(&attacker_outpoint));

let received = ReceivedOutput::read(&mut encoded.as_slice()).unwrap();
assert_eq!(received.offset(), claimed_offset);
assert_eq!(received.value(), 1_000_000);
assert_eq!(received.outpoint(), &attacker_outpoint);
```

The decode succeeds even though `attacker_output.script_pubkey` was never present in `Scanner::scripts` for `K_wallet`. The claimed spending key implied by the object would be `K_wallet + GENERATOR * claimed_offset`, while the output actually commits to `K_attacker`, so funds represented by the decoded object are not spendable under that claimed wallet credential. [5](#0-4) [2](#0-1)

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

**File:** networks/bitcoin/src/wallet/mod.rs (L99-118)
```rust
impl ReceivedOutput {
  /// The offset for this output.
  pub fn offset(&self) -> Scalar {
    self.offset
  }

  /// The Bitcoin output for this output.
  pub fn output(&self) -> &TxOut {
    &self.output
  }

  /// The outpoint for this output.
  pub fn outpoint(&self) -> &OutPoint {
    &self.outpoint
  }

  /// The value of this output.
  pub fn value(&self) -> u64 {
    self.output.value.to_sat()
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

**File:** networks/bitcoin/src/wallet/mod.rs (L151-165)
```rust
/// A transaction scanner capable of being used with HDKD schemes.
#[derive(Clone, Debug)]
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
```

**File:** networks/bitcoin/src/wallet/mod.rs (L180-195)
```rust
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
