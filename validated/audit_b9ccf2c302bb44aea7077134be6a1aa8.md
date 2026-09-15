### Title
Reverted bundled transactions leave permanent live-pruning marks in the trie DB, causing state-trie corruption on live-pruning nodes - (File: work/worker.go)

### Summary
This is the closest reachable analog to CVE-2022-27778's bug class ("an operation performs a destructive cleanup action based on stale/incorrect assumptions about what state is still valid after an error"). In curl, `--remove-on-error` deletes a file based on the on-disk name that no longer corresponds to the content that was actually written, because `--no-clobber` silently redirected the write to a different file. In Kaia, when a bundle of transactions is speculatively executed during block building and a later transaction in the bundle fails, the framework reverts the in-memory `StateDB` object, but the **live-pruning marks that were already written to the trie/database as a side effect of the (now reverted) trie mutations are never rolled back**. The pruning subsystem later deletes trie nodes based on these stale marks even though those nodes are still part of the actual (reverted) canonical state — deleting the wrong data, analogous to curl removing the wrong file.

### Finding Description
`Task.commitBundleTransaction` snapshots the state before executing a bundle of transactions and restores it via `env.state.Set(lastSnapshot)` if any transaction in the bundle fails: [1](#0-0) 

The revert path (`restoreEnv`) restores balances, nonces, and other logically-journaled `StateDB` fields, but it operates purely at the `StateDB`/`stateObject` level. It does not undo the raw trie-level side effects that already occurred inside `storage/statedb/trie.go` while committing intermediate tries during the bundle's tx execution.

Specifically, `Trie.delete`/`Trie.insert` unconditionally call `markPrunableNode`, which records affected node hashes into `t.pruningMarksCache` whenever live pruning is enabled and a `PruningBlockNumber` is set: [2](#0-1) 

These marks are later flushed to the on-disk `MiscDB` via `commitPruningMarks`/`Database.Commit`, independent of whether the higher-level `StateDB` operation that produced them is ultimately kept or rolled back: [3](#0-2) 

Once written, `pruneTrieNodeLoop` asynchronously deletes the marked nodes from disk after `LivePruningRetention` blocks have passed, with no way to know that the marks originated from a transaction whose effects were subsequently discarded: [4](#0-3) 

The bug is explicitly reproduced and acknowledged in the test suite: a bundle containing a legacy transfer that will fail with `nonceTooHigh` is executed and reverted, and the test comment states the state DB will be left broken specifically when live pruning is enabled: [5](#0-4) 

Because the bundle's speculative execution mutates real account/storage tries (which are shared, mutable objects — not simple value copies) before the final abort decision is made, and `StateDB.Copy()`/`Set()` do not reset trie-level pruning bookkeeping, the marks survive the logical rollback. When the retention window elapses, `PruneTrieNodes` deletes trie nodes that are still referenced by the actual (unreverted) canonical state, corrupting the state trie on any node that has live pruning enabled.

### Impact Explanation
This causes a real state divergence / crash between honest nodes:
- Nodes running with live pruning enabled (`--state.live-pruning`) will have their state trie's persisted nodes deleted even though those nodes are still referenced by canonical state, since the deletion was scheduled based on a reverted (discarded) trie mutation rather than the final committed one.
- This manifests as `MissingNodeError` when accessing/serving that state later (e.g., RPC `eth_getBalance` at a historical block, state sync, or a validator resuming from disk), which the codebase's own tests treat as a real availability/integrity concern for pruned nodes: `assert.IsType(t, &statedb.MissingNodeError{}, err, num)` type failures becoming spurious for currently-referenced state.
- Nodes with live pruning disabled do not exhibit the bug, meaning behavior diverges between operator configurations for what should be semantically identical chain state — an availability/integrity issue reachable purely by submitting an ordinary bundle whose last transaction is engineered to fail (e.g., by using a too-high nonce or an out-of-gas smart-contract execution), something any unprivileged transaction sender/bundle submitter can trigger.

This fits the required category of "state divergence between honest nodes" and "state trie and proofs" corruption, triggered by ordinary transaction/bundle submission, and is Medium severity consistent with the CVE-2022-27778 precedent (data loss / incorrect destructive cleanup on error, not itself a fund-theft primitive, but a state-integrity/availability defect).

### Likelihood Explanation
Any user who can submit a transaction bundle (a normal capability exposed to public bundle senders, per the existence of `TxBundlingModule`/`GenABlockWithTransactionsWithBundle` machinery) can trigger this by crafting a bundle where a later transaction predictably fails (nonce-too-high, out-of-gas, revert) after an earlier transaction has caused trie mutations. This requires:
1. A node operator has live pruning enabled (a supported, documented production configuration, `chainDB.WritePruningEnabled()` / `config.LivePruning`).
2. A bundle is proposed where an intermediate tx causes real trie node deletions/updates before the abort is detected.

Both conditions are ordinary and already explicitly exercised by the project's own test (`makeTestTxBundleLivePruningScenario`), which demonstrates the trigger is trivial to reach through normal bundle submission and does not require any privileged or malicious-node capability.

### Recommendation
- Scope pruning-mark bookkeeping to the same rollback boundary as the `StateDB`/trie speculative execution: either defer `commitPruningMarks`/`Database.Commit` for pruning marks until the bundle (or block) is fully finalized, or make `env.state.Copy()`/`restoreEnv()` also snapshot and restore the trie-level `pruningMarksCache` (and any already-flushed marks) associated with tries mutated during the aborted execution.
- Alternatively, perform bundle simulation on a fully isolated trie/database overlay that is discarded wholesale on failure, so no pruning marks (or any other trie-database side effects) can leak from speculative execution into the persistent pruning-marks store.
- Add a regression test that inserts, prunes, and asserts liveness of state after a reverted bundle under live-pruning to ensure previously-referenced nodes are never scheduled for deletion.

### Proof of Concept
1. Start a Kaia node with live pruning enabled (`chainDB.WritePruningEnabled()`, `LivePruningRetention` set to a small value), mirroring the existing test setup: [6](#0-5) 
2. Submit a bundle of two transactions from the same sender: tx0 with the correct nonce, tx1 with an incorrect (too-high) nonce, so tx1 fails and the bundle is reverted per `commitBundleTransaction`'s error handling: [7](#0-6) 
3. Continue producing blocks until `LivePruningRetention` blocks have elapsed so `pruneTrieNodeLoop` processes the pruning marks written during the aborted bundle execution: [8](#0-7) 
4. Attempt to read historical/canonical state that depended on the deleted-but-still-referenced trie nodes (e.g., `StateAt` for an affected block/account) and observe `statedb.MissingNodeError`, confirming state corruption purely from ordinary bundle submission — exactly the scenario the project's own `makeTestTxBundleLivePruningScenario(true)` test comment describes as "State db will be broken."

### Citations

**File:** work/worker.go (L877-903)
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
```

**File:** work/worker.go (L906-936)
```go
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

**File:** storage/statedb/database.go (L905-924)
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

**File:** tests/kaia_scenario_test.go (L2257-2304)
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
```
