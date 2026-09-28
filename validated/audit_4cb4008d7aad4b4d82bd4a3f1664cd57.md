### Title
ReceivedOutput deserializes `offset` and `script_pubkey` as independent fields, letting a forged pair misattribute ownership when `key()` trusts both - ([File: networks/bitcoin/src/wallet/mod.rs](networks/bitcoin/src/wallet/mod.rs))

### Summary
CVE-2016-4553 is an identity-ambiguity flaw: Squid routed requests by the absolute-URI but keyed its cache on the `Host` header, so two conflicting identifiers for one resource let an attacker poison responses. The Serai analog is `ReceivedOutput`: it carries *two* independent claims about who owns a UTXO — the `output.script_pubkey` (the authoritative on-chain identifier) and the `offset` scalar (metadata saying which registered offset/key produced that script). The scanner guarantees they agree when it creates them, but `ReceivedOutput::read` accepts arbitrary untrusted bytes with no consistency check, and downstream `Output::key()` in `processor/src/networks/bitcoin.rs` resolves the owning key by subtracting `G * offset` from the point embedded in `script_pubkey` — trusting both fields simultaneously, exactly like trusting Host and URI together.

### Finding Description
`Scanner::scan_transaction` builds `ReceivedOutput` by looking up `self.scripts[&output.script_pubkey]`, so the stored `offset` is always the one that actually produces the on-chain script_pubkey under `key + offset * G` (`networks/bitcoin/src/wallet/mod.rs:199-214`, registration at `:180-196`). However `ReceivedOutput::read` deserializes `offset` via `Secp256k1::read_F`, then `output` (a `TxOut`) and `outpoint` via consensus decode, with no check that `p2tr_script_buf(scanner_key + G*offset) == output.script_pubkey` (`networks/bitcoin/src/wallet/mod.rs:122-134`).

`Output::key()` then derives the owning multisig key as `x_only(script_pubkey) − G * output.offset()` (`processor/src/networks/bitcoin.rs:112-122`). With a forged `ReceivedOutput` pairing a real Serai script_pubkey `p2tr(K + o*G)` with a mismatched `offset = o'`, `key()` returns `K + (o − o')*G` — an arbitrary, attacker-chosen group key. `get_outputs` additionally classifies the output kind by `kinds[offset_repr_ref]` (`processor/src/networks/bitcoin.rs:686-699`), so the *declared* offset also selects the `OutputType` (External / Branch / Change / Forwarded) independently of which script actually appeared on chain. Conversely, `SignableTransaction::multisig` does check `p2tr_script_buf(offset.group_key()) == prevouts[i].script_pubkey` (`networks/bitcoin/src/wallet/send.rs:273-282`), so the two fields are silently authoritative in different subsystems — the same split-trust shape as the Squid bug.

### Impact Explanation
An unprivileged party able to feed crafted bytes to `ReceivedOutput::read`/`Output::read` (a reachable untrusted-bytes sink per scope) can cause an output to be attributed to a validator-set key that does not control the on-chain script_pubkey, or to be classified under an `OutputType` (e.g., `External` vs `Change`/`Forwarded`) inconsistent with the registered offset for that script. Concretely this yields funds reported received under a key that cannot spend them (the chosen `o'` maps `key()` to a different set, while the real on-chain output is spendable only by `K + o*G`), and `Output::id`/`balance` flows then index the phantom output in the processor's DB. This matches the cache-poisoning shape: the authoritative identifier (script_pubkey) is displaced by the secondary identifier (offset) during keying/classification.

### Likelihood Explanation
Reachability requires untrusted bytes to enter `ReceivedOutput::read`/`Output::read`, i.e., any coordinator→processor or cross-component channel carrying serialized outputs — permitted by the scope rules, though the precise production caller passing fully attacker-controlled `Output` bytes was not isolated in this pass (reads are also used for trusted DB state, which limits practical exposure). Exploitation requires only choosing an arbitrary `offset` scalar alongside a valid Serai P2TR `TxOut`; no curve tricks or collusion are needed. Impact is integrity-of-attribution rather than direct key recovery, so Medium is appropriate.

### Recommendation
Either (a) make `ReceivedOutput` verify consistency at construction/`read` by storing the scanner key and re-deriving `p2tr_script_buf(key + G*offset) == output.script_pubkey`, or (b) drop the redundant field: derive `offset` solely from the scanner's `scripts` map keyed by `script_pubkey` (single source of truth), and have `Output::key()`/`kinds` lookup use only the script_pubkey-to-offset map rather than the deserialized scalar. At minimum, `get_outputs` should assert `p2tr_script_buf(key + G*output.offset()) == output.output().script_pubkey` before trusting `offset` for `key()`/`kind` classification.

### Proof of Concept
```rust
// Attacker-controlled bytes fed to ReceivedOutput::read / Output::read.
// Take a genuine Serai output paying to p2tr(K + o*G), but rewrite `offset` to o'.
let real_script = p2tr_script_buf(key + (ProjectivePoint::GENERATOR * o)).unwrap();
let forged = ReceivedOutput {
    offset: o_prime,                    // o' != o, attacker-chosen
    output: TxOut { value, script_pubkey: real_script },
    outpoint,
};
// Output::key() (processor/src/networks/bitcoin.rs:112-122) computes:
//   x_only(real_script) - G*o' == K + (o - o')*G
// => the output is attributed to a multisig key that never received funds,
//    while the real on-chain output remains spendable only by K + o*G.
// kinds[o'.to_repr()] additionally mislabels the OutputType independently of
// the script actually on chain.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L122-134)
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
```

**File:** networks/bitcoin/src/wallet/mod.rs (L199-214)
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
    }
    res
  }
```

**File:** processor/src/networks/bitcoin.rs (L112-122)
```rust
  fn key(&self) -> ProjectivePoint {
    let script = &self.output.output().script_pubkey;
    assert!(script.is_p2tr());
    let Instruction::PushBytes(key) = script.instructions_minimal().last().unwrap().unwrap() else {
      panic!("last item in v1 Taproot script wasn't bytes")
    };
    let key = XOnlyPublicKey::from_slice(key.as_ref())
      .expect("last item in v1 Taproot script wasn't x-only public key");
    Secp256k1::read_G(&mut key.public_key(Parity::Even).serialize().as_slice()).unwrap() -
      (ProjectivePoint::GENERATOR * self.output.offset())
  }
```

**File:** processor/src/networks/bitcoin.rs (L686-699)
```rust
  async fn get_outputs(&self, block: &Self::Block, key: ProjectivePoint) -> Vec<Output> {
    let (scanner, _, kinds) = scanner(key);

    let mut outputs = vec![];
    // Skip the coinbase transaction which is burdened by maturity
    for tx in &block.txdata[1 ..] {
      for output in scanner.scan_transaction(tx) {
        let offset_repr = output.offset().to_repr();
        let offset_repr_ref: &[u8] = offset_repr.as_ref();
        let kind = kinds[offset_repr_ref];

        let output = Output { kind, presumed_origin: None, output, data: vec![] };
        assert_eq!(output.tx_id(), tx.id());
        outputs.push(output);
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
