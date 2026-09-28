### Title
`Scanner::scan_block` reports coinbase outputs as received funds that are not spendable — griefing via immature/vanishing coinbase outputs - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
The M-03 report describes a griefing vector where an attacker, at cost to themselves, manipulates initial state so a victim's deposit is absorbed into a "weird state" — tokens locked/accounted but not usable. The analog in Serai's in-scope code is `Scanner::scan_block`: it scans `block.txdata` including the coinbase transaction, so any miner can send a coinbase output to a scanned key and have it reported as a `ReceivedOutput` — funds that are not spendable for 100 blocks and that disappear entirely if the block is reorged before maturity.

### Finding Description
`scan_transaction` matches any output whose `script_pubkey` is a registered script and emits a `ReceivedOutput` carrying offset, `TxOut`, and outpoint — with no marker distinguishing where in the block the tx appeared. `scan_block` then iterates `for tx in &block.txdata`, i.e., starting at index 0, the coinbase.

<cite repo="Kirstentat/serai--012" path="networks/bitcoin/src/wallet/mod.rs" start="216" end="end" />

The doc comment itself concedes the defect: "This will also scan the coinbase transaction which is bound by maturity. If received outputs must be immediately spendable, a post-processing pass is needed." However, `ReceivedOutput` exposes only `offset()`, `output()`, `outpoint()`, and `value()` — a downstream consumer that holds a bare `ReceivedOutput` (or its serialization via `write`/`read`, which also drops positional context) cannot tell it is a coinbase output without re-fetching and re-scanning the containing block. [1](#0-0) 

The coinbase path is reachable from an unprivileged party's public input: a miner includes a coinbase paying `p2tr_script_buf(key)` (or any registered offset script) — public data — and `scan_block` surfaces it identically to a normal payment. Note the processor works around this in `get_outputs` by slicing `block.txdata[1 ..]`, which is evidence the raw `scan_block` behavior is wrong, not a safe default. [2](#0-1) 

### Impact Explanation
- **Immature funds treated as received**: A `ReceivedOutput` from a coinbase cannot be spent for 100 blocks; feeding it into `SignableTransaction` produces a transaction Bitcoin consensus will reject, or — worse for accounting — the output is credited as available balance.
- **Vanishing funds**: Coinbase outputs are destroyed by any reorg before maturity. An output reported via `scan_block` and persisted (it serializes cleanly through `ReceivedOutput::write`) may correspond to a UTXO that never exists — "funds reported received that are not spendable," the exact validated impact class.
- **Griefing shape matches M-03**: the attacker (miner) bears the cost (coinbase subsidy directed to the victim's script), the victim's wallet/scanner state is left "weird" — holding unspendable or nonexistent outputs — and there is no profitability requirement.

### Likelihood Explanation
Low-to-moderate. It requires the attacker to be a miner (or to coordinate with one) and sacrifice block subsidy directed at the target script. But every miner is an unprivileged party under the threat model, the cost is bounded, and unlike an ERC-4626 vault there is no "initial deposit" mitigation: `Scanner::new`/`register_offset` accept any script and `scan_block` has no coinbase filter.

### Recommendation
Skip `block.txdata[0]` inside `scan_block` (the function already documents coinbase maturity as the reason a post-pass is required — enforce it instead). If raw coinbase scanning must remain available, add an explicit `scan_block_including_coinbase` or mark `ReceivedOutput` with a maturity flag so callers can enforce spendability.

### Proof of Concept
```rust
// regtest-style sketch against bitcoin_serai::wallet
let key = ProjectivePoint::random(&mut OsRng);
let key = if is_even(key) { key } else { key + ProjectivePoint::GENERATOR };
let scanner = Scanner::new(key).unwrap();

// A miner crafts a block whose coinbase pays our P2TR script.
let coinbase = Transaction {
    version: Version::TWO,
    lock_time: LockTime::ZERO,
    input: vec![/* coinbase input (prevout null) */],
    output: vec![TxOut {
        value: Amount::from_sat(50 * 100_000_000),
        script_pubkey: p2tr_script_buf(key).unwrap(),
    }],
};
let block = Block { header: /* valid header */, txdata: vec![coinbase] };

let outputs = scanner.scan_block(&block);
// BUG: the coinbase output is reported identically to a normal payment,
// indistinguishable via ReceivedOutput's public API.
assert_eq!(outputs.len(), 1);
assert_eq!(outputs[0].outpoint().vout, 0);
// outputs[0] is unspendable for 100 blocks and evaporates on any reorg;
// serializing it (ReceivedOutput::write) preserves no coinbase marker.
```

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L88-113)
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
```

**File:** processor/src/networks/bitcoin.rs (L686-692)
```rust
  async fn get_outputs(&self, block: &Self::Block, key: ProjectivePoint) -> Vec<Output> {
    let (scanner, _, kinds) = scanner(key);

    let mut outputs = vec![];
    // Skip the coinbase transaction which is burdened by maturity
    for tx in &block.txdata[1 ..] {
      for output in scanner.scan_transaction(tx) {
```
