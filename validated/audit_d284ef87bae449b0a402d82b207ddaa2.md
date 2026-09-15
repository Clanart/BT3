## Analysis Result

### Title
Failed transaction bundles leave orphaned trie-pruning marks that corrupt the state trie database on live-pruning miners - (File: `work/worker.go`, `storage/statedb/trie.go`, `blockchain/state/statedb.go`)

### Summary
The Xenstore CVE describes orphaned database nodes created inside a transaction that are not cleaned up when the transaction errors out, and which can become permanently committed. Kaia's bundle-execution / live-pruning path has the same bug class: trie mutations performed while executing a multi-tx bundle mark underlying trie nodes as prunable, but if the bundle later fails and is rolled back, those pruning marks are **not** undone, so live nodes can be permanently deleted from the state database.

### Finding Description
When a block-proposing node has live pruning enabled, its trie is opened with a `PruningBlockNumber` via `BlockChain.PrunableStateAt` [1](#0-0) . Any trie mutation (insert/delete) performed on that trie calls `markPrunableNode`, which unconditionally records a pruning mark for the *previous* version of the node into `t.pruningMarksCache`, independent of whether the enclosing operation later succeeds [2](#0-1) .

During bundle building, `Task.commitBundleTransaction` executes each tx in a bundle via `bc.ApplyTransaction`, which internally calls `StateDB.Finalise`, updating/deleting state objects (and therefore committing trie mutations that trigger `markPrunableNode`) [3](#0-2) . If a subsequent tx in the same bundle fails or reverts, the code only restores the high-level `StateDB` (state objects, gas counters, tcount) via `env.state.Set(lastSnapshot)` [4](#0-3) . This `Set`/`copyStateDB` path copies the trie object itself (`dst.trie = src.db.CopyTrie(src.trie)`) but has no mechanism to undo pruning marks that were already appended to the underlying trie `Database`'s `pruningMarksCache` during the failed execution [5](#0-4) .

Those orphaned pruning marks are later flushed to disk and, once the retention window elapses, the referenced (still-live) trie nodes are deleted by the pruning loop [6](#0-5) , permanently corrupting the node's state trie database — exactly the "orphaned nodes... made permanent in the data base" pattern described in the Xenstore advisory.

This exact issue is already acknowledged in the Kaia test suite itself:

> "State db will be broken because nodes marked as pruning during executing the bundle will be left in state db even though the bundle is reverted. This is because the state.Finalise() will store nodes as pruning before restoring the state." [7](#0-6) , with the corresponding regression test confirming block generation errors out when live pruning is enabled and a bundle is intentionally made to fail [8](#0-7) .

### Impact Explanation
An unprivileged actor who can get a transaction included in a bundle (e.g., an auction bidder via `SubmitBid`/`kaiax/auction`, a gasless swap user via `kaiax/gasless`, or any sender whose tx is grouped by a `TxBundlingModule`) can deliberately cause a later transaction in the bundle to fail (nonce mismatch, EVM revert, out-of-gas) to trigger `restoreEnv`. On any block-proposing (miner) node running with live pruning enabled, this leaves orphaned pruning marks that eventually cause deletion of still-referenced trie nodes from that node's persistent state database. This matches the CVE's impact profile precisely: no confidentiality or integrity impact, but a severe availability impact (`A:H`) — the miner's state database becomes corrupted/unreadable (`MissingNodeError`), potentially halting block production or causing the affected proposer to diverge/crash relative to honest peers without live pruning.

### Likelihood Explanation
The trigger requires only submitting a transaction (or bid) that can be bundled with another transaction that predictably fails — no special privileges, validator role, or peer/network position needed. It only manifests on validator/proposer nodes that opt into live pruning (`WritePruningEnabled`), which limits blast radius but does not require any cooperation beyond normal public transaction/bid submission. The bug is already reproduced and acknowledged by an existing repository test (`makeTestTxBundleLivePruningScenario(true)`), confirming it is a live, reachable condition rather than a theoretical one.

### Recommendation
When a bundle transaction fails and `restoreEnv`/`StateDB.Set` rolls back state, also roll back any pruning marks recorded in the underlying `storage/statedb.Database.pruningMarksCache` (or `Trie.pruningMarksCache`) that were added during the aborted portion of the bundle — e.g., by snapshotting/truncating the pruning-mark list at the same point the state snapshot was taken, or by deferring pruning-mark commitment until the entire bundle (not just the per-tx `Finalise`) succeeds.

### Proof of Concept
1. Enable live pruning on a block-proposing node (`db.WritePruningEnabled()` equivalent to `--state.live-pruning`).
2. Submit two transactions from the same sender designed to be bundled together (e.g., via the gasless/auction bundling modules or any custom `TxBundlingModule`), where the second transaction is crafted to fail deterministically (e.g., wrong nonce or a contract call that reverts due to insufficient balance/gas).
3. During block building, `Task.commitBundleTransaction` executes tx1 (mutating/finalizing trie state and recording pruning marks for superseded nodes), then tx2 fails; `restoreEnv()` reverts the `StateDB` but leaves the pruning marks recorded for tx1's trie mutations in place.
4. After the `LivePruningRetention` window passes, the pruning loop (`pruneTrieNodeLoop`) deletes the marked nodes from disk, even though those nodes are still referenced by the canonical (reverted) trie, corrupting the proposer's persistent state database (reproduced by `TestTxBundleWithLivePruningByMiner` at `tests/kaia_scenario_test.go:2257-2306`, which asserts an error is returned exactly due to this condition).

### Citations

**File:** blockchain/blockchain.go (L809-819)
```go
// PrunableStateAt returns a new mutable state based on a particular point in time.
// If live pruning is enabled on the databse, and num is nonzero, then trie will mark obsolete nodes for pruning.
func (bc *BlockChain) PrunableStateAt(root common.Hash, num uint64) (*state.StateDB, error) {
	if bc.IsLivePruningRequired() {
		return state.New(root, bc.stateCache, bc.snaps, &statedb.TrieOpts{
			PruningBlockNumber: num,
		})
	} else {
		return bc.StateAt(root)
	}
}
```

**File:** blockchain/blockchain.go (L1555-1583)
```go
func (bc *BlockChain) pruneTrieNodeLoop() {
	// ReadPruningMarks(1, limit) is very slow because it iterates over the most of MiscDB.
	// ReadPruningMarks(start, limit) is much faster because it only iterates a small range.
	startNum := uint64(1)

	bc.wg.Go(func() {
		for {
			select {
			case num := <-bc.chPrune:
				if num <= bc.cacheConfig.LivePruningRetention {
					continue
				}
				limit := num - bc.cacheConfig.LivePruningRetention // Prune [1, latest - retention]

				startTime := time.Now()
				marks := bc.db.ReadPruningMarks(startNum, limit+1)
				bc.db.PruneTrieNodes(marks)
				bc.db.DeletePruningMarks(marks)
				bc.db.WriteLastPrunedBlockNumber(limit)

				logger.Info("Pruned trie nodes", "number", num, "start", startNum, "limit", limit,
					"count", len(marks), "elapsed", time.Since(startTime))

				startNum = limit + 1
			case <-bc.quit:
				return
			}
		}
	})
```

**File:** storage/statedb/trie.go (L606-626)
```go
// Mark the node for later pruning by writing PruningMark to database.
func (t *Trie) markPrunableNode(n node) {
	// Mark nodes only if both conditions are met:
	// - t.pruning: database has pruning enabled, i.e. nodes are stored with ExtHash
	// - t.PruningBlockNumber: requested pruning through state.New -> OpenTrie -> NewTrie.
	if !t.pruning || t.PruningBlockNumber == 0 {
		return
	}

	if hn, ok := n.(hashNode); ok {
		// If a node exists as a hashNode, it means the node is either:
		// (1) lives in database but yet to be resolved - subject to pruning,
		// (2) collapsed by Hash or Commit - may or may not be in database, add the mark anyway.
		t.pruningMarksCache[common.BytesToExtHash(hn)] = t.PruningBlockNumber
	} else if hn, _ := n.cache(); hn != nil {
		// If node.flags.hash is nonempty, it means the node is either:
		// (1) loaded from database - subject to pruning,
		// (2) went through hasher by Hash or Commit - may or may not be in database, add the mark anyway.
		t.pruningMarksCache[common.BytesToExtHash(hn)] = t.PruningBlockNumber
	}
}
```

**File:** blockchain/state/statedb.go (L949-954)
```go
func copyStateDB(dst, src *StateDB) {
	// Copy all the basic fields, initialize the memory ones
	dst.db = src.db
	dst.trie = src.db.CopyTrie(src.trie)
	dst.trieOpts = src.trieOpts

```

**File:** blockchain/state/statedb.go (L1071-1105)
```go
// Finalise finalises the state by removing the self destructed objects
// and clears the journal as well as the refunds.
func (stateDB *StateDB) Finalise(deleteEmptyObjects bool, setStorageRoot bool) {
	for addr := range stateDB.journal.dirties {
		so, exist := stateDB.stateObjects[addr]
		if !exist {
			// ripeMD is 'touched' at block 1714175, in tx 0x1237f737031e40bcde4a8b7e717b2d15e3ecadfe49bb1bbc71ee9deb09c6fcf2
			// That tx goes out of gas, and although the notion of 'touched' does not exist there, the
			// touch-event will still be recorded in the journal. Since ripeMD is a special snowflake,
			// it will persist in the journal even though the journal is reverted. In this special circumstance,
			// it may exist in `stateDB.journal.dirties` but not in `stateDB.stateObjects`.
			// Thus, we can safely ignore it here
			continue
		}

		if so.selfDestructed || (deleteEmptyObjects && so.empty()) {
			stateDB.deleteStateObject(so)

			// If state snapshotting is active, also mark the destruction there.
			// Note, we can't do this only at the end of a block because multiple
			// transactions within the same block might self destruct and then
			// resurrect an account; but the snapshotter needs both events.
			if stateDB.snap != nil {
				stateDB.snapDestructs[so.addrHash] = struct{}{} // We need to maintain account deletions explicitly (will remain set indefinitely)
				delete(stateDB.snapAccounts, so.addrHash)       // Clear out any previously updated account data (may be recreated via a resurrect)
				delete(stateDB.snapStorage, so.addrHash)        // Clear out any previously updated storage data (may be recreated via a resurrect)
			}
		} else {
			so.updateStorageTrie(stateDB.db)
			so.setStorageRoot(setStorageRoot, stateDB.stateObjectsDirtyStorage)
			stateDB.updateStateObject(so)
		}
		so.created = false
		stateDB.stateObjectsDirty[addr] = struct{}{}
	}
```

**File:** work/worker.go (L896-903)
```go
	restoreEnv := func() {
		env.state.Set(lastSnapshot)
		env.header.GasUsed = gasUsedSnapshot
		env.tcount = tcountSnapshot
		// blob related env are restored to the snapshot
		env.header.BlobGasUsed = blobGasUsedSnapshot
		env.blobs = blobsSnapshot
	}
```

**File:** tests/kaia_scenario_test.go (L1953-1967)
```go
			// TestTxBundleWithLivePruningByMiner tests a following scenario:
			//  1. Transfer (rewardBase -> anon) using a legacy transaction.
			//     This is just deposit for anon.
			//  2. Transfer (anon -> validator) using a legacy transaction two times.
			//     Create a mock TxBundlingModule and create a bundle for each two tx.
			//     At that point, tx0 and tx1 are bundled, and then tx1 fails due to nonceTooHigh, so it is reverted.
			//
			// This test confirms that miners with live pruning cannot process failing bundles.
			// All nodes marked as pruning during executing the bundle will be left in state db even though the bundle is reverted.
			// This is because the state.Finalise() will store nodes as pruning before restoring the state.
			name:            "TestTxBundleWithLivePruningByMiner",
			runScenario:     makeTestTxBundleLivePruningScenario(true),
			bundleFuncMaker: bundleEachTwoTxs,
			cacheConfig:     cacheConfigForLivePruning(),
		},
```

**File:** tests/kaia_scenario_test.go (L2257-2306)
```go
func makeTestTxBundleLivePruningScenario(livePruningEnabled bool) func(*testing.T, *BCData, *TestAccountType, *TestAccountType, *TestAccountType, *AccountMap, *profile.Profiler, []kaiax.TxBundlingModule) {
	return func(t *testing.T, bcdata *BCData, rewardBase, validator, anon *TestAccountType, accountMap *AccountMap, prof *profile.Profiler, txBundlingModules []kaiax.TxBundlingModule) {
		if livePruningEnabled {
			bcdata.db.WritePruningEnabled()
		}

		bcdata.EnableMiner()

		signer := types.LatestSignerForChainID(bcdata.bc.Config().ChainID)
		gasPrice := new(big.Int).SetUint64(bcdata.bc.Config().UnitPrice)

		// 1. Transfer (rewardBase -> anon) using a legacy transaction.
		{
			amount := new(big.Int).Mul(big.NewInt(10000), new(big.Int).SetUint64(params.KAIA))
			tx := types.NewTransaction(rewardBase.Nonce, anon.Addr, amount, gasLimit, gasPrice, []byte{})
			rewardBase.Nonce += 1
			err := tx.SignWithKeys(signer, rewardBase.Keys)
			assert.Equal(t, nil, err)
			if err := bcdata.GenABlockWithTransactions(accountMap, []*types.Transaction{tx}, prof); err != nil {
				t.Fatal(err)
			}
		}

		// 2. Transfer (anon -> validator) using a legacy transaction two times.
		{
			txs := []*types.Transaction{}
			for i := range 2 {
				amount := new(big.Int).Mul(common.Big1, new(big.Int).SetUint64(params.Kei))
				nonce := anon.Nonce
				if i == 1 {
					nonce += 100
				}
				tx := types.NewTransaction(nonce, validator.Addr, amount, gasLimit, gasPrice, []byte{})
				err := tx.SignWithKeys(signer, anon.Keys)
				assert.Equal(t, nil, err)
				txs = append(txs, tx)
			}
			// tx0 and tx1 are bundled, and then tx1 fails due to nonceTooHigh, so it is reverted.
			expectedFailTxHashes := []common.Hash{txs[0].Hash(), txs[1].Hash()}
			err := bcdata.GenABlockWithTransactionsWithBundle(accountMap, txs, expectedFailTxHashes, prof, txBundlingModules)

			if livePruningEnabled {
				// State db will be broken because nodes marked as pruning during executing the bundle will be left in state db
				// even though the bundle will be reverted.
				assert.Error(t, err)
			} else if err != nil {
				t.Fatal(err)
			}
		}
	}
```
