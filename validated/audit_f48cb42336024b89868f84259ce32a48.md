### Title
Outputs sent to a multisig key before its `activation_number` are never scanned and become permanently unspendable - (File: processor/src/multisigs/scanner.rs)

### Summary
The scanner enrolls each multisig key with an `activation_number` and, during block scanning, skips all blocks at or before the moment of activation-relative history: any output sent to the key's address in a block `< activation_number` is permanently ignored. The group key/address becomes public on Substrate when the key pair is set, strictly before the activation block is chosen and scanned. An unprivileged party can therefore send funds to a known multisig address before `register_key` runs (or before its activation block), and those funds are never emitted as `ScannerEvent`s, never enter the scheduler, and are never spendable — the analog of a vault added to an oracle after it already received deposits.

### Finding Description
`ScannerHandle::register_key` stores `(activation_number, key)` and asserts only that `activation_number > scanner.ram_scanned.unwrap_or(0)` — it does not check that no outputs already exist to the key's address on chain [1](#0-0) . The scan loop then iterates `scanner.keys` and skips every block before the activation block:

```rust
for (activation_number, key) in scanner.keys.clone() {
  if activation_number > block_being_scanned {
    continue;
  }
  ...
  for output in network.get_outputs(&block, key).await { ... }
}
``` [2](#0-1) 

The activation block is computed by Substrate as "queue block + CONFIRMATIONS", after the new multisig publishes its key pair — meaning the address is derivable and deposits can be made several blocks before `activation_number` [3](#0-2) . `MultisigManager::add_key` passes this `activation_block` straight through to `register_key` with no check for pre-existing deposits [4](#0-3) . Because `ram_scanned` and the DB `scanned_block` cursor only move forward, blocks `< activation_number` are never re-scanned for that key, so the outputs are permanently invisible.

The spec acknowledges funds may be sent prematurely ("If coins are prematurely sent to the new multisig, they're artificially delayed") but only covers sends *after* the activation block; nothing handles outputs that land in earlier confirmed blocks [5](#0-4) .

### Impact Explanation
Funds sent to a multisig address in a block earlier than `activation_number` are received on-chain under control of the threshold key, yet the processor never emits them as outputs, never schedules them, and never produces signatures spending them. They are effectively burned: "funds reported received that are not spendable" — here, funds actually received that are never even reported. Even if the processor is restarted, `Scanner::new`/`ram_scanned` resume from the DB cursor and the activation gate still skips those blocks.

### Likelihood Explanation
Any unprivileged party can compute the multisig's external address (e.g., `p2tr_script_buf(key)` for Bitcoin) as soon as the key pair is published on Substrate, and broadcast a deposit transaction that confirms in a block before `activation_number`. Key rotation is a routine, recurring event, so the window recurs every session. A malicious actor can also grief honest users by racing a dust or front-running deposit, or a user/UI acting on the published-but-not-yet-active address loses funds. Requires no validator collusion — just a public transaction.

### Recommendation
When registering a key, either (a) scan from the block at which the key pair was finalized on Substrate (or a safe lower bound) rather than `activation_number`, or (b) have the network layer verify at registration time that no outputs exist to the key's address in blocks `< activation_number` and reject/flag the activation if they do, mirroring the recommended "revert if deposits exist" fix. At minimum, document and emit an alert for pre-activation deposits, and provide a recovery path (e.g., explicit re-registration covering the earlier range) so such outputs are eventually forwarded rather than locked forever.

### Proof of Concept
1. A new validator set completes key generation and publishes `key_pair` on Substrate at session rotation; the external network address `addr = p2tr_script_buf(group_key)` (Bitcoin) is publicly derivable.
2. Attacker submits a Bitcoin transaction paying to `addr`, confirmed in block `B`.
3. Substrate finalizes the queue block and sets `activation_number = queue + CONFIRMATIONS`; `activate_key` calls `add_key(txn, activation_number, key)` → `register_key` [6](#0-5) .
4. If `B < activation_number`, the scan loop's `if activation_number > block_being_scanned { continue; }` skips `B` for this key forever; the output is never included in `outputs`, never emitted as `ScannerEvent::Block`, and never marked `seen`.
5. The multisig's scheduler never learns of the output, so no `Plan`/`SignableTransaction` ever spends it — the coins remain locked under the threshold key despite being fully confirmed on-chain.

### Citations

**File:** processor/src/multisigs/scanner.rs (L275-303)
```rust
  /// Register a key to scan for.
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

**File:** spec/processor/Multisig Rotation.md (L12-26)
```markdown
1) The new multisig is created, and has its keys set on Serai. Once the next
   `Batch` with a new external network block is published, its block becomes the
   "queue block". The new multisig is set to activate at the "queue block", plus
   `CONFIRMATIONS` blocks (the "activation block").

   We don't use the last `Batch`'s external network block, as that `Batch` may
   be older than `CONFIRMATIONS` blocks. Any yet-to-be-included-and-finalized
   `Batch` will be within `CONFIRMATIONS` blocks of what any processor has
   scanned however, as it'll wait for inclusion and finalization before
   continuing scanning.

2) Once the "activation block" itself has been finalized on Serai, UIs should
   start exclusively using the new multisig. If the "activation block" isn't
   finalized within `2 * CONFIRMATIONS` blocks, UIs should stop making
   transactions to any multisig on that network.
```

**File:** spec/processor/Multisig Rotation.md (L63-68)
```markdown
   shouldn't actually be sent coins during this period, making it irrelevant.
   If coins are prematurely sent to the new multisig, they're artificially
   delayed until the end of the `CONFIRMATIONS` blocks plus 10 minutes period.
   This prevents an adversary from minting Serai tokens using coins in the new
   multisig, yet then burning them to drain the prior multisig, creating a lack
   of liquidity for several blocks.
```

**File:** processor/src/multisigs/mod.rs (L237-255)
```rust
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

**File:** processor/src/main.rs (L195-223)
```rust
  async fn activate_key<N: Network, D: Db>(
    network: &N,
    substrate_mutable: &mut SubstrateMutable<N, D>,
    tributary_mutable: &mut TributaryMutable<N, D>,
    txn: &mut D::Transaction<'_>,
    session: Session,
    key_pair: KeyPair,
    activation_number: usize,
  ) {
    info!("activating {session:?}'s keys at {activation_number}");

    let network_key = <N as Network>::Curve::read_G::<&[u8]>(&mut key_pair.1.as_ref())
      .expect("Substrate finalized invalid point as a network's key");

    if tributary_mutable.key_gen.in_set(&session) {
      // See TributaryMutable's struct definition for why this block is safe
      let KeyConfirmed { substrate_keys, network_keys } =
        tributary_mutable.key_gen.confirm(txn, session, &key_pair);
      if session.0 == 0 {
        tributary_mutable.batch_signer =
          Some(BatchSigner::new(N::NETWORK, session, substrate_keys));
      }
      tributary_mutable
        .signers
        .insert(session, Signer::new(network.clone(), session, network_keys));
    }

    substrate_mutable.add_key(txn, activation_number, network_key).await;
  }
```
