### Title
Single malicious bundle-triggering transaction can cause the block builder to discard **all** transactions from a block - `work/builder/builder.go: ExtractBundlesAndIncorporate()`

### Summary
`ExtractBundlesAndIncorporate()` is the block-assembly routine that merges gasless/auction bundle transactions into the candidate transaction list for the block being built. If `IncorporateBundleTx()` fails for *any* bundle, the function does not gracefully drop only the offending bundle — it discards the entire batch of transactions and returns an empty list, exactly like the reported bug where one uncooperative/blacklisted participant in a loop caused the entire batch operation (`netAtPrice()`) to revert for everyone.

### Finding Description
`ExtractBundlesAndIncorporate` builds `bundles` from all registered `TxBundlingModule`s (currently Gasless and Auction, reachable by any unprivileged sender submitting `GaslessApproveTx`/`GaslessSwapTx` or an auction bid tx) and then calls `IncorporateBundleTx(arrayTxs, bundles)`: [1](#0-0) 

`IncorporateBundleTx` iterates over each bundle and calls `incorporate()`, returning an error the moment a single bundle cannot be placed: [2](#0-1) 

`incorporate()` fails with `ErrFailedToIncorporateBundle` whenever a bundle's `TargetTxHash` cannot be located in the current transaction list (`targetFound` stays `false`). This is analogous to `CrabNetting.netAtPrice()`, where a single participant's transfer reverting inside a loop halts the whole batch operation for all other legitimate participants — here, a single bundle whose target hash cannot be resolved (e.g., a bundle created by the Gasless or Auction `TxBundlingModule` while pending-pool state differs slightly from `arrayTxs`, or via `coordinateTargetTxHash` producing a hash that no longer matches after reordering) aborts processing of the *entire* candidate transaction set.

Critically, on error the caller receives `flattenedTxs`, which is `[]*TxOrGen{}` — never populated on the non-nil-`txBundlingModules` path — so **all** of `arrayTxs` (every pending transaction from every sender, bundled or not) is dropped from the round of block assembly, not just the malicious/broken bundle: [1](#0-0) 

### Impact Explanation
Because Gasless (`kaiax/gasless`) and Auction (`kaiax/auction`) bundle creation is driven entirely by transactions from unprivileged senders (a gasless swap sender or an auction bidder), a single crafted or mistimed bundle-eligible transaction can trigger `ErrFailedToIncorporateBundle` and cause the proposer's block-assembly pass to silently discard every transaction it was about to include for that pass — an availability/DoS defect at the block-assembly layer, matching the reachable analog classes explicitly in scope ("block assembly", "gasless and auction modules"). This can suppress otherwise-valid, fee-paying user transactions network-wide for affected rounds, degrading throughput and potentially being weaponized repeatedly by a single low-cost attacker to keep emptying block-building passes.

### Likelihood Explanation
Reachable purely through normal transaction submission (gasless approve/swap tx pair or an auction bid tx) — no privileged role, node compromise, or consensus-message manipulation is required. The trigger condition (bundle target hash mismatch during `incorporate`) depends on interaction between `ExtractTxBundles` from the Gasless/Auction modules and the underlying pending-tx ordering/`coordinateTargetTxHash` logic, so it is not guaranteed on every call, but the code path and failure mode are concretely present and unguarded against silently by every proposer running this code.

### Recommendation
On `IncorporateBundleTx` failure, do not drop the whole `arrayTxs` set. Instead, identify and exclude only the specific bundle(s) that failed to incorporate (mirroring the `PopTxs`/dependency-removal logic already used elsewhere in this file) and retry incorporation with the remaining bundles and the full, un-bundled transaction list — analogous to adding a “skip the bad participant” fallback instead of aborting the whole batch, as recommended for the original `netAtPrice()` issue.

### Proof of Concept
1. Submit a `GaslessApproveTx`/`GaslessSwapTx` pair (or an auction bid tx) such that the Gasless/Auction `TxBundlingModule.ExtractTxBundles` produces a bundle whose `TargetTxHash` refers to a transaction that will not be present (or already consumed by an overlapping bundle) at `IncorporateBundleTx` time — e.g., by racing the mempool with a second bundle-eligible transaction sharing the same target, or exploiting a state where `coordinateTargetTxHash` reassigns a stale hash.
2. Verify `incorporate()` at [3](#0-2)  returns `ErrFailedToIncorporateBundle` because `targetFound` never becomes true.
3. Observe `ExtractBundlesAndIncorporate` returns the empty `flattenedTxs` slice instead of the populated `arrayTxs`, meaning the block-building code receives zero transactions to include for that pass, regardless of how many honest transactions were pending.

### Citations

**File:** work/builder/builder.go (L69-116)
```go
// IncorporateBundleTx incorporates bundle transactions into the transaction list.
// Caller must ensure that there is no conflict between bundles.
func IncorporateBundleTx(txs []*types.Transaction, bundles []*Bundle) ([]*TxOrGen, error) {
	ret := make([]*TxOrGen, len(txs))
	for i, tx := range txs {
		ret[i] = NewTxOrGenFromTx(tx)
	}

	for _, bundle := range bundles {
		var err error
		ret, err = incorporate(ret, bundle)
		if err != nil {
			return nil, err
		}
	}
	return ret, nil
}

// incorporate assumes that `txs` does not contain any bundle transactions.
func incorporate(txs []*TxOrGen, bundle *Bundle) ([]*TxOrGen, error) {
	ret := make([]*TxOrGen, 0, len(txs)+len(bundle.BundleTxs))
	targetFound := false

	// 1. place bundle at the beginning
	if bundle.TargetTxHash == (common.Hash{}) {
		ret = append(ret, bundle.BundleTxs...)
		targetFound = true
	}

	// 2. place bundle after TargetTxHash
	for _, txOrGen := range txs {
		// if tx-in-bundle, the tx will be appended when target is found.
		if bundle.Has(txOrGen) {
			continue
		}
		ret = append(ret, txOrGen)
		if txOrGen.Id == bundle.TargetTxHash {
			targetFound = true
			ret = append(ret, bundle.BundleTxs...)
		}
	}

	if !targetFound {
		return nil, ErrFailedToIncorporateBundle
	}

	return ret, nil
}
```

**File:** work/builder/builder.go (L270-309)
```go
func ExtractBundlesAndIncorporate(arrayTxs []*types.Transaction, txBundlingModules []TxBundlingModule) ([]*TxOrGen, []*Bundle) {
	// Detect bundles and add them to bundles
	bundles := []*Bundle{}
	flattenedTxs := []*TxOrGen{}
	if txBundlingModules == nil {
		for _, tx := range arrayTxs {
			flattenedTxs = append(flattenedTxs, NewTxOrGenFromTx(tx))
		}
		return flattenedTxs, nil
	}

	for _, txBundlingModule := range txBundlingModules {
		newBundles := txBundlingModule.ExtractTxBundles(arrayTxs, bundles)
		for _, newBundle := range newBundles {
			isConflict := false
			// Check for conflicts with all previous bundles
			for _, prevBundle := range bundles {
				isConflict = prevBundle.IsConflict(newBundle)
				if isConflict {
					break
				}
			}
			// Not allowing empty bundles
			if !isConflict && len(newBundle.BundleTxs) > 0 {
				bundles = append(bundles, newBundle)
			}
		}
	}

	// Coordinate target tx hash of bundles. It assumes the Gasless and Auction modules only currently.
	// This reordering does not break the execution result.
	// For example, if bundle reordering breaks the nonce ordering, the execution result will be different.
	bundles = coordinateTargetTxHash(bundles)

	incorporatedTxs, err := IncorporateBundleTx(arrayTxs, bundles)
	if err != nil {
		return flattenedTxs, nil
	}

	return incorporatedTxs, bundles
```
