### Title
Unprivileged sender can force funds to be recorded under internal `OutputType`s by paying publicly derivable offset scripts - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary

Analogous to CVE-2017-15196 (an authenticated party altering form data to modify resources belonging to another's private project), Serai's Bitcoin scanner classifies every received output solely by `script_pubkey`, and the internal-purpose scripts (branch, change, forward) are deterministically derivable from public data — the group key plus fixed `hash_to_F` offsets. Any unprivileged on-chain sender can therefore craft a transaction whose outputs the processor records as internal `Branch`/`Change`/`Forwarded` outputs of the multisig rather than `External` deposits, supplying "form data" (their transaction's script and embedded payload) that mutates the accounting and output classification of resources belonging to another party's key domain.

### Finding Description

`Scanner` in `networks/bitcoin/src/wallet/mod.rs` holds `scripts: HashMap<ScriptBuf, Scalar>` and `scan_transaction` matches `output.script_pubkey` only:

- `scan_transaction` returns a `ReceivedOutput` for any transaction output whose script is in `self.scripts`, with the registered `offset` [1](#0-0) 
- `register_offset` derives scripts as `p2tr_script_buf(key + G*offset)` [2](#0-1) 

In `processor/src/networks/bitcoin.rs`, the processor's `scanner()` registers deterministic internal offsets via `Secp256k1::hash_to_F(KEY_DST, b"branch" | b"change" | b"forward")` and builds a `kinds` map from offset bytes to `OutputType` (External/Branch/Change/Forwarded) [3](#0-2) . Both `KEY_DST` (`b"Serai Bitcoin Output Offset"`) and the group key are public, so an unprivileged sender can compute the branch/change/forward P2TR scripts offline.

There is no authorization binding between the sender and the internal classification: whoever pays a registered script gets that script's `OutputType`. `Output::key()` then reconstructs the base key as `xonly(script) - offset*G` [4](#0-3) , so the forged internal output is attributed to the multisig key with attacker-chosen internal semantics, and (for types that consume witness/script data such as the `OP_SHA256 … OP_EQUALVERIFY` message path exercised in `processor/src/tests/literal/mod.rs`) attacker-controlled `data`.

### Impact Explanation

An unprivileged external party can inject outputs that the processor treats as protocol-internal:

- Outputs sent to the `Forwarded` script cause the multisig to plan and sign forwarding transactions driven by attacker-supplied data — i.e., the multisig signs spends whose classification and handling path the attacker selected rather than the protocol.
- Outputs sent to `Change`/`Branch` scripts are recorded as internal outputs (not `External` deposits), bypassing the external-deposit path and its credit/origin handling (`presumed_origin` is only meaningful for external classification). This corrupts internal accounting — the scheduler consumes these as internally-owned inputs in `prepare_send`/rotation flows.

This yields attacker-triggered signing of unintended transactions and misattribution of funds within the multisig's key domain, matching the CVE's shape: attacker-supplied data acting on another party's private resource due to a missing ownership/context binding on the action.

### Likelihood Explanation

Reachability is high: the only cost is broadcasting a Bitcoin transaction paying ≥ `DUST` (10_000 sat) to a script computed from the public group key and the fixed `hash_to_F` offsets [5](#0-4) . No validator status, collusion, or private data is needed. Impact is bounded by the value the attacker must lock up, but the forged classification is granted automatically on every scan; the practical severity is Medium (integrity of internal accounting / attacker-steered internal handling rather than direct theft of other users' funds).

### Recommendation

Bind internal output types to protocol origin instead of bare script matching:

1. For `Change`/`Branch`/`Forwarded`, require corroborating evidence of protocol authorship — e.g., only classify an output as internal if it appears in a transaction whose txid/eventuality the scheduler itself produced (`Eventuality`/`EventualitiesTracker`), or commit a nonce/MAC into the internal tweak: `offset = hash_to_F(DST, kind || internal_secret)` so external parties cannot derive the script.
2. Alternatively, keep `register_offset` scripts private by deriving them from a secret held by the validator set rather than a fixed public DST, so `scan_transaction` matches cannot be forged by outsiders.
3. Treat any `scripts` match not originated by the processor as `External`, and emit a warning metric for script collisions.

### Proof of Concept

```rust
// Attacker-side (no validator role needed)
let group_key: ProjectivePoint = /* public multisig key */;
let scanner_key = group_key; // Scanner::new(group_key) uses key + ZERO offset

// Derive the protocol's internal "forward" script from the public DST
let fwd_offset = Secp256k1::hash_to_F(b"Serai Bitcoin Output Offset", b"forward");
let mut fwd_script = None;
let mut o = fwd_offset;
// replicate register_offset's evenness loop
while fwd_script.is_none() {
  fwd_script = p2tr_script_buf(scanner_key + ProjectivePoint::GENERATOR * o);
  o += Scalar::ONE;
}

// Pay >= 10_000 sat to fwd_script in a normal Bitcoin tx.
// Processor scanner() registered this script under OutputType::Forwarded,
// so scan_transaction returns it as a ReceivedOutput and the processor
// emits an Output with kind == Forwarded and attacker-chosen `data`,
// triggering internal forwarding/scheduling on a deposit the protocol
// never created — classification was granted purely by script match.
```

Note: I was unable to inspect the matched lines of `get_outputs`/kind assignment in `processor/src/networks/bitcoin.rs` (the final grep returned only match counts), so the exact wiring from `kinds` to emitted `OutputType` is inferred from the `scanner()` constructor and the `Output`/`OutputType` read/write paths shown; the script-only matching root cause in `Scanner::scan_transaction` is directly confirmed.

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

**File:** processor/src/networks/bitcoin.rs (L308-347)
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
}
```
