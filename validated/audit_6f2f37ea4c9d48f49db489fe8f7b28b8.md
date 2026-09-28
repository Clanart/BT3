### Title

Forged `ReceivedOutput` enables signing a transaction that drains an arbitrary known vault UTXO - (File: networks/bitcoin/src/wallet/send.rs)

### Summary

`ReceivedOutput::read` constructs a spendable-output capability entirely from attacker-controlled bytes, while `SignableTransaction` trusts its claimed outpoint and previous output, and `SignableTransaction::multisig` only verifies that the supplied previous output's script matches the corresponding offset key. [1](#0-0) [2](#0-1) 

### Finding Description

`ReceivedOutput::read` accepts the `offset`, full `TxOut`, and `OutPoint` from an untrusted reader without authenticating that the object was produced by `Scanner` or persisted by trusted wallet state. [1](#0-0) 

The scalar reader only enforces canonical encoding, so an attacker can encode `Scalar::ZERO`, which `Scanner::new` automatically associates with the base vault script. [3](#0-2) [4](#0-3) 

`SignableTransaction::new` uses the supplied previous output amount for funding calculations, places the supplied outpoint in the unsigned transaction, and stores the attacker-provided `TxOut` as the `Prevouts::All` data later committed to by the sighash. [5](#0-4) [6](#0-5) [7](#0-6) 

The only ownership check performed by `multisig` is that `p2tr_script_buf(keys.offset(offset).group_key())` equals the supplied previous output's `script_pubkey`; there is no check that the `ReceivedOutput` came from the scanner, trusted storage, or an authorized withdrawal flow. [2](#0-1) 

Because `payments` are also supplied by the caller, the resulting FROST protocol signs a transaction spending the referenced UTXO to an attacker-selected destination and installs the signatures as Taproot witnesses. [8](#0-7) [9](#0-8) 

### Impact Explanation

If an unprivileged party can cause these serialized bytes and payment parameters to reach this signing path, it can obtain a valid transaction spending any known base-key multisig UTXO to itself. [10](#0-9) 

This is the Serai analogue of accepting an attacker-supplied `from` address: the code treats a deserializable description of the victim output as authorization to spend it instead of binding the spend to an internally authenticated output and authorized payment request. [11](#0-10) [12](#0-11) 

A forged `ReceivedOutput` can alternatively claim a nonexistent output and cause unavailable funds to be represented as spendable inputs, although the UTXO-theft variant is more severe. [1](#0-0) [13](#0-12) 

### Likelihood Explanation

The attacker needs only public blockchain data—the target outpoint and its exact `TxOut`—plus a path that feeds untrusted bytes to `ReceivedOutput::read` and then constructs or requests a `SignableTransaction`. [14](#0-13) 

For a base vault output, no private offset, key share, signature approval, or control of a validator is required because `Scalar::ZERO` serializes normally and `Scanner::new` registers the base script with offset zero. [4](#0-3) [3](#0-2) 

### Recommendation

Do not expose `ReceivedOutput::read` as a public constructor for untrusted data; restore outputs only through authenticated wallet persistence or a keyed integrity mechanism that proves the object was produced by `Scanner`. [14](#0-13) 

`SignableTransaction` should consume opaque scanner-generated identifiers resolved inside trusted wallet state, rather than caller-provided `offset`, `TxOut`, and `OutPoint` triples. [8](#0-7) 

Before creating a signing machine, resolve each outpoint against confirmed chain state and require the spend request to be bound to an authorized operation containing the destination, amount, fee, and input identity. [2](#0-1) 

### Proof of Concept

The following sequence demonstrates that no private data is needed to construct a spend request for a real base-key vault UTXO:

```rust
use bitcoin::{consensus::Encodable, OutPoint, TxOut};
use frost::curve::Secp256k1;
use k256::Scalar;
use bitcoin_serai::wallet::{ReceivedOutput, SignableTransaction};

// Public data obtained from Bitcoin.
let victim_outpoint: OutPoint = /* txid and vout of the vault UTXO */;
let victim_txout: TxOut = /* exact amount and script_pubkey at that outpoint */;

// Serialize a forged ReceivedOutput:
//   Scalar::ZERO || TxOut || OutPoint
let mut encoded = Scalar::ZERO.to_bytes().to_vec();
victim_txout.consensus_encode(&mut encoded).unwrap();
victim_outpoint.consensus_encode(&mut encoded).unwrap();

let forged = ReceivedOutput::read(&mut encoded.as_slice()).unwrap();

let attack = SignableTransaction::new(
  vec![forged],
  &[(attacker_script_pubkey, victim_txout.value.to_sat() - adequate_fee)],
  None,
  None,
  fee_per_vbyte,
).unwrap();

// This succeeds when `vault_keys` correspond to the victim output's script.
let machine = attack.multisig(&vault_keys).unwrap();
```

`Scalar::ZERO` maps the base key to the base P2TR script, the supplied `TxOut` and `OutPoint` become the transaction's input and committed prevout, and `multisig` accepts the object solely because the script matches the derived key. [15](#0-14) [5](#0-4) [2](#0-1) 

Completing the resulting `TransactionMachine` signs each input's Taproot key-spend sighash and returns a transaction with valid Schnorr witnesses for the referenced victim output. [16](#0-15) [9](#0-8)

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

**File:** networks/bitcoin/src/wallet/mod.rs (L138-140)
```rust
    w.write_all(&self.offset.to_bytes())?;
    w.write_all(&serialize(&self.output))?;
    w.write_all(&serialize(&self.outpoint))
```

**File:** networks/bitcoin/src/wallet/mod.rs (L162-165)
```rust
  pub fn new(key: ProjectivePoint) -> Option<Scanner> {
    let mut scripts = HashMap::new();
    scripts.insert(p2tr_script_buf(key)?, Scalar::ZERO);
    Some(Scanner { key, scripts })
```

**File:** networks/bitcoin/src/wallet/send.rs (L150-156)
```rust
  pub fn new(
    mut inputs: Vec<ReceivedOutput>,
    payments: &[(ScriptBuf, u64)],
    change: Option<ScriptBuf>,
    data: Option<Vec<u8>>,
    fee_per_vbyte: u64,
  ) -> Result<SignableTransaction, TransactionError> {
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

**File:** networks/bitcoin/src/wallet/send.rs (L245-253)
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

**File:** networks/bitcoin/src/wallet/send.rs (L373-397)
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
        )?;
        shares.push(share);
        Ok(sig)
      })
      .collect::<Result<_, _>>()?;

    Ok((TransactionSignatureMachine { tx: self.tx.tx, sigs }, shares))
```

**File:** networks/bitcoin/src/wallet/send.rs (L413-425)
```rust
  fn complete(
    mut self,
    mut shares: HashMap<Participant, Self::SignatureShare>,
  ) -> Result<Transaction, FrostError> {
    for (input, schnorr) in self.tx.input.iter_mut().zip(self.sigs.drain(..)) {
      let sig = schnorr.complete(
        shares.iter_mut().map(|(l, shares)| (*l, shares.remove(0))).collect::<HashMap<_, _>>(),
      )?;

      let mut witness = Witness::new();
      witness.push(sig);
      input.witness = witness;
    }
```

**File:** crypto/ciphersuite/src/lib.rs (L74-82)
```rust
  fn read_F<R: Read>(reader: &mut R) -> io::Result<Self::F> {
    let mut encoding = <Self::F as PrimeField>::Repr::default();
    reader.read_exact(encoding.as_mut())?;

    // ff mandates this is canonical
    let res = Option::<Self::F>::from(Self::F::from_repr(encoding))
      .ok_or_else(|| io::Error::other("non-canonical scalar"));
    encoding.as_mut().zeroize();
    res
```
