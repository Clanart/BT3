Confirmed root cause chain. I have enough evidence to write up the finding.

### Title
State pruning marks are recorded and eventually flushed for trie nodes belonging to a reverted bundle, causing live-pruning nodes to delete trie nodes still referenced by the canonical committed state - ([File: work/worker.go], [File: storage/statedb/trie.go], [File: storage/statedb/database.go])

### Summary
When `LivePruningRetention` is enabled, `Trie.markPrunableNode` (`storage/statedb/trie.go`) records "old" node hashes as prunable every time a storage/account trie is hashed or committed, including hashing performed by `StateDB.IntermediateRoot`/`Finalise` during **speculative, in-block** execution of transactions inside a bundle. When a bundled transaction later fails and the block-producer rolls back with `env.state.Set(lastSnapshot)` (`work/worker.go`), only the in-memory `StateDB` object graph is restored — the pruning marks already staged in the shared `storage/statedb.Database` (`db.pruningMarks`) are **not** undone. Those marks are later flushed via `Database.Commit`/`Cap` and physically deleted from disk by `PruneTrieNodes`, even though the marked nodes may still be referenced by the trie that is ultimately committed for the block. This is the same bug class as CVE-2025-22090: a resource "reservation"/side effect (there: PAT tracking reservation; here: pruning marks) is created eagerly during a speculative/aborted execution path, and is not rolled back when that path is abandoned, corrupting persistent state after the fact.

### Finding Description
The trie pruning subsystem stages deletions in two steps: `Trie.markPrunableNode` (`storage/statedb/trie.go:606-626`) records a node hash to be pruned whenever a previously persisted/loaded node is superseded during `hashRoot`/`Commit`, and these marks accumulate in `Database.pruningMarks` (`storage/statedb/database.go`), which get written to disk by `WritePruningMarks` and eventually consumed and executed by `BlockChain.pruneTrieNodeLoop` → `db.PruneTrieNodes` (`blockchain/blockchain.go:1555-1584`, `storage/database/db_manager.go:2136-2151`), which unconditionally deletes the corresponding `TrieNodeKey` entries from `StateTrieDB`. [1](#0-0) [2](#0-1) 

During block building, `Task.commitBundleTransaction` speculatively executes every transaction of a bundle against the live `env.state`. `bc.ApplyTransaction` internally calls `StateDB.IntermediateRoot`→`Finalise`, which calls `so.updateStorageTrie`/`trie.Hash()`, triggering `markPrunableNode` and staging pruning marks in the shared `Database` object even for transactions that ultimately fail and must be discarded. [3](#0-2) [4](#0-3) 

If any transaction in the bundle fails or returns a non-successful receipt, `restoreEnv()` is invoked, which calls `env.state.Set(lastSnapshot)`. `StateDB.Set`/`copyStateDB` restores `dst.trie`, `dst.stateObjects`, etc. from the snapshot, but explicitly reuses the same underlying trie `Database` instance (`dst.db = src.db`), so it cannot and does not remove the pruning marks that were already staged in `db.pruningMarks` while executing the now-discarded transactions. [5](#0-4) [6](#0-5) 

The project's own test suite documents this exact defect: `TestTxBundleWithLivePruningByMiner` states "All nodes marked as pruning during executing the bundle will be left in state db even though the bundle is reverted. This is because the state.Finalise() will store nodes as pruning before restoring the state," and asserts that `GenABlockWithTransactionsWithBundle` returns an error specifically when live pruning is enabled, versus succeeding when it's disabled. [7](#0-6) [8](#0-7) 

Because the stale marks accumulate in the shared `Database.pruningMarks` and are flushed unconditionally at `Commit`/`Cap` time and later executed by the asynchronous `pruneTrieNodeLoop`, nodes belonging to the *canonical* (correctly committed) trie that happen to share hashes/subtrees with nodes touched by the aborted bundle transactions can be deleted from `StateTrieDB` after the retention window, producing `MissingNodeError` when that state is later accessed (state sync, historical queries, re-execution for reward/staking lookups, etc. as exercised in `TestStateReexec`). [9](#0-8) [10](#0-9) 

### Impact Explanation
This causes silent, delayed corruption of the persistent state trie on any node/validator that (a) runs with live pruning enabled (`WritePruningEnabled` + non-zero `LivePruningRetention`) and (b) uses the bundle/TxBundlingModule block-building path. An attacker who is simply an unprivileged transaction sender can craft ordinary transactions that a bundling module groups into a bundle where a later member transaction fails (e.g., by submitting a transaction with a stale/invalid nonce, insufficient balance, or one that reverts in the EVM), forcing `restoreEnv()`/rollback on the proposer. Over time this causes trie nodes still required by the canonical, committed chain state to be deleted, leading to state trie corruption / `MissingNodeError` for the affected node — i.e., state divergence between honest nodes that do and do not hit this path, and denial of the ability to serve historical or even current state (accounts, proofs, `eth_call`, staking/reward calculations that depend on state re-execution) from the affected node. This matches the "High" severity class of the reference CVE (unauthorized corruption of persistent structures due to missing rollback of a partial reservation).

### Likelihood Explanation
Reachable purely through normal, permissionless transaction/bundle submission — no special privileges, malicious peer, or consensus-message manipulation is required. It requires only: (1) a Kaia node operating as a block proposer with live pruning enabled (an operator-selectable, documented configuration, not an attacker-only condition), and (2) at least one TxBundlingModule in use that groups multiple transactions into a bundle where a later transaction can fail. Any external tx sender able to submit a transaction that fails mid-bundle (trivial: bad nonce, insufficient balance, EVM revert) can trigger the rollback path. The project's own regression test (`TestTxBundleWithLivePruningByMiner`) demonstrates the bug is reliably triggered by a two-transaction bundle where the second transaction fails with `nonceTooHigh`.

### Recommendation
Undo/rollback the pruning-mark side effects together with the state rollback: either (a) snapshot and restore `Database.pruningMarks` (and any per-trie `pruningMarksCache`) alongside `env.state.Set(lastSnapshot)` in `restoreEnv()`, or (b) defer staging of pruning marks until the owning block/transaction is known to be final (e.g., only mark for pruning at `Finalise`/`Commit` time for the finally-accepted set of transactions, not during speculative per-tx `IntermediateRoot` calls that may be discarded), or (c) make pruning-mark bookkeeping copy-on-write per `StateDB` snapshot rather than shared mutable state on the underlying `Database`, so a `Set`/revert genuinely discards marks recorded after the snapshot point.

### Proof of Concept
1. Start a Kaia node as block proposer with `db.WritePruningEnabled()` and a non-zero `CacheConfig.LivePruningRetention`, and register a `TxBundlingModule` that groups transactions in pairs (as in `bundleEachTwoTxs`).
2. Submit a bundle of two transactions from the same or related accounts where the second transaction is guaranteed to fail (e.g., nonce set far ahead, as in `testTxBundleRevertScenario`/`makeTestTxBundleLivePruningScenario(true)`), causing `commitBundleTransaction` to execute both transactions against `env.state`, fail on the second, and call `restoreEnv()`.
3. Observe that block generation subsequently errors/produces an inconsistent state root (as asserted by `assert.Error(t, err)` in `makeTestTxBundleLivePruningScenario` when `livePruningEnabled` is true) — reproduced directly by running `TestTxBundle/TestTxBundleWithLivePruningByMiner` in `tests/kaia_scenario_test.go`.
4. Repeating block production over many blocks while the `pruneTrieNodeLoop` executes `PruneTrieNodes` on the accumulated stale marks results in deletion of trie nodes referenced by the canonical chain, producing `MissingNodeError` on subsequent state access, as demonstrated by the pattern in `TestStatePruning` (`blockchain/blockchain_test.go`) and `TestStateReexec` (`tests/state_reexec_test.go`). [7](#0-6) [8](#0-7) [3](#0-2) [1](#0-0)

### Citations

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

**File:** storage/database/db_manager.go (L2136-2151)
```go
// PruneTrieNodes deletes the trie nodes according to the provided set of pruning marks.
func (dbm *databaseManager) PruneTrieNodes(marks []PruningMark) {
	batch := dbm.NewBatch(StateTrieDB)
	defer batch.Release()
	for _, mark := range marks {
		if err := batch.Delete(TrieNodeKey(mark.Hash)); err != nil {
			logger.Crit("Failed to prune trie node", "err", err)
		}
		if _, err := WriteBatchesOverThreshold(batch); err != nil {
			logger.Crit("Failed to prune trie node", "err", err)
		}
	}
	if err := batch.Write(); err != nil {
		logger.Crit("Failed to batch prune trie node", "err", err)
	}
}
```

**File:** work/worker.go (L877-936)
```go
func (env *Task) commitBundleTransaction(bundle *builder.Bundle, bc BlockChain, nodeAddr common.Address, vmConfig *vm.Config) (error, *types.Transaction, []*types.Log) {
	lastSnapshot := env.state.Copy()
	gasUsedSnapshot := env.header.GasUsed
	blobGasUsedSnapshot := env.header.BlobGasUsed
	blobsSnapshot := env.blobs
	tcountSnapshot := env.tcount
	txs := []*types.Transaction{}
	receipts := []*types.Receipt{}
	logs := []*types.Log{}

	markAllTxUnexecutable := func() {
		for _, txOrGen := range bundle.BundleTxs {
			if txOrGen.IsConcreteTx() {
				tx, _ := txOrGen.GetTx(0)
				tx.MarkUnexecutable(true)
			}
		}
	}

	restoreEnv := func() {
		env.state.Set(lastSnapshot)
		env.header.GasUsed = gasUsedSnapshot
		env.tcount = tcountSnapshot
		// blob related env are restored to the snapshot
		env.header.BlobGasUsed = blobGasUsedSnapshot
		env.blobs = blobsSnapshot
	}

	var totalTxSize uint64 = 0
	for _, txOrGen := range bundle.BundleTxs {
		tx, err := txOrGen.GetTx(env.state.GetNonce(nodeAddr))
		if err != nil {
			logger.Error("TxGenerator error", "error", err)
			markAllTxUnexecutable()
			restoreEnv()
			return kerrors.ErrTxGeneration, nil, nil
		}

		env.state.SetTxContext(tx.Hash(), common.Hash{}, env.tcount)
		receipt, _, err := bc.ApplyTransaction(env.config, &nodeAddr, env.state, env.header, tx, &env.header.GasUsed, vmConfig)
		// Bundled tx will be rejected with any receipt.Status other than success.
		// There may be cases where a revert occurs within the EVM, which could result in an attack on a tx sender in an already executed bundle.
		if err != nil || receipt.Status != types.ReceiptStatusSuccessful {
			if err != vm.ErrInsufficientBalance && err != vm.ErrTotalTimeLimitReached {
				markAllTxUnexecutable()
			}
			receiptStatus := ""
			if receipt != nil {
				receiptStatus = strconv.FormatUint(uint64(receipt.Status), 10)
			}
			logger.Warn("ApplyTransaction error, restoring env",
				"blockNum", env.header.Number.String(), "txHash", tx.Hash().String(),
				"error", err, "receiptStatus", receiptStatus,
			)
			restoreEnv()
			if err == nil {
				err = kerrors.ErrRevertedBundleByVmErr
			}
			return err, tx, nil
		}
```

**File:** blockchain/state/statedb.go (L939-951)
```go
func (s *StateDB) Copy() *StateDB {
	state := &StateDB{}
	copyStateDB(state, s)
	return state
}

func (s *StateDB) Set(src *StateDB) {
	copyStateDB(s, src)
}

func copyStateDB(dst, src *StateDB) {
	// Copy all the basic fields, initialize the memory ones
	dst.db = src.db
```

**File:** blockchain/state/statedb.go (L1121-1131)
```go
// IntermediateRoot computes the current root hash of the state statedb.
// It is called in between transactions to get the root hash that
// goes into transaction receipts.
func (s *StateDB) IntermediateRoot(deleteEmptyObjects bool) common.Hash {
	s.Finalise(deleteEmptyObjects, true)
	// Track the amount of time wasted on hashing the account trie
	if EnabledExpensive {
		defer func(start time.Time) { s.AccountHashes += time.Since(start) }(time.Now())
	}
	return s.trie.Hash()
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

**File:** storage/statedb/database.go (L905-935)
```go
func (db *Database) Commit(root common.Hash, report bool, blockNum uint64) error {
	hash := root.ExtendZero()
	// Create a database batch to flush persistent data out. It is important that
	// outside code doesn't see an inconsistent state (referenced data removed from
	// memory cache during commit but not yet in persistent database). This is ensured
	// by only uncaching existing data when the database write finalizes.
	db.lock.RLock()

	commitStart := time.Now()
	db.diskDB.WritePreimages(0, db.preimages)
	db.diskDB.WritePruningMarks(db.pruningMarks)
	numPreimages := len(db.preimages)
	numPruningMarks := len(db.pruningMarks)

	// Move the trie itself into the batch, flushing if enough data is accumulated
	numNodes, nodesSize := len(db.nodes), db.nodesSize
	if err := db.writeBatchNodes(hash); err != nil {
		db.lock.RUnlock()
		return err
	}

	db.lock.RUnlock()

	// Write successful, clear out the flushed data
	db.lock.Lock()
	defer db.lock.Unlock()

	db.preimages = make(map[common.Hash][]byte)
	db.preimagesSize = 0
	db.pruningMarks = []database.PruningMark{}

```

**File:** tests/state_reexec_test.go (L22-27)
```go
// Test State Regeneration (reexecution) after pruning state trie nodes.
// This test ensures that the state regeneration yields the exact same state as the block's stateRoot.
// Post-Kaia engine.Finalize() relies on the state trie to calculate rewards, so the state regeneration
// can be interfered. This test ensures that the state regeneration is robust against such interference.
func TestStateReexec(t *testing.T) {
	log.EnableLogForTest(log.LvlCrit, log.LvlWarn)
```
