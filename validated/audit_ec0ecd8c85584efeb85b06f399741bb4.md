### Title
Unbounded nested conflict-check loop in gasless bundle extraction can be configured/exploited to cause quadratic-cost block-building DoS - (File: kaiax/gasless/impl/builder.go)

### Summary
`GaslessModule.ExtractTxBundles` builds one `Bundle` per detected gasless swap and, for every new bundle, performs an `O(k)` scan over **all previously extracted bundles** (`append(prevBundles, bundles...)`) to check for conflicts. Because this scan happens once per candidate transaction, the total cost of `ExtractTxBundles` is `O(n·k)`, which degrades to `O(n²)` as the number of valid gasless approve/swap pairs included in the candidate transaction set grows. The size of that set is nominally capped by `GaslessConfig.MaxBundleTxsInPending`/`MaxBundleTxsInQueue` (default 100/200), but the very same config exposes a documented way to disable the cap entirely ("No limit if negative value" → `math.MaxUint64`), and even with the default caps the check is duplicated per-instance without global dedup across the whole `txs` slice the worker passes in. The identical pattern (`for _, tx := range txs { ... for _, prev := range append(prevBundles, bundles...) { if prev.IsConflict(b) ... } }`) also exists in `kaiax/auction/impl/builder.go`.

### Finding Description
`ExtractTxBundles` is invoked by the block-building worker (`work/builder/builder.go`) on the full candidate transaction list for the block being assembled. For each transaction that forms a valid gasless swap (optionally preceded by an approve), the code constructs a new `Bundle` and then, for every one of the bundles collected so far, calls `Bundle.IsConflict`: [1](#0-0) 

`Bundle.IsConflict` itself performs `slices.ContainsFunc` and two `FindIdx` map lookups, so each conflict check is not free, and it is invoked once per already-built bundle for every new candidate — the classic pattern that causes quadratic blow-up as the candidate list grows, analogous to `PoolTemplate.sol`'s unbounded loop over indexes.

The nominal safety valve is the per-account limits enforced in the tx-pool promotion path: [2](#0-1) 

but the underlying configuration explicitly allows removing this bound: [3](#0-2) [4](#0-3) 

An unprivileged actor who controls many funded EOAs can submit many valid `[approve, swap]` transaction pairs against an allowed token/router (satisfying `IsExecutable`/`VerifyExecutable`) to reach the pending pool up to whatever limit the operator has configured. If an operator runs with the "no limit" flag value (a documented, supported configuration) or simply raises the limits, the number of gasless bundles considered by a single `ExtractTxBundles` call is attacker-influenced and effectively unbounded, making the nested conflict-check loop the dominant cost of block assembly.

### Impact Explanation
If the number of colliding/valid gasless bundle candidates grows large (either because an operator disables/raises the per-account caps, or because many distinct senders each contribute a bundle up to the configured caps), the quadratic cost of the conflict-check loop can materially slow down `ExtractTxBundles`, which runs synchronously inside block building. This delays block production for the affected proposer, degrading chain liveness/throughput — the same "denial of service for the desired functionality" impact called out in the original report, here reachable through the gasless (and, similarly, the auction) `TxBundlingModule` used during block assembly, which is one of the explicitly in-scope subsystems.

### Likelihood Explanation
Likelihood depends on operator configuration: with the shipped defaults (100/200 caps), the quadratic factor is small and unlikely to cause a practical DoS by itself. However, because the config explicitly documents and supports an "unlimited" mode, and because an attacker only needs ordinary funded accounts and a valid gasless swap flow (no special privilege) to add candidates, likelihood rises meaningfully on any deployment that does not keep the default caps, or as legitimate gasless-swap usage naturally grows over time.

### Recommendation
- Enforce a hard upper bound on the number of bundles considered inside a single `ExtractTxBundles` call regardless of the `MaxBundleTxsInPending`/`MaxBundleTxsInQueue` settings (i.e., do not honor an "unlimited" configuration for this code path).
- Replace the `O(k)` per-bundle linear conflict scan with an indexed structure (e.g., a set of transaction hashes already included in prior bundles, checked in `O(1)`), removing the quadratic growth entirely.
- Apply the same fix to the structurally identical loop in `kaiax/auction/impl/builder.go`.

### Proof of Concept
Not independently verified by executing code (index-based review only). The reasoning is based on static analysis of: [5](#0-4) [6](#0-5) [4](#0-3) 
An attacker could: (1) fund N EOAs; (2) for each, submit a valid ERC20 `approve` to the configured gasless router followed by a valid gasless `swap` transaction satisfying `VerifyExecutable`; (3) submit these to a node whose operator has configured `gasless.max-bundle-txs-in-pending`/`-in-queue` to a negative ("unlimited") value or a large value; (4) observe increased block-building latency as `ExtractTxBundles`'s nested conflict-check loop scales quadratically with N. Confirming actual wall-clock impact and the precise limits/interactions with `work/builder/builder.go` would require running the node and a benchmark, which was not performed here due to tool limitations (read-only code search only).

### Citations

**File:** kaiax/gasless/impl/builder.go (L28-72)
```go
func (g *GaslessModule) ExtractTxBundles(txs []*types.Transaction, prevBundles []*builder.Bundle) []*builder.Bundle {
	// there are only at most two gasless transactions in pending for a sender
	bundles := []*builder.Bundle{}
	approveTxs := map[common.Address]*types.Transaction{}
	targetTxHash := common.Hash{}
	for _, tx := range txs {
		addr, err := types.Sender(g.signer, tx)
		if err != nil {
			continue
		}
		if g.IsApproveTx(tx) {
			approveTxs[addr] = tx
		} else if g.IsSwapTx(tx) && g.IsExecutable(approveTxs[addr], tx) {
			bundleTxs := builder.NewTxOrGenList(g.GetLendTxGenerator(approveTxs[addr], tx))
			if approveTxs[addr] != nil {
				bundleTxs = append(bundleTxs, builder.NewTxOrGenFromTx(approveTxs[addr]))
			}
			bundleTxs = append(bundleTxs, builder.NewTxOrGenFromTx(tx))

			b := builder.NewBundle(
				bundleTxs,
				targetTxHash,
				false,
			)

			targetTxHash = tx.Hash()

			isConflict := false
			for _, prev := range append(prevBundles, bundles...) {
				if prev.IsConflict(b) {
					isConflict = true
					break
				}
			}
			if isConflict {
				// Gasless transactions will just fail even if they aren't bundled.
				continue
			}
			bundles = append(bundles, b)
		} else {
			targetTxHash = tx.Hash()
		}
	}
	return bundles
}
```

**File:** kaiax/gasless/impl/builder.go (L78-80)
```go
func (g *GaslessModule) GetMaxBundleTxsInPending() uint {
	return g.GaslessConfig.MaxBundleTxsInPending
}
```

**File:** kaiax/gasless/config.go (L41-54)
```go
	MaxBundleTxsInPendingFlag = &cli.IntFlag{
		Name:     "gasless.max-bundle-txs-in-pending",
		Usage:    "max number of gasless bundle txs in pending queue. Default value is 100. No limit if negative value",
		Value:    100,
		Aliases:  []string{"kaiax.module.gasless.max-bundle-txs-in-pending"},
		Category: "KAIAX",
	}
	MaxBundleTxsInQueueFlag = &cli.IntFlag{
		Name:     "gasless.max-bundle-txs-in-queue",
		Usage:    "max number of gasless bundle txs in queue. Default value is 200. No limit if negative value",
		Value:    200,
		Aliases:  []string{"kaiax.module.gasless.max-bundle-txs-in-queue"},
		Category: "KAIAX",
	}
```

**File:** kaiax/gasless/config.go (L116-127)
```go
	// use default value if size is zero
	if size := ctx.Int(MaxBundleTxsInPendingFlag.Name); size > 0 {
		cfg.MaxBundleTxsInPending = uint(size)
	} else {
		cfg.MaxBundleTxsInPending = math.MaxUint64
	}

	if size := ctx.Int(MaxBundleTxsInQueueFlag.Name); size > 0 {
		cfg.MaxBundleTxsInQueue = uint(size)
	} else {
		cfg.MaxBundleTxsInQueue = math.MaxUint64
	}
```

**File:** work/builder/bundle.go (L73-103)
```go
// IsConflict checks if newBundle conflicts with current bundle.
func (b *Bundle) IsConflict(newBundle *Bundle) bool {
	// 1. Check for same target tx hash and both are required
	// If both are required, it discards the new bundle.
	if b.TargetTxHash == newBundle.TargetTxHash && b.TargetRequired && newBundle.TargetRequired {
		return true
	}

	// 2-1. Empty bundleTxs does not conflict with other transactions
	if len(b.BundleTxs) == 0 {
		return false
	}

	// 2-2. Check for overlapping txs
	if slices.ContainsFunc(newBundle.BundleTxs, b.Has) {
		return true
	}

	// 2-3. Check for TargetTxHash breaking current bundle.
	// If newBundle.TargetTxHash is equal to the last tx of current bundle, it is NOT a conflict.
	// Check both direction to guarantee symmetry.
	// e.g.) b.txs = [0x1, 0x2] and newBundle's TargetTxHash is 0x2.
	if idx := b.FindIdx(newBundle.TargetTxHash); idx != -1 && idx != len(b.BundleTxs)-1 {
		return true
	}
	if idx := newBundle.FindIdx(b.TargetTxHash); idx != -1 && idx != len(newBundle.BundleTxs)-1 {
		return true
	}

	return false
}
```
