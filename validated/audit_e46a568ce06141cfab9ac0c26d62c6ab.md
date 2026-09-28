### Title
A single transaction matching Serai's Bitcoin scanner causes its presumed origin and attached instruction data to be applied to every previously matched output in the same block — ([File: processor/src/networks/bitcoin.rs])

### Summary
Analogous to the Drippie finding (a zero `interval` permits repeated execution within one block, letting a later same-block call clobber/frontrun an earlier one), `Bitcoin::get_outputs` has a per-block ordering dependence: the `outputs` vector and the `if outputs.is_empty() { continue }` gate are scoped to the whole block, not to each transaction. Any transaction appearing *after* a Serai-matching transaction in the same block re-runs the attribution pass and overwrites `presumed_origin`/`data` for all outputs accumulated so far — including outputs from earlier transactions.

### Finding Description
In `get_outputs`, `outputs` is declared once before iterating `block.txdata[1 ..]`:

```rust
let mut outputs = vec![];
// Skip the coinbase transaction which is burdened by maturity
for tx in &block.txdata[1 ..] {
  for output in scanner.scan_transaction(tx) { ... outputs.push(output); }
  if outputs.is_empty() { continue; }
  let presumed_origin = { ... tx.input[0] ... };
  let data = Self::extract_serai_data(tx);
  for output in &mut outputs {
    if output.kind == OutputType::External { output.data.clone_from(&data); }
    output.presumed_origin.clone_from(&presumed_origin);
  }
}
``` [1](#0-0) 

Two defects follow:

1. `outputs.is_empty()` gates on the cumulative block vector, not per-tx matches. Once any earlier tx in the block matched a watched script, every subsequent tx — whether it pays Serai or not — executes the attribution block.
2. The inner `for output in &mut outputs` stamps `data`/`presumed_origin` derived from the *current* tx onto *all* accumulated outputs, including those created by earlier transactions.

`data` is extracted via `extract_serai_data` (the OP_RETURN / InInstruction payload the scanner uses to interpret an `External` deposit), and `presumed_origin` is taken from `tx.input[0]`'s spent output — neither is tied to the tx that actually produced the output.

### Impact Explanation
Any unprivileged party can craft and broadcast a Bitcoin transaction that pays to the multisig's `External` (tweak=0), `Branch`, `Change`, or `Forwarded` P2TR script — all deterministic for the group key — and, by having it ordered later in the same block, overwrite the `data` (InInstruction) and `presumed_origin` of a legitimate deposit earlier in the block. This is precisely the "multiple executions in one block → transaction-order-dependence" bug class: the later call clobbers the earlier one's effect.

Concretely, the victim deposit's `External` output can be emitted with attacker-chosen or empty `data`, so the processor interprets the incoming funds under the wrong/missing instruction — funds reported received that are not spendable/creditable as intended — while the attacker's own dust output (worth less than `N::DUST` is filtered at `output.balance().amount.0 >= N::DUST` in the scanner loop only for the `outputs.push` path, but the attribution pass requires *no* output from the attacker's tx at all) costs only fees. In fact the attacker's tx doesn't even need to pay Serai: any transaction in the block after a matching one triggers the overwrite, since the attribution loop runs whenever the cumulative `outputs` is non-empty.

### Likelihood Explanation
- Reachable by any party able to get a transaction mined in the same block as a Serai deposit (standard Bitcoin, no special access).
- ScriptPubkeys are deterministic functions of the group key (`Scanner::new` inserts `p2tr_script_buf(key)` with `Scalar::ZERO`; `register_offset` inserts Branch/Change/Forwarded scripts), so the watched scripts are publicly computable. [2](#0-1) [3](#0-2) 

- The only mitigant is that `data` is only applied to `OutputType::External` outputs; `presumed_origin` is corrupted for all kinds, which affects origin accounting for Branch/Change/Forwarded outputs as well.

### Recommendation
Scope the attribution per transaction:

```rust
for tx in &block.txdata[1 ..] {
  let mut tx_outputs: Vec<Output> = scanner.scan_transaction(tx).into_iter().map(...).collect();
  if tx_outputs.is_empty() { continue; }
  let presumed_origin = ...;
  let data = Self::extract_serai_data(tx);
  for output in &mut tx_outputs { ... }
  outputs.extend(tx_outputs);
}
```

This removes the cross-transaction ordering dependence entirely, matching the Drippie recommendation of eliminating same-block re-execution effects rather than relying on ordering assumptions.

### Proof of Concept
1. Block contains tx A at index 1: a victim deposit paying `p2tr_script_buf(group_key)` (External) with a valid Serai OP_RETURN InInstruction.
2. Attacker's tx B at index 2: any transaction — e.g., a self-payment — with no Serai data.
3. `get_outputs` processes A: pushes the victim `Output`, stamps A's data/origin. It then processes B: `scan_transaction` returns nothing, but `outputs` is non-empty, so `extract_serai_data(B)` (empty/attacker-controlled) is `clone_from`'d onto the victim output, and `presumed_origin` becomes B's `input[0]` spend.
4. The emitted `Output` for the victim's funds carries corrupted `data`/`presumed_origin`, misattributing the deposit.

### Citations

**File:** processor/src/networks/bitcoin.rs (L313-346)
```rust
// Always construct the full scanner in order to ensure there's no collisions
fn scanner(
  key: ProjectivePoint,
) -> (Scanner, HashMap<OutputType, Scalar>, HashMap<Vec<u8>, OutputType>) {
  let mut scanner = Scanner::new(key).unwrap();
  let mut offsets = HashMap::from([(OutputType::External, Scalar::ZERO)]);

  let zero = Scalar::ZERO.to_repr();
  let zero_ref: &[u8] = zero.as_ref();
  let mut kinds = HashMap::from([(zero_ref.to_vec(), OutputType::External)]);

  let mut register = |kind, offset| {
    let offset = scanner.register_offset(offset).expect("offset collision");
    offsets.insert(kind, offset);

    let offset = offset.to_repr();
    let offset_ref: &[u8] = offset.as_ref();
    kinds.insert(offset_ref.to_vec(), kind);
  };

  register(
    OutputType::Branch,
    *BRANCH_OFFSET.get_or_init(|| Secp256k1::hash_to_F(KEY_DST, b"branch")),
  );
  register(
    OutputType::Change,
    *CHANGE_OFFSET.get_or_init(|| Secp256k1::hash_to_F(KEY_DST, b"change")),
  );
  register(
    OutputType::Forwarded,
    *FORWARD_OFFSET.get_or_init(|| Secp256k1::hash_to_F(KEY_DST, b"forward")),
  );

  (scanner, offsets, kinds)
```

**File:** processor/src/networks/bitcoin.rs (L686-740)
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
      }

      if outputs.is_empty() {
        continue;
      }

      // populate the outputs with the origin and data
      let presumed_origin = {
        // This may identify the P2WSH output *embedding the InInstruction* as the origin, which
        // would be a bit trickier to spend that a traditional output...
        // There's no risk of the InInstruction going missing as it'd already be on-chain though
        // We *could* parse out the script *without the InInstruction prefix* and declare that the
        // origin
        // TODO
        let spent_output = {
          let input = &tx.input[0];
          let mut spent_tx = input.previous_output.txid.as_raw_hash().to_byte_array();
          spent_tx.reverse();
          let mut tx;
          while {
            tx = self.rpc.get_transaction(&spent_tx).await;
            tx.is_err()
          } {
            log::error!("couldn't get transaction from bitcoin node: {tx:?}");
            sleep(Duration::from_secs(5)).await;
          }
          tx.unwrap().output.swap_remove(usize::try_from(input.previous_output.vout).unwrap())
        };
        Address::new(spent_output.script_pubkey)
      };
      let data = Self::extract_serai_data(tx);
      for output in &mut outputs {
        if output.kind == OutputType::External {
          output.data.clone_from(&data);
        }
        output.presumed_origin.clone_from(&presumed_origin);
      }
    }

    outputs
  }
```

**File:** networks/bitcoin/src/wallet/mod.rs (L162-196)
```rust
  pub fn new(key: ProjectivePoint) -> Option<Scanner> {
    let mut scripts = HashMap::new();
    scripts.insert(p2tr_script_buf(key)?, Scalar::ZERO);
    Some(Scanner { key, scripts })
  }

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
          return Some(offset);
        }
        None => offset += Scalar::ONE,
      }
    }
  }
```
