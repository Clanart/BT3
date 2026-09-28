### Title
Forged `ReceivedOutput` deserialization enables unspendable deposits to be signed - ([File: networks/bitcoin/src/wallet/mod.rs])

### Summary
`ReceivedOutput::read` accepts an arbitrary scalar offset, `TxOut`, and `OutPoint` from untrusted bytes without proving that the referenced Bitcoin output exists or that the scalar is associated with a registered scanner offset. A forged value can therefore pass `SignableTransaction::multisig` when its `script_pubkey` matches the wallet key, causing Serai to treat and attempt to sign a nonexistent deposit as spendable funds. [1](#0-0) [2](#0-1) 

### Finding Description
`ReceivedOutput::read` performs only syntactic deserialization: it reads a canonical secp256k1 scalar, then consensus-decodes the supplied `TxOut` and `OutPoint`. [1](#0-0)  Unlike `Scanner::scan_transaction`, which constructs an object only after matching an output in a provided transaction to a registered script and binding the real transaction ID and vout, the deserializer has no provenance check. [3](#0-2) 

The downstream transaction builder trusts the deserialized output value and outpoint as inputs. [4](#0-3)  `SignableTransaction::multisig` then checks only that applying the stored offset to the threshold key produces the supplied `script_pubkey`; it does not verify that the outpoint exists or was discovered by the scanner. [2](#0-1) 

For the wallet’s base address, the required offset is `Scalar::ZERO`, so a party who knows the public wallet address can serialize a zero offset, a fake high-value `TxOut` paying that address, and an arbitrary nonexistent outpoint. [5](#0-4) 

### Impact Explanation
An attacker can cause the system to report or consume a deposited balance that is not backed by any UTXO. If accounting, persistence recovery, or inter-process message handling accepts `ReceivedOutput::read` input from an untrusted source, the forged object can be credited internally and supplied to `SignableTransaction::new`. The resulting threshold-signing machine will produce signatures for a transaction that Bitcoin necessarily rejects because its referenced prevout does not exist. [6](#0-5) 

This satisfies the security impact of funds being reported received even though they are not spendable.

### Likelihood Explanation
The attacker needs only the wallet’s public P2TR script/address and the ability to feed serialized bytes to `ReceivedOutput::read`. No private key material, validator compromise, malformed curve point, or invalid scalar is required. A zero offset targets the base wallet script; alternatively, any previously registered derived script whose offset became public can be reused. The required fields are all chosen by the attacker and the signing path’s only key-consistency check passes for a correctly computed wallet script. [1](#0-0) [7](#0-6) 

### Recommendation
Do not treat deserialized `ReceivedOutput` values as authenticated scanner results. Store the scanner’s base key or registration identity in the object, validate that `key + offset*G` canonically derives the output script, and verify the claimed outpoint against confirmed blockchain data before accounting for or spending it. If serialized outputs are intended only for trusted local persistence, authenticate them with a MAC or equivalent trusted-channel boundary and expose a separate untrusted-candidate type that must be re-scanned and confirmed on-chain.

### Proof of Concept
Conceptually, construct bytes containing:

1. A canonical zero scalar encoding for the `offset`.
2. A consensus-encoded `TxOut` with an arbitrary large value and `script_pubkey` equal to the wallet’s public P2TR script.
3. A consensus-encoded `OutPoint` naming a random/nonexistent transaction ID and vout.

Feeding those bytes to `ReceivedOutput::read` succeeds because every field is merely decoded. [1](#0-0)  Passing the result to `SignableTransaction::new` causes its claimed value and outpoint to be used as an input. [4](#0-3)  Calling `multisig` with the wallet’s `ThresholdKeys` succeeds because offset zero preserves the even base key and reproduces the supplied P2TR script. [2](#0-1)  The transaction-signing path then creates BIP-340 signatures committing to the nonexistent prevout through `Prevouts::All`. [6](#0-5)

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

**File:** networks/bitcoin/src/wallet/mod.rs (L162-165)
```rust
  pub fn new(key: ProjectivePoint) -> Option<Scanner> {
    let mut scripts = HashMap::new();
    scripts.insert(p2tr_script_buf(key)?, Scalar::ZERO);
    Some(Scanner { key, scripts })
```

**File:** networks/bitcoin/src/wallet/mod.rs (L199-211)
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
      }
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
