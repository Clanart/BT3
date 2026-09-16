### Title
Gasless module's `Disable` flag is not enforced in the tx-pool admission path, allowing gasless bundles to be processed even when the module is disabled - (File: `kaiax/gasless/impl/tx_pool.go`)

### Summary
This is the Kaia analog of the Aave `priceOracleSentinel` bug: an admission-control flag that is supposed to globally gate an operation (`GaslessConfig.Disable`) is checked in only one place (`IsDisabled()` used by the RPC API layer) but is not consulted by the actual tx-pool hooks that decide whether an unprivileged sender's transaction is treated as a gasless bundle transaction.

### Finding Description
`GaslessModule` exposes `IsDisabled()`, which simply returns `g.GaslessConfig.Disable`: [1](#0-0) 

`Disable` is meant to be an authoritative kill-switch for the module — it is set from the CLI flag `gasless.disable`, and is also forced to `true` automatically when a consensus node (lender) does not have sufficient balance to fund gasless lend transactions: [2](#0-1) 

However, the tx-pool entry points that actually decide whether an incoming transaction is admitted/validated as a gasless module transaction — `IsModuleTx` and the balance-check hook returned by `GetCheckBalance` — never check `g.GaslessConfig.Disable`: [3](#0-2) [4](#0-3) 

`PreAddTx`, which promotes/queues gasless bundle transactions, similarly does not reference `Disable`: [5](#0-4) 

The `Disable` check found in the codebase's search results is only wired into the RPC surface (`kaiax/gasless/impl/api.go`) and node backend wiring (`node/cn/backend.go`), not into the module methods (`IsModuleTx`, `GetCheckBalance`, `PreAddTx`, `IsExecutable`/`VerifyExecutable`) that are invoked from the transaction-pool admission and block-building code paths reachable directly by an unprivileged transaction sender.

This mirrors the Aave/Morpho report precisely: a flag/oracle that is supposed to prevent an operation (borrow / gasless bundling) from executing exists, but the enforcement point is not the one actually reachable from the operation's real code path, so the operation can proceed even when it is supposed to be disabled.

### Impact Explanation
If `Disable` becomes `true` at runtime (e.g., a CN auto-disables itself in `init.go` due to insufficient lender balance, or an operator sets `gasless.disable=true` post-launch without restarting/propagating state consistently), a plain user submitting `GaslessApproveTx`/`GaslessSwapTx` pairs will still have them recognized by `IsModuleTx`, pass `GetCheckBalance`, and be queued/promoted by `PreAddTx` as if the module were active. This can lead to the lender-balance safety mechanism being silently bypassed at the tx-pool level, and to gasless bundle transactions still occupying `MaxBundleTxsInPending`/`MaxBundleTxsInQueue` slots and being validated/promoted, contrary to the operator's/protocol's intended "disabled" state. This is state-divergence-relevant: on a node where `Disable` is true due to insufficient CN balance, gasless swap transactions could still be treated as valid bundle candidates while the lend side (fee funding) would eventually fail elsewhere, but the disable safeguard that was meant to prevent this scenario at the earliest admission point is not actually enforced there.

### Likelihood Explanation
Any unprivileged sender can trigger this path simply by submitting a gasless approve/swap transaction pair to a node whose gasless module has `Disable=true` — no special privileges or race conditions are required, since `IsModuleTx`/`GetCheckBalance`/`PreAddTx` are on the default, always-reachable tx-pool ingestion path.

### Recommendation
Add an explicit `g.GaslessConfig.Disable` (or `g.IsDisabled()`) check at the top of `IsModuleTx`, `GetCheckBalance`'s returned closure, and `PreAddTx` (and any bundle-extraction entry point in `builder.go`) so that when the module is disabled, gasless transactions are neither recognized nor admitted/promoted, consistent with how `IsDisabled()` is already surfaced via the RPC API.

### Proof of Concept
1. Configure or trigger `GaslessConfig.Disable = true` (either via `--gasless.disable=true`, or implicitly via the CN lender-balance check in `kaiax/gasless/impl/init.go` lines 87-95).
2. As an ordinary account, submit a valid `GaslessApproveTx` followed by a `GaslessSwapTx` to the node's tx pool.
3. Observe that `IsModuleTx` (`tx_pool.go:55-60`) still returns `true` for both transactions, `GetCheckBalance` (`tx_pool.go:62-72`) still performs its normal validation, and `PreAddTx` (`tx_pool.go:38-53`) still queues them as bundle transactions — none of these consult `GaslessConfig.Disable`, despite the module being configured/flagged as disabled.

### Citations

**File:** kaiax/gasless/impl/init.go (L87-95)
```go
	// Disable module if CN (lender) does not have sufficient balance
	if g.NodeType == common.CONSENSUSNODE {
		nodeAddr := crypto.PubkeyToAddress(opts.NodeKey.PublicKey)
		balance := g.getCurrentStateBalance(nodeAddr)
		if balance.Cmp(GaslessLenderMinBal) < 0 {
			g.GaslessConfig.Disable = true
			logger.Warn("disabling gasless module due to insufficient balance", "node", nodeAddr.Hex(), "balance", balance.String())
		}
	}
```

**File:** kaiax/gasless/impl/init.go (L100-102)
```go
func (g *GaslessModule) IsDisabled() bool {
	return g.GaslessConfig.Disable
}
```

**File:** kaiax/gasless/impl/tx_pool.go (L38-53)
```go
func (g *GaslessModule) PreAddTx(tx *types.Transaction, local bool) error {
	g.knownTxsMu.RLock()
	defer g.knownTxsMu.RUnlock()

	if knownTx, ok := g.knownTxs.get(tx.Hash()); ok && knownTx.elapsedPromotedOrAddedTime() < KnownTxTimeout {
		return ErrUnableToAddKnownBundleTx
	}

	if g.IsBundleTx(tx) {
		if uint(g.knownTxs.numQueue()) >= g.GetMaxBundleTxsInQueue() {
			return ErrBundleTxQueueFull
		}
		g.knownTxs.add(tx, TxStatusQueue)
	}
	return nil
}
```

**File:** kaiax/gasless/impl/tx_pool.go (L55-60)
```go
func (g *GaslessModule) IsModuleTx(tx *types.Transaction) bool {
	if tx == nil {
		return false
	}
	return g.IsApproveTx(tx) || g.IsSwapTx(tx)
}
```

**File:** kaiax/gasless/impl/tx_pool.go (L62-72)
```go
func (g *GaslessModule) GetCheckBalance() func(tx *types.Transaction) error {
	return func(tx *types.Transaction) error {
		if approveArgs, ok := decodeApproveTx(tx, g.signer); ok {
			return g.checkBalanceForApprove(approveArgs)
		}
		if swapArgs, ok := decodeSwapTx(tx, g.signer); ok {
			return g.checkBalanceForSwap(swapArgs, tx.Nonce())
		}
		return errors.New("not a gasless transaction") // should not happen because IsModuleTx is called before GetCheckBalance
	}
}
```
