### Title
Forged `ReceivedOutput` records allow public input to claim unspendable wallet funds - (`networks/bitcoin/src/wallet/mod.rs`)

### Summary
Medium severity. `ReceivedOutput::read` deserializes an offset, `TxOut`, and `OutPoint` without authenticating that the output was discovered by `Scanner`, that the offset corresponds to the output’s `script_pubkey`, or that the claimed outpoint exists. [1](#0-0) 

### Finding Description
`ReceivedOutput` is documented as “a spendable output,” but its public deserializer accepts three self-asserted fields: a scalar offset, an arbitrary Bitcoin output, and an arbitrary outpoint. [2](#0-1) 

No scanner context or wallet key is supplied to `ReceivedOutput::read`, so it cannot and does not verify that:

- `offset` derives the output’s `script_pubkey`;
- the `OutPoint` exists on-chain;
- the output was produced by a real transaction or block scan.

The claimed amount is exposed directly through `ReceivedOutput::value`. [3](#0-2) 

Downstream, `SignableTransaction::new` trusts `input.output.value` as available input balance and `input.outpoint` as the transaction input. [4](#0-3)  It then stores the claimed outputs as the transaction prevouts. [5](#0-4) 

The offset relationship is only checked when `SignableTransaction::multisig` creates signing machines. [6](#0-5)  A forged record with a mismatched script is therefore accepted into wallet inventory but cannot be signed. A forged record with the wallet’s script but a nonexistent outpoint can pass this check locally while still being impossible to spend on-chain.

### Impact Explanation
An unprivileged party can submit serialized bytes that `ReceivedOutput::read` converts into an apparently spendable wallet output with an arbitrary value. A component accepting these bytes can report funds as received even though they cannot be spent, corrupting wallet accounting or causing deposits to be credited without an actual spendable UTXO.

### Likelihood Explanation
The vulnerable entry point is a public deserializer explicitly reachable with attacker-controlled bytes. Exploitation requires a downstream component to trust `ReceivedOutput::read` as representing scanner-observed wallet inventory rather than treating it as a self-asserted claim. Under that deployment assumption, no validator key, leaked secret, malformed curve point, or privileged RPC is required.

### Recommendation
Do not expose `ReceivedOutput::read` as an unauthenticated public-input constructor. Either:

- restrict deserialization to authenticated, locally generated scanner state and document that requirement;
- store and verify scanner context sufficient to check that `offset` derives `output.script_pubkey`;
- add a constructor that takes the wallet key and rejects inconsistent offset/output pairs; and
- separately persist transaction inclusion evidence or revalidate the outpoint against Bitcoin before treating the object as spendable.

### Proof of Concept
The following construction demonstrates that arbitrary bytes become an apparently spendable `ReceivedOutput` without any scanner evidence:

```rust
use bitcoin::{
  consensus::serialize,
  Amount, OutPoint, ScriptBuf, TxOut,
};
use k256::Scalar;
use bitcoin_serai::wallet::{ReceivedOutput, SignableTransaction};

let mut bytes = Scalar::ONE.to_bytes().to_vec();

// Claim a large output paying to a script unrelated to the supplied offset.
bytes.extend(serialize(&TxOut {
  value: Amount::from_sat(100_000_000),
  script_pubkey: ScriptBuf::new(),
}));

// Claim an arbitrary outpoint.
bytes.extend(serialize(&OutPoint::default()));

let forged = ReceivedOutput::read(&mut bytes.as_slice()).unwrap();
assert_eq!(forged.value(), 100_000_000);

// For any wallet `keys`, this inventory entry cannot produce a signer because
// ScriptBuf::new() does not match the key derived from Scalar::ONE.
let signable =
  SignableTransaction::new(vec![forged], &[], Some(change_script), None, 1).unwrap();
assert!(signable.multisig(&keys).is_none());
```

Changing `script_pubkey` to the wallet’s registered Taproot script and keeping a fake `OutPoint` instead allows the local offset check to succeed while the referenced UTXO remains nonexistent.

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

**File:** networks/bitcoin/src/wallet/mod.rs (L115-118)
```rust
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
