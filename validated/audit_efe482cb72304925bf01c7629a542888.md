### Title
Anyone can inject outputs into the multisig's internal `Branch`/`Change`/`Forwarded` offset namespaces, causing attacker-controlled UTXOs to be misclassified as protocol-internal outputs - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
The CVE-2026-13444 bug class is "shared resource keyed only by an attacker-reproducible namespace identifier": an attacker who computes the same `persist_directory`/`collection_name` reads and pollutes another user's data. Serai's analog is `Scanner`: outputs are matched to the multisig purely by `script_pubkey` (the shared namespace), and all derived deposit namespaces (`External`, `Branch`, `Change`, `Forwarded`) use publicly computable offsets. An arbitrary Bitcoin sender can therefore insert outputs into namespaces intended to be internal-only.

### Finding Description
`Scanner::new`/`register_offset` build a `scripts: HashMap<ScriptBuf, Scalar>` keyed solely by `script_pubkey` [1](#0-0) . `scan_transaction` returns a `ReceivedOutput` for *any* transaction output whose `script_pubkey` collides with a registered script, with no provenance or authorization check [2](#0-1) .

The processor registers deterministic, publicly derivable offsets for internal output types: `External` = `Scalar::ZERO`, and `Branch`/`Change`/`Forwarded` = `Secp256k1::hash_to_F(KEY_DST, b"branch"|"change"|"forward")` with the fixed `KEY_DST = b"Serai Bitcoin Output Offset"` [3](#0-2) [4](#0-3) . `register_offset` only bumps an offset to the next even point, so the resulting script for each type is fully computable offline by anyone [5](#0-4) .

The `kinds` map then attributes each `ReceivedOutput` to an `OutputType` by its offset [6](#0-5) . Consequently an attacker who broadcasts a transaction paying to the `Change` (or `Branch`/`Forwarded`) script produces a `ReceivedOutput` classified as `OutputType::Change` — an output type reserved for protocol-internal traffic — even though the attacker is external and the multisig never created that output.

### Impact Explanation
- **Namespace pollution / misattribution**: attacker-origin UTXOs enter the internal `Change`/`Branch`/`Forwarded` output set and are reported to the scheduler as internal funds. Any downstream logic that treats `Change`/`Branch` outputs as protocol-created (e.g., for re-keying, batching, or accounting of internal flows) now operates on attacker-inserted entries — the direct analog of the Langflow attacker inserting documents into the victim's shared collection.
- **Forced fee burn / griefing**: because every matching output is reported, repeated dust deposits to the deterministic internal scripts either get consumed as inputs in later multisig transactions (increasing tx size and fee cost borne by the multisig) or accumulate as economically unspendable dust in the tracked set.
- The received outputs remain cryptographically spendable (the offset is recorded), so this is integrity/accounting impact rather than direct theft — analogous to CVE-2026-13444's write-pollution side rather than its read side.

### Likelihood Explanation
Trivially reachable by any unprivileged party: the only requirement is broadcasting a standard Bitcoin transaction paying to a computable P2TR script. No secrets, no validator status, no malformed encodings required. The parity-bump behavior of `register_offset` makes every internal script address derivable with certainty [5](#0-4) .

### Recommendation
- Bind received outputs to protocol context, not just `script_pubkey`: e.g., only classify an output as `Branch`/`Change`/`Forwarded` if the multisig previously created a transaction producing that output type (track expected outpoints), and classify all other inbound payments to those scripts as `External` deposits or quarantine them.
- Alternatively, mix a per-session secret (or the multisig's actual group key material plus a non-public tweak commitment) into `KEY_DST` so external parties cannot derive internal scripts.
- At minimum, filter `ReceivedOutput`s below `DUST` before reporting them to the scheduler, and document that internal-type scripts are publicly derivable namespaces that must not be assumed exclusive to protocol-created outputs.

### Proof of Concept
```rust
// Attacker, knowing only the multisig's public group key `key`:
// 1. Reproduce the internal offsets (all constants are public).
let change_offset = Secp256k1::hash_to_F(b"Serai Bitcoin Output Offset", b"change");
// 2. Bump to even parity exactly as register_offset does.
let mut off = change_offset;
while !is_even(key + (ProjectivePoint::GENERATOR * off)) { off += Scalar::ONE; }
// 3. Compute the internal "change" script_pubkey via p2tr_script_buf(key + G*off)
//    and send a transaction paying it.
// 4. The victim's Scanner::scan_transaction returns a ReceivedOutput with
//    offset == off, which the processor's `kinds` map attributes to
//    OutputType::Change — an attacker-created UTXO now classified as
//    protocol-internal change.
```

Note: the exact downstream scheduler behavior for misclassified `Change` outputs was not fully verified within scope (I confirmed the classification path in `processor/src/networks/bitcoin.rs::scanner` and `Scanner::scan_transaction`, but not every consumer of `OutputType::Change`); the core finding — unauthenticated insertion into an internal-only namespace via publicly derivable identifiers — stands on the cited code regardless.

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L153-196)
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
