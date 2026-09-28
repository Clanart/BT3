### Title
Outputs sent to a multisig address before its `activation_number` are permanently unscanned and unspendable — (`processor/src/multisigs/scanner.rs`)

### Summary
The processor's `Scanner` only scans for outputs to a multisig key in blocks at or after the key's `activation_number`, and refuses to register a key whose activation is at or before the already-scanned tip. There is no migration/catch-up path: a Bitcoin (or other external-network) deposit made to a key's publicly derivable external address in the gap between the key becoming known and its activation block is never detected, never credited, and—unlike the audit report's "withdraw and redeposit" workaround—cannot be moved because only the multisig can spend it and the processor never learns the output exists.

### Finding Description
In the external report, deposits made before the reward hook was connected were never accounted for, and the client's only remediation was a manual withdraw/redeposit. The Serai analog is the multisig scanner's activation-block model:

- `ScannerHandle::register_key` stores `(activation_number, key)` and asserts `activation_number > scanner.ram_scanned.unwrap_or(0)`, i.e. activation is always in the future, and initializes `ram_scanned`/`save_scanned_block` at `activation_number` for the first key. [1](#0-0) 
- In `Scanner::run`, each block is only scanned for keys with `activation_number <= block_being_scanned` (`if activation_number > block_being_scanned { continue; }`), and scanning only proceeds forward from the last scanned block. Outputs in earlier blocks are never revisited. [2](#0-1) 
- The deposit address is publicly derivable: the `External` output kind uses offset `Scalar::ZERO`, i.e. the plain `p2tr_script_buf(key)` / `N::external_address`, which any party can compute once the group key is known (it is published on Serai when `set_keys` is confirmed, before the processor's `activation_block`). [3](#0-2) 
- `MultisigManager::add_key` registers the key at an `activation_block` chosen in the future (comments note activation only occurs after a "too far ahead" window to keep `block_number` monotonic), so there is a real on-chain window where the address is live but not scanned. [4](#0-3) 
- There is no equivalent of the recommended migration: `retire_key`, `save_scanned_block`, and the `seen`/`ram_outputs` dedup logic only ever move forward, and the duplicate-output panic path actually *panics* on a second sighting rather than crediting late-discovered outputs. [5](#0-4) 

### Impact Explanation
Any unprivileged party can compute `external_address(key)` (e.g., `p2tr_script_buf(key)` for Bitcoin) as soon as the group key is public and send a transaction to it before `activation_number`. The resulting UTXO is real, confirmed, and spendable only by the threshold key — but the processor never emits a `ScannerEvent` for it, never creates a `Plan`, and never signs for it. Since Bitcoin UTXOs cannot be "redeposited" without the multisig signing a spend, the audit's suggested workaround does not exist here: the funds are permanently locked in the multisig. This matches the "funds received that are not spendable" acceptance criterion.

### Likelihood Explanation
The window is bounded (key publication on Serai → processor activation block on the external network), so it requires the deposit to land within that gap. However, no user-facing mechanism prevents it — the address is valid and payable the moment the key exists, and nothing rejects or warns about pre-activation deposits. A user following a cached/published address, or a deliberate self-inflicted deposit to grief/claim confusion during rotation (the second key in `scanner.keys` also gets an activation in the future while the old key is still scanned), can trigger it. Given permanence of loss, Medium–High.

### Recommendation
When registering a key, scan from the block at which the key became publicly known (e.g., the external-network block contemporary with the `set_keys` confirmation) rather than `activation_number`, or perform a one-time backward rescan of the key's addresses over the gap blocks at activation. Alternatively, register the key's birth block explicitly in `ScannerDb::register_key` and have `run` iterate `max(birth_block, activation_number - window)`. If pre-activation deposits are an accepted design choice (mirroring the client's stance in the report), this must be documented as a protocol invariant and ideally enforced by not exposing the address until activation — which is not currently enforceable since the derivation is public.

### Proof of Concept
1. DKG completes; `set_keys` confirms key `K` on Serai at time T. The processor calls `add_key(txn, activation_block = B, K)` where `B` is an external-network block in the future (asserted `> ram_scanned` at `processor/src/multisigs/scanner.rs:286-289`).
2. Attacker (or uninformed user) computes `addr = Address::from_script(&p2tr_script_buf(K).unwrap(), network)` — the same construction the tests use — and broadcasts a Bitcoin tx paying `addr` at block `B - k` (`processor/src/tests/wallet.rs:44-49` shows the address derivation is purely public).
3. `Scanner::run` reaches block `B` and begins scanning for `K`; blocks `< B` are skipped for `K` via `activation_number > block_being_scanned → continue` (`processor/src/multisigs/scanner.rs:550-553`).
4. The UTXO is never emitted as a `ScannerEvent::Block` output, never enters `ram_outputs`/`seen`, and no `Plan` is ever created (`processor/src/multisigs/mod.rs:573-602`). The deposit is confirmed on-chain yet permanently unspendable, with no migration path available even to the protocol itself.

Uncertainty: `processor/src/main.rs` (where `activation_block` is chosen) and `mini/src/tests/activation_race` were not fully read; the existence of an `activation_race` test suggests the gap is a known design consideration, which would make this an accepted-but-undocumented-loss vector rather than an oversight. The core mechanics — forward-only scanning and the `activation_number` skip — are verified in the cited code.

### Citations

**File:** processor/src/multisigs/scanner.rs (L276-303)
```rust
  pub async fn register_key(
    &mut self,
    txn: &mut D::Transaction<'_>,
    activation_number: usize,
    key: <N::Curve as Ciphersuite>::G,
  ) {
    info!("Registering key {} in scanner at {activation_number}", hex::encode(key.to_bytes()));

    let mut scanner_lock = self.scanner.write().await;
    let scanner = scanner_lock.as_mut().unwrap();
    assert!(
      activation_number > scanner.ram_scanned.unwrap_or(0),
      "activation block of new keys was already scanned",
    );

    if scanner.keys.is_empty() {
      assert!(scanner.ram_scanned.is_none());
      scanner.ram_scanned = Some(activation_number);
      assert!(ScannerDb::<N, D>::save_scanned_block(txn, activation_number).is_empty());
    }

    ScannerDb::<N, D>::register_key(txn, activation_number, key);
    scanner.keys.push((activation_number, key));
    #[cfg(not(test))] // TODO: A test violates this. Improve the test with a better flow
    assert!(scanner.keys.len() <= 2);

    scanner.eventualities.insert(key.to_bytes().as_ref().to_vec(), EventualitiesTracker::new());
  }
```

**File:** processor/src/multisigs/scanner.rs (L550-567)
```rust
        for (activation_number, key) in scanner.keys.clone() {
          if activation_number > block_being_scanned {
            continue;
          }

          if activation_number == block_being_scanned {
            has_activation = true;
          }

          let key_vec = key.to_bytes().as_ref().to_vec();

          // TODO: These lines are the ones which will cause a really long-lived lock acquisition
          for output in network.get_outputs(&block, key).await {
            assert_eq!(output.key(), key);
            if output.balance().amount.0 >= N::DUST {
              outputs.push(output);
            }
          }
```

**File:** processor/src/multisigs/scanner.rs (L634-640)
```rust
          let seen = ScannerDb::<N, D>::seen(&db, &id);
          let id = id.as_ref().to_vec();
          if seen || scanner.ram_outputs.contains(&id) {
            panic!("scanned an output multiple times");
          }
          scanner.ram_outputs.insert(id);
        }
```

**File:** processor/src/networks/bitcoin.rs (L314-347)
```rust
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

**File:** processor/src/multisigs/mod.rs (L216-255)
```rust
  /// Returns the block number for a block hash, if it's known and all keys have scanned the block.
  // This is guaranteed to atomically increment so long as no new keys are added to the scanner
  // which activate at a block before the currently highest scanned block. This is prevented by
  // the processor waiting for `Batch` inclusion before scanning too far ahead, and activation only
  // happening after the "too far ahead" window.
  pub async fn block_number<G: Get>(
    &self,
    getter: &G,
    hash: &<N::Block as Block<N>>::Id,
  ) -> Option<usize> {
    let latest = ScannerHandle::<N, D>::block_number(getter, hash)?;

    // While the scanner has cemented this block, that doesn't mean it's been scanned for all
    // keys
    // ram_scanned will return the lowest scanned block number out of all keys
    if latest > self.scanner.ram_scanned().await {
      return None;
    }
    Some(latest)
  }

  pub async fn add_key(
    &mut self,
    txn: &mut D::Transaction<'_>,
    activation_block: usize,
    external_key: <N::Curve as Ciphersuite>::G,
  ) {
    self.scanner.register_key(txn, activation_block, external_key).await;
    let viewer = Some(MultisigViewer {
      activation_block,
      key: external_key,
      scheduler: N::Scheduler::new::<D>(txn, external_key, N::NETWORK),
    });

    if self.existing.is_none() {
      self.existing = viewer;
      return;
    }
    self.new = viewer;
  }
```
