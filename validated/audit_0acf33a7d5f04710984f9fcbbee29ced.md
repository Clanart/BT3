### Title
Blocking RPC retry loop performed while holding the scanner write lock causes a permanent livelock of the processor - (File: processor/src/networks/bitcoin.rs)

### Summary
The kernel bug class behind CVE-2023-31082 is "a sleeping/blocking function invoked from a context where blocking is not permitted". The direct analog in Serai is `Bitcoin::get_outputs` being `await`ed — including an unbounded `while`-retry loop with `sleep()` — while the processor's `Scanner` holds its async `RwLock` write guard. An unprivileged party who sends a Bitcoin transaction paying to a Serai multisig script triggers `get_outputs` for the matching key; the RPC fetch of the transaction's first input's previous output is retried forever on error, so the write lock is never released and every other scanner operation (`register_key`, `ack_block`, `register_eventuality`, `ram_scanned`) spins forever in `yield_now()`.

### Finding Description
`Scanner::run` acquires the write lock before scanning a block and then performs network I/O under it: [1](#0-0) 

`get_outputs` is called per registered key while `scanner_lock` (a `RwLockWriteGuard`) is held — the code even carries `// TODO: These lines are the ones which will cause a really long-lived lock acquisition`. For Bitcoin, `get_outputs` iterates `block.txdata[1 ..]` and, whenever any matched output exists, resolves the "presumed origin" by fetching the transaction referenced by `tx.input[0].previous_output`: [2](#0-1) 

The retry loop `while { tx = self.rpc.get_transaction(&spent_tx).await; tx.is_err() } { sleep(5s) }` is an unconditional infinite loop: any persistent RPC failure (connection error, missing transaction index, pruning) causes `get_outputs` never to return. The same pattern exists in `get_eventuality_completions`, also awaited under the same lock, which loops with `sleep(Duration::from_secs(60))` until `get_block`/`get_block_number` succeed: [3](#0-2) 

Meanwhile, `ScannerHold::read`/`write`/`long_term_acquire` busy-spin with `tokio::task::yield_now()` waiting for the lock to become `Some`/available: [4](#0-3) 

So once the scanning task blocks inside `get_outputs`, every handle API deadlocks/livelocks rather than failing. This is precisely the invalid-context-blocking pattern: an operation that may sleep is executed while holding a lock that other tasks must acquire to make progress.

### Impact Explanation
A permanently stalled `Scanner` halts all output detection and eventuality completion for that network. Deposits to the multisig stop being observed (`ScannerEvent::Block`/`Completed` stop being emitted), `register_key` during multisig rotation cannot proceed, and `ack_block` cannot run — freezing the processor's view of the Bitcoin chain indefinitely. Funds received during the stall are never reported, and the busy `yield_now` spin additionally burns CPU. This is an availability impact on a network-critical path reachable purely by an unprivileged party broadcasting a valid Bitcoin transaction to a scanned address, matching the Medium severity of the reference advisory (availability-only, low complexity, no privileges required beyond sending a public transaction).

### Likelihood Explanation
Two triggers exist, both reached from public chain data:

1. **RPC failure persistence**: `get_transaction` is retried unconditionally. A transaction paying to the multisig whose first input spends a transaction the node cannot serve (no txindex, pruned blocks, or transient RPC errors overlapping the 5-second retry) makes the loop unbounded. Because the write lock is held for the entire retry, even a minutes-long RPC outage stalls all scanner APIs.
2. **Long gaps in eventuality history**: `get_eventuality_completions` re-fetches every historical block between `eventualities.block_number` and the tip with a 60-second sleep-per-failure loop under the lock, so a block-fetch failure similarly wedges the scanner.

No collusion, threshold corruption, or privileged access is needed — the attacker only causes a confirmed transaction to exist paying to a public multisig address.

### Recommendation
Do not perform fallible network I/O while holding `scanner_lock`. In `Scanner::run` (processor/src/multisigs/scanner.rs ~line 544), drop the write guard before calling `network.get_outputs`/`get_eventuality_completions` (collect key/eventuality state first, do I/O unlocked, then re-acquire to mutate and emit). Additionally, bound the retry loops in `Bitcoin::get_outputs` and `get_eventuality_completions` (processor/src/networks/bitcoin.rs ~lines 719-725 and 766-798) — propagate `NetworkError` after a timeout instead of retrying forever, and make `presumed_origin` resolution fault-tolerant (e.g., `Option`/best-effort) since it is auxiliary metadata.

### Proof of Concept
1. A Serai multisig key `K` is registered; `ScannerDb::keys` contains `K` and `scanner.keys` is non-empty.
2. An attacker broadcasts a confirmed Bitcoin transaction `T` paying to `p2tr_script_buf(K)` (the plain external output kind). No Serai privileges are required.
3. `Scanner::run` reaches the block containing `T`, takes `scanner_hold.write().await` (processor/src/multisigs/scanner.rs:544), iterates `scanner.keys`, and calls `network.get_outputs(&block, K)`.
4. Inside `get_outputs` (processor/src/networks/bitcoin.rs:686+), `scanner.scan_transaction(T)` matches `T`'s script_pubkey, so `outputs` is non-empty and the `presumed_origin` block runs, calling `self.rpc.get_transaction(&spent_tx)` on `T.input[0].previous_output`.
5. If the node returns an error for that prev-tx (connection error, `gettransaction` requiring txindex, pruned data), the `while tx.is_err() { sleep(5s) }` loop never exits. The write guard is held for the loop's entire duration.
6. Any concurrent call to `ScannerHandle::register_key`, `ack_block`, `register_eventuality`, or `ram_scanned` calls `ScannerHold::read`/`write`/`long_term_acquire`, which loops on `yield_now()` forever — the scanner livelocks and no further blocks, outputs, or completions are ever emitted. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** processor/src/multisigs/scanner.rs (L230-256)
```rust
  async fn read(&self) -> RwLockReadGuard<'_, Option<Scanner<N, D>>> {
    loop {
      let lock = self.scanner.read().await;
      if lock.is_none() {
        drop(lock);
        tokio::task::yield_now().await;
        continue;
      }
      return lock;
    }
  }
  async fn write(&self) -> RwLockWriteGuard<'_, Option<Scanner<N, D>>> {
    loop {
      let lock = self.scanner.write().await;
      if lock.is_none() {
        drop(lock);
        tokio::task::yield_now().await;
        continue;
      }
      return lock;
    }
  }
  // This is safe to not check if something else already acquired the Scanner as the only caller is
  // sequential.
  async fn long_term_acquire(&self) -> Scanner<N, D> {
    self.scanner.write().await.take().unwrap()
  }
```

**File:** processor/src/multisigs/scanner.rs (L543-567)
```rust
        // TODO: This lock acquisition may be long-lived...
        let mut scanner_lock = scanner_hold.write().await;
        let scanner = scanner_lock.as_mut().unwrap();

        let mut has_activation = false;
        let mut outputs = vec![];
        let mut completion_block_numbers = vec![];
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

**File:** processor/src/networks/bitcoin.rs (L714-727)
```rust
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
```

**File:** processor/src/networks/bitcoin.rs (L766-798)
```rust
    let this_block_hash = block.id();
    let this_block_num = (async {
      loop {
        match self.rpc.get_block_number(&this_block_hash).await {
          Ok(number) => return number,
          Err(e) => {
            log::error!("couldn't get the block number for {}: {}", hex::encode(this_block_hash), e)
          }
        }
        sleep(Duration::from_secs(60)).await;
      }
    })
    .await;

    for block_num in (eventualities.block_number + 1) .. this_block_num {
      let block = {
        let mut block;
        while {
          block = self.get_block(block_num).await;
          block.is_err()
        } {
          log::error!("couldn't get block {}: {}", block_num, block.err().unwrap());
          sleep(Duration::from_secs(60)).await;
        }
        block.unwrap()
      };

      check_block(eventualities, &block, &mut res);
    }

    // Also check the current block
    check_block(eventualities, block, &mut res);
    assert_eq!(eventualities.block_number, this_block_num);
```
