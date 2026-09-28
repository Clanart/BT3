### Title
Attacker-spoofed output classification via publicly derivable scanner offsets leads to mis-credited/mis-processed funds - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
CVE-2023-1078 is a type confusion: `list_entry()` treats a list head as a contained element, so the wrong "type" of object is processed. The analog in Serai is a classification confusion in the Bitcoin output scanner: `Scanner::scan_transaction` identifies received outputs purely by `script_pubkey` membership in the `scripts` map and returns the associated `offset`. The processor then maps `offset -> OutputType` (`External`, `Branch`, `Change`, `Forwarded`) via `kinds` in `processor/src/networks/bitcoin.rs`. Because `Branch`/`Change`/`Forwarded` offsets are public deterministic values (`Secp256k1::hash_to_F(b"Serai Bitcoin Output Offset", ...)`) and `register_offset` increments the scalar until the tweaked key is even (making the exact registered offset computable by replaying the same loop), any unprivileged party can craft a P2TR output whose `script_pubkey` collides with an internal-kind address. The deposit is then reported as `Change`/`Forwarded` instead of `External`, so no deposit instruction data is attached and no plan is generated for it — the sender's funds are received but never processed as a deposit.

### Finding Description
`Scanner::scan_transaction` matches solely on `output.script_pubkey` and returns the registered offset:

```rust
if let Some(offset) = self.scripts.get(&output.script_pubkey) {
  res.push(ReceivedOutput { offset: *offset, ... });
}
``` [1](#0-0) 

The processor rebuilds the same scanner with deterministic, publicly computable offsets:

```rust
register(OutputType::Branch, *BRANCH_OFFSET.get_or_init(|| Secp256k1::hash_to_F(KEY_DST, b"branch")));
register(OutputType::Change, *CHANGE_OFFSET.get_or_init(|| Secp256k1::hash_to_F(KEY_DST, b"change")));
register(OutputType::Forwarded, *FORWARD_OFFSET.get_or_init(|| Secp256k1::hash_to_F(KEY_DST, b"forward")));
``` [2](#0-1) 

`register_offset` mutates the requested offset (`offset += Scalar::ONE` until the tweaked key is even), so the stored script corresponds to `offset + k` for small public `k` — fully reproducible by anyone who knows the group key. [3](#0-2) 

In `get_outputs`, the kind is taken from `kinds[offset_repr]` and deposit data (`extract_serai_data`) is only attached when `kind == OutputType::External`:

```rust
let kind = kinds[offset_repr_ref];
...
if output.kind == OutputType::External {
  output.data.clone_from(&data);
}
``` [4](#0-3) 

### Impact Explanation
An attacker who sends a transaction output paying to the derived `Change`/`Forwarded`/`Branch` script for the active multisig key causes the scanner to emit an `Output` with the wrong `OutputType`. Downstream, `instruction_from_output` handling in `processor/src/multisigs/mod.rs` will not treat it as an external deposit (no `External` data is attached), so the deposit is not credited to any instruction/plan, while the UTXO is real and spendable only by the multisig. The attacker's funds are locked inside Serai's multisig accounting as if they were internal change/forward outputs — a funds-received-but-not-properly-accounted condition, reachable purely from a public Bitcoin transaction an unprivileged party can broadcast. It can also poison `presumed_origin`/refund heuristics since `tx.input[0]` is used unconditionally for all matched outputs.

### Likelihood Explanation
Fully reachable: the offsets derive from a fixed public DST (`b"Serai Bitcoin Output Offset"`) and fixed labels, the multisig group key is public, and constructing the matching `script_pubkey` requires only recomputing `key + G*offset` (incrementing until even) and paying to it. No collusion or key material needed; cost is one Bitcoin transaction meeting `N::DUST`.

### Recommendation
Bind the output kind to authenticatable data rather than inferring it solely from `script_pubkey`: e.g., require an accompanying `InInstruction`/data field or an authenticated internal record (expected change address of a pending plan) before classifying an output as `Change`/`Forwarded`/`Branch`, and treat any script match without such evidence as `External` (or ignore it). Alternatively, include a private/domain-separated component in the internal-kind offsets so external parties cannot construct matching scripts.

### Proof of Concept
1. Obtain the multisig group key `K` (public).
2. Compute `offset = Secp256k1::hash_to_F(b"Serai Bitcoin Output Offset", b"change")`, then increment until `K + G*offset` is even — identical to `register_offset`'s loop.
3. Broadcast a Bitcoin transaction with an output to `p2tr_script_buf(K + G*offset)` and value ≥ `N::DUST`.
4. `get_outputs` returns `Output { kind: OutputType::Change, ... }` with no `data` and a `presumed_origin` taken from the attacker's `tx.input[0]`; the deposit is never processed as an external instruction even though the multisig received real funds.

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L180-196)
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
  }
```

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

**File:** processor/src/networks/bitcoin.rs (L308-346)
```rust
const KEY_DST: &[u8] = b"Serai Bitcoin Output Offset";
static BRANCH_OFFSET: OnceLock<Scalar> = OnceLock::new();
static CHANGE_OFFSET: OnceLock<Scalar> = OnceLock::new();
static FORWARD_OFFSET: OnceLock<Scalar> = OnceLock::new();

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

**File:** processor/src/networks/bitcoin.rs (L691-735)
```rust
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
```
