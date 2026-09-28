### Title
Publicly-derivable deterministic output offsets let any third party inject misclassified outputs (deposits credited as internal `Change`/`Branch`, or outputs triggering `Forwarded` handling) - ([File: processor/src/networks/bitcoin.rs](processor/src/networks/bitcoin.rs))

### Summary
Analogous to the `createDrop()` report — where a salt taken purely from caller input lets an attacker pre-compute and squat on a deterministic address — the Bitcoin network code derives its `Branch`, `Change`, and `Forwarded` output offsets as `hash_to_F` of fixed public strings. The resulting P2TR `script_pubkey`s are therefore computable by anyone who knows the group key, which is public. `Scanner::scan_transaction` matches outputs on `script_pubkey` alone, so an attacker can send an on-chain payment to one of these deterministic addresses and have it ingested with the wrong `OutputType`. [1](#0-0) [2](#0-1) 

### Finding Description
`scanner()` builds the scan set deterministically: `BRANCH_OFFSET`, `CHANGE_OFFSET`, and `FORWARD_OFFSET` are each `Secp256k1::hash_to_F(b"Serai Bitcoin Output Offset", <fixed label>)`, stored in process-wide `OnceLock`s. [3](#0-2)  The scanned address is `p2tr(key + G*offset)`; since the group `key` is a public multisig key and the offsets are public constants, every registered script is publicly computable. `scan_transaction` then classifies any output whose `script_pubkey` appears in `self.scripts`, returning the recorded offset, and `get_outputs` maps that offset to an `OutputType` via `kinds[offset_repr_ref]`. [4](#0-3) [5](#0-4)  Nothing binds a payment to the internal action that legitimately produces these scripts — there is no equivalent of mixing `msg.sender` into the salt. The only guard is `register_offset`'s collision check, which runs at registration time and does nothing to stop an external party from paying to the resulting script. [6](#0-5) 

### Impact Explanation
Outputs crafted by an arbitrary third party are reported under an internal `OutputType` they did not originate from. A payment to the `Change`/`Branch` script is ingested as internal change/branching rather than an `External` deposit (deposit data is only attached for `OutputType::External`), so user funds sent to that address are not credited as deposits. [7](#0-6)  A payment to the `Forwarded` script is treated as a forward/refund flow the protocol never initiated, potentially driving unintended outbound transactions of scanned funds. In both cases the scanner reports funds under a semantics that does not match reality — funds "received" that are misattributed or consumed by the wrong code path.

### Likelihood Explanation
The attack requires only knowledge of the public group key plus the fixed `hash_to_F` labels, and one ordinary Bitcoin transaction paying to the derived P2TR script — the same cost as the mempool front-run in the source report. No validator access, leaked key, or malformed encoding is needed. The misclassification persists because `scan_transaction` has no way to distinguish "change we created" from "change script someone else paid to".

### Recommendation
Bind intent to the output. Options include: (a) only treat `Branch`/`Change`/`Forwarded`-kinded outputs as valid if they appear in transactions the processor itself produced (check against a local record of broadcast TXIDs/outpoints before emitting them as internal outputs), or (b) derive the offsets with a per-session/per-transaction secret component so the scripts are not publicly computable — the equivalent of adding `msg.sender` to the salt. At minimum, emit a warning and do not auto-process non-`External` outputs whose outpoint was not created by a known plan.

### Proof of Concept
1. Attacker observes the public multisig group key `K`.
2. Computes `offset = hash_to_F(b"Serai Bitcoin Output Offset", b"change")` (a fixed constant in `CHANGE_OFFSET`) and derives `script = p2tr_script_buf(K + G*offset)` — exactly what `scanner()` registers for `OutputType::Change`. [8](#0-7) 
3. Sends a normal Bitcoin transaction with an output to `script`, paying dust-or-more from any wallet.
4. `get_outputs` scans the block, `scan_transaction` matches `output.script_pubkey` in `self.scripts`, and the output is returned with `kind = OutputType::Change` and `presumed_origin` populated — ingested as an internal change output the processor never created, while a depositor legitimately paying this script would likewise never be credited as `External` (no `data` attached). [9](#0-8) [2](#0-1)

### Citations

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

**File:** processor/src/networks/bitcoin.rs (L730-736)
```rust
      let data = Self::extract_serai_data(tx);
      for output in &mut outputs {
        if output.kind == OutputType::External {
          output.data.clone_from(&data);
        }
        output.presumed_origin.clone_from(&presumed_origin);
      }
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
