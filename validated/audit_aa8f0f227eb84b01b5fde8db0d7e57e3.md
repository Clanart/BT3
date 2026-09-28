### Title
`Scanner::scan_block` reports immature coinbase outputs as received, with no maturity-bound check - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
The external report's bug class is a validity window that is only bounded on one side: a start check exists, but no end/constraint check prevents the action outside its valid period. The Serai analog is `Scanner::scan_block`, which scans *every* transaction in a block — including the coinbase transaction — and returns `ReceivedOutput`s that are not spendable until they reach coinbase maturity (100 blocks). There is no check in `scan_transaction`/`scan_block` that an output came from a non-coinbase transaction or that it is past its maturity window, so funds are reported as received before they can actually be spent.

### Finding Description
`Scanner::scan` matches outputs purely on `script_pubkey` membership in `self.scripts` ( [1](#0-0) ). `scan_block` iterates `block.txdata` starting at index 0, i.e. it includes the coinbase transaction ( [2](#0-1) ). Coinbase outputs are consensus-immature for 100 blocks and cannot be spent; yet `scan_block` returns them indistinguishably from ordinary `ReceivedOutput`s. Any downstream consumer that treats the returned outputs as spendable inputs — e.g. feeding them into `SignableTransaction::new`, which performs no maturity/coinbase check on `inputs` ( [3](#0-2) ) — will produce a transaction that the network rejects (or worse, the FROST threshold will have signed a spend that can never confirm, consuming a signing attempt and the eventuality tracking around it). The processor's `get_outputs` works around this by manually slicing `block.txdata[1 ..]` ( [4](#0-3) ), confirming the library-level result is wrong by default — the bound exists only in one caller, not in the API that produces the data.

### Impact Explanation
An unprivileged party (any miner — mining is open to anyone and requires no validator role) who mines a block paying to one of the scanner's registered scripts causes `scan_block` to report funds as received that are provably unspendable for 100 blocks. If such an output is consumed as an input, the threshold group produces a valid FROST signature over a transaction that fails consensus (`bad-txns-premature-spend-of-coinbase`), so the signed transaction is dead on arrival and the outputs it aggregates remain locked until a re-attempt is coordinated. This falls under the accepted "funds reported received that are not spendable" impact.

### Likelihood Explanation
On Bitcoin mainnet, miner payouts to arbitrary scripts are routine (pools pay directly to addresses). Any miner — knowingly or not — paying to a monitored P2TR script triggers this. The only mitigating factor is that the first-party caller in `processor/src/networks/bitcoin.rs` skips `txdata[0]`; but `scan_block` is the public API (`pub fn`), is used directly in tests ( [5](#0-4) ), and any other integrator using it on raw blocks hits the bug. Likelihood: Medium.

### Recommendation
Either make `scan_block` skip `block.txdata[0]` (matching the processor's own workaround), or have `scan_transaction`/`scan_block` tag `ReceivedOutput`s originating from coinbase transactions (e.g. a `coinbase: bool` / maturity height field) so callers can enforce the maturity window. At minimum, `SignableTransaction::new` should reject inputs whose `ReceivedOutput` is a coinbase output that has not reached maturity — but since `ReceivedOutput` currently carries no provenance, the clean fix is in `scan_block`.

### Proof of Concept
```rust
// Conceptual PoC against networks/bitcoin/src/wallet/mod.rs
// Mine a block whose coinbase pays to a scanned script_pubkey
rpc.rpc_call::<Vec<String>>(
    "generatetoaddress",
    serde_json::json!([1, Address::from_script(&p2tr_script_buf(key).unwrap(), network)]),
).await.unwrap();

let block = rpc.get_block(&rpc.get_block_hash(height).await.unwrap()).await.unwrap();

// scan_block returns the coinbase output as a normal ReceivedOutput
let outputs = scanner.scan_block(&block);
assert_eq!(outputs.len(), 1);

// That output is consensus-immature for 100 blocks. Yet SignableTransaction::new
// accepts it as an input with no maturity check:
let tx = SignableTransaction::new(
    outputs.clone(),           // immature coinbase output
    &[(payment_script, 10_000)],
    Some(change_script),
    None,
    fee_per_vbyte,
).unwrap();

// The threshold signs successfully, producing a transaction that every node
// rejects with bad-txns-premature-spend-of-coinbase.
let signed = sign(&keys, &tx); // valid FROST signature, invalid transaction
```
The signed transaction's input spends `block.txdata[0]` (a coinbase output at height `h`), which cannot be spent until `h + 100`; `scan_block` provided no indication of this, and no other in-scope API enforces the bound.

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L205-211)
```rust
      if let Some(offset) = self.scripts.get(&output.script_pubkey) {
        res.push(ReceivedOutput {
          offset: *offset,
          output: output.clone(),
          outpoint: OutPoint::new(tx.compute_txid(), vout),
        });
      }
```

**File:** networks/bitcoin/src/wallet/mod.rs (L221-227)
```rust
  pub fn scan_block(&self, block: &Block) -> Vec<ReceivedOutput> {
    let mut res = Vec::new();
    for tx in &block.txdata {
      res.extend(self.scan_transaction(tx));
    }
    res
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

**File:** processor/src/networks/bitcoin.rs (L689-692)
```rust
    let mut outputs = vec![];
    // Skip the coinbase transaction which is burdened by maturity
    for tx in &block.txdata[1 ..] {
      for output in scanner.scan_transaction(tx) {
```

**File:** networks/bitcoin/tests/wallet.rs (L65-66)
```rust
  let mut outputs = scanner.scan_block(&block);
  assert_eq!(outputs, scanner.scan_transaction(&block.txdata[0]));
```
