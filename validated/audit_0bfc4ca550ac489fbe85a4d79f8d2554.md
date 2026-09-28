### Title
Untrusted `ReceivedOutput` offsets can bind attacker-controlled Taproot script-path outputs to a multisig key - ([File: networks/bitcoin/src/wallet/mod.rs](networks/bitcoin/src/wallet/mod.rs))

### Summary
`ReceivedOutput::read` accepts an arbitrary scalar offset and arbitrary `TxOut` without proving that the offset came from the trusted scanner’s registered-offset set. [1](#0-0)  An attacker can choose a Taproot script-path tweak as the offset, causing the deserialized output’s script to be accepted as belonging to the multisig while retaining an independent script-path spending authority. [2](#0-1) 

### Finding Description
`ReceivedOutput` stores `(offset, output, outpoint)`, and its deserializer directly accepts each field from untrusted bytes without associating the offset with a previously generated scanner registration. [3](#0-2) [1](#0-0) 

`SignableTransaction::new` preserves those attacker-controlled offsets and prevouts as the inputs to be signed. [4](#0-3) [5](#0-4) 

The only ownership check occurs in `SignableTransaction::multisig`, which verifies that `p2tr_script_buf(group_key + offset * G)` equals the supplied output’s `script_pubkey`. [6](#0-5) 

This check proves only that the output key is linearly related to the multisig key; it does not prove that the key was generated through one of Serai’s intended offsets or that no Taproot script path exists. [7](#0-6) 

The code itself warns that arbitrary offsets can introduce a script path spendable without the threshold signature. [2](#0-1) 

### Impact Explanation
An attacker can construct a Taproot output key `Q = K + tG`, where `K` is a Serai multisig output key and `t` is a TapTweak commitment to an attacker-controlled script tree. [8](#0-7) 

If serialized bytes claim `offset = t`, the output is treated as belonging to base key `K`, and `multisig` accepts it because `K + tG` produces exactly `Q`’s script. [6](#0-5) 

The result is a deposit or stored output attributed to the multisig that is not exclusively controlled by the multisig: the attacker can spend it through the committed script path while Serai also treats it as a normal offset output. [2](#0-1) 

When such a forged `ReceivedOutput` is included in a spend, the transaction signer calculates Taproot signatures over all supplied prevouts and can produce a valid key-path signature for the malicious input. [9](#0-8) 

### Likelihood Explanation
The attack requires the attacker to reach a path that accepts serialized `ReceivedOutput` values or otherwise chooses their offset and output metadata. [1](#0-0) 

No discrete-logarithm break or secret access is required because the malicious tweak is calculated from public Taproot data and only needs to satisfy the existing `script_pubkey` equality check. [10](#0-9) 

The severity is bounded by whether downstream code treats these deserialized objects as authenticated scanner results; if it does, attacker-controlled deposits can be credited as Serai-controlled funds. [1](#0-0) 

### Recommendation
Do not allow `ReceivedOutput::read` to mint trusted wallet outputs from arbitrary offsets. [1](#0-0) 

Deserialize an untrusted representation separately, then re-derive or validate the offset against the scanner’s authoritative `script_pubkey -> offset` registry before permitting it to be scheduled or signed. [11](#0-10) 

Alternatively, include an authenticated scanner provenance tag or a whitelist of permitted offsets in `ReceivedOutput`, and reject any `offset` whose corresponding script was not generated through `Scanner::register_offset` with a securely generated offset. [12](#0-11) 

`SignableTransaction::multisig` should additionally reject inputs whose effective output key is a Taproot tweak with a known script path, or require proof that the registered offset is one of the protocol-defined script-path-free offsets. [6](#0-5) 

### Proof of Concept
Let `K` be the multisig’s public output key and choose an attacker-controlled Taproot internal key `P = K`, script Merkle root `r`, and scalar `t = TapTweakHash(xonly(P) || r)`. [8](#0-7) 

Compute the Taproot output point `Q = P + tG`, retrying with another `r` until `p2tr_script_buf(Q)` returns `Some`, and create a P2TR `TxOut` paying `Q`. [7](#0-6) 

Serialize bytes as `t.to_bytes() || consensus_encode(TxOut(Q)) || consensus_encode(attacker_outpoint)` and feed them to `ReceivedOutput::read`. [13](#0-12) 

The resulting `ReceivedOutput` claims `offset = t` for script `Q`, so `keys.offset(t).group_key() == K + tG == Q` and `SignableTransaction::multisig` accepts it. [6](#0-5) 

Because `Q` was generated as `P + TapTweakHash(P || r)G`, the attacker retains the script-path spend defined by `r`, even though Serai classifies the output as an offset output of key `K`. [2](#0-1) 

A minimal exploit trace is therefore `attacker bytes -> ReceivedOutput::read -> SignableTransaction::new -> multisig -> taproot_key_spend_signature_hash`, ending in a signature share for an input whose authority is not solely the threshold key. [9](#0-8)

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L55-64)
```rust
    let tweak_hash = TapTweakHash::hash(&keys.group_key().to_bytes().as_slice()[1 ..]);
    /*
      https://github.com/bitcoin/bips/blob/master/bip-0340.mediawiki#cite_ref-13-0 states how the
      bias is negligible. This reduction shouldn't ever occur, yet if it did, the script path
      would be unusable due to a check the script path hash is less than the order. That doesn't
      impact us as we don't want the script path to be usable.
    */
    keys.offset(<Secp256k1 as Ciphersuite>::F::reduce(U256::from_be_bytes(
      *tweak_hash.to_raw_hash().as_ref(),
    )))
```

**File:** networks/bitcoin/src/wallet/mod.rs (L80-85)
```rust
pub fn p2tr_script_buf(key: ProjectivePoint) -> Option<ScriptBuf> {
  if key.to_encoded_point(true).tag() != Tag::CompressedEvenY {
    return None;
  }

  Some(ScriptBuf::new_p2tr_tweaked(TweakedPublicKey::dangerous_assume_tweaked(x_only(&key))))
```

**File:** networks/bitcoin/src/wallet/mod.rs (L90-96)
```rust
pub struct ReceivedOutput {
  // The scalar offset to obtain the key usable to spend this output.
  offset: Scalar,
  // The output to spend.
  output: TxOut,
  // The TX ID and vout of the output to spend.
  outpoint: OutPoint,
```

**File:** networks/bitcoin/src/wallet/mod.rs (L122-140)
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
  }

  /// Write a ReceivedOutput to a generic satisfying Write.
  pub fn write<W: Write>(&self, w: &mut W) -> io::Result<()> {
    w.write_all(&self.offset.to_bytes())?;
    w.write_all(&serialize(&self.output))?;
    w.write_all(&serialize(&self.outpoint))
```

**File:** networks/bitcoin/src/wallet/mod.rs (L153-165)
```rust
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

**File:** networks/bitcoin/src/wallet/mod.rs (L168-190)
```rust
  /// Register an offset to scan for.
  ///
  /// Due to Bitcoin's requirement that points are even, not every offset may be used.
  /// If an offset isn't usable, it will be incremented until it is. If this offset is already
  /// present, None is returned. Else, Some(offset) will be, with the used offset.
  ///
  /// This means offsets are surjective, not bijective, and the order offsets are registered in
  /// may determine the validity of future offsets.
  ///
  /// The offsets registered must be securely generated. Arbitrary offsets may introduce a script
  /// path into the output, allowing the output to be spent by satisfaction of an arbitrary script
  /// (not by the signature of the key).
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
```

**File:** networks/bitcoin/src/wallet/send.rs (L175-176)
```rust
    let input_sat = inputs.iter().map(|input| input.output.value.to_sat()).sum::<u64>();
    let offsets = inputs.iter().map(|input| input.offset).collect();
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
