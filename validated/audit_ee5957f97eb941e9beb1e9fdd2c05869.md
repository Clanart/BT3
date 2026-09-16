### Title
Gasless module's `Disable` flag is defined and can be auto-set, but is never checked by the functions that decide whether to treat a transaction as gasless - (File: kaiax/gasless/impl/tx_pool.go, kaiax/gasless/impl/init.go)

### Summary
This mirrors the reported bug class: a boolean gate meant to block a set of state-changing operations is defined and even auto-toggled by the system, but the functions that actually execute the gated behavior never read/enforce that flag.

### Finding Description
`GaslessConfig.Disable` is a config field that can be set via the `--gasless.disable` CLI flag, and is also force-set at runtime when a consensus node lacks sufficient lender balance: [1](#0-0) 

The module exposes `IsDisabled()` as the accessor for this flag: [2](#0-1) 

However, the functions that actually gate reachable gasless behavior - `IsModuleTx`, `PreAddTx`, `IsReady`/`isReady`, `GetCheckBalance` (and its callees `checkBalanceForApprove`/`checkBalanceForSwap`), and `ExtractTxBundles` - never call `IsDisabled()` or read `g.GaslessConfig.Disable`: [3](#0-2) [4](#0-3) [5](#0-4) 

These functions only check token allow-lists, sender balances/allowances, nonce sequencing, and deadlines via `GaslessConfig.ShouldCheckToken()`, `ShouldCheckSwapAmount()`, `ShouldCheckSenderCode()` — none of which depend on `Disable`: [6](#0-5) 

This is directly analogous to the Superfluid report: `unlockAvailable` was defined and meant to gate `provideLiquidity()`/`withdrawLiquidity()`, but those functions omitted the modifier so the flag had no effect on them. Here, `Disable`/`IsDisabled()` is defined and can be set (including automatically, when a CN's lender balance drops below `GaslessLenderMinBal`), but none of `IsModuleTx`, `IsApproveTx`, `IsSwapTx`, `IsReady`, `PreAddTx`, `GetCheckBalance`, or `ExtractTxBundles` consult it before recognizing/promoting/bundling a gasless transaction pair and generating the block-proposer-funded lend transaction via `GetLendTxGenerator`.

### Impact Explanation
If `IsDisabled()` is not enforced at the points where transactions are pooled, promoted, and bundled, an attacker (any public RPC caller submitting a valid approve+swap gasless transaction pair) could still have their gasless pair recognized, promoted to pending, bundled with a proposer-funded lend transaction, and executed in a block — even though the node/operator intended the module to be disabled (e.g., because the consensus node no longer has sufficient balance to safely lend gas fees, per the very check at `init.go:87-95`). This risks the block proposer lending KAIA it should not be lending under the disabled condition, i.e., fee-delegation/lending abuse and potential value loss for the proposer, which the `Disable` flag exists specifically to prevent.

### Likelihood Explanation
Reachable from a single submitted transaction pair (approve tx + swap tx) via a normal RPC call. The only uncertainty is whether some outer registration layer (module wiring code outside `kaiax/gasless`) checks `Disable`/`IsDisabled()` once at startup before wiring the module's `TxPoolModule`/`TxBundlingModule` callbacks into `blockchain/tx_pool.go` and the block builder. No such call site was found within the searched scope (`kaiax/gasless/**`), and critically, the flag can flip to `true` dynamically only inside `Init()` based on balance at start-up — there is no re-evaluation path shown that would cause a live disable to propagate into already-wired pool/builder logic, since none of those functions reference the flag at all.

### Recommendation
Add an explicit `g.IsDisabled()` check (or equivalent) at the entry points that gate gasless transaction handling — `IsModuleTx`, `PreAddTx`, `isReady`/`IsReady`, `GetCheckBalance`, and `ExtractTxBundles` — so that when the module is disabled (by flag or by insufficient lender balance), gasless approve/swap transactions are neither promoted, checked, nor bundled, consistent with the intent already expressed by `IsDisabled()`.

### Proof of Concept
1. Configure a consensus node acting as gasless lender; let its balance fall below `GaslessLenderMinBal` so `Init()` sets `g.GaslessConfig.Disable = true` (`kaiax/gasless/impl/init.go:87-95`).
2. Submit a valid `approve` + `swapForGas` transaction pair meeting all `IsApproveTx`/`IsSwapTx`/`VerifyExecutable` conditions.
3. Because `IsModuleTx`, `IsReady`, and `ExtractTxBundles` never check `g.IsDisabled()`, the pair is still recognized as a gasless bundle, `GetLendTxGenerator` still produces a proposer-funded lend transaction, and the bundle can still be included in a block — despite the module being marked disabled.

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

**File:** kaiax/gasless/impl/tx_pool.go (L38-72)
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

func (g *GaslessModule) IsModuleTx(tx *types.Transaction) bool {
	if tx == nil {
		return false
	}
	return g.IsApproveTx(tx) || g.IsSwapTx(tx)
}

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

**File:** kaiax/gasless/impl/tx_pool.go (L184-230)
```go
// Check promotion condition and enforce pending pool flow control.
func (g *GaslessModule) IsReady(txs map[uint64]*types.Transaction, next uint64, ready types.Transactions) bool {
	g.knownTxsMu.Lock()
	defer g.knownTxsMu.Unlock()

	tx, ok := txs[next]
	if !ok {
		return false
	}

	if !g.isReady(txs, next, ready) {
		return false
	}

	if g.IsBundleTx(tx) {
		// If prev tx is bundle tx, there's no need to check the knownTxs limit because it has been checked in the previous `IsReady()` execution.
		isPrevTxBundleTx := len(ready) != 0 && g.IsBundleTx(ready[len(ready)-1])
		if isPrevTxBundleTx {
			g.knownTxs.add(tx, TxStatusPending)
			return true
		}

		maxBundleTxsInPending := g.GetMaxBundleTxsInPending()
		if maxBundleTxsInPending != math.MaxUint64 {
			numExecutable := uint(g.knownTxs.numExecutable())

			numSeqTxs := uint(1)
			for i := next + 1; i < next+uint64(len(txs)); i++ {
				if tx, ok := txs[i]; ok && g.IsBundleTx(tx) {
					numSeqTxs++
				} else {
					break
				}
			}

			// false if there is possibility of exceeding max bundle tx num
			if numExecutable+numSeqTxs > maxBundleTxsInPending {
				logger.Trace("Not promoting a tx because of exceeding max bundle tx num", "tx", tx.Hash().String(), "numExecutable", numExecutable, "maxBundleTxsInPending", maxBundleTxsInPending)
				return false
			}
		}

		g.knownTxs.add(tx, TxStatusPending)
	}

	return true
}
```

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

**File:** kaiax/gasless/config.go (L90-100)
```go
func (cfg *GaslessConfig) ShouldCheckToken() bool {
	return cfg.BalanceCheckLevel >= BalanceCheckLevelTokenBalanceAndAllowance
}

func (cfg *GaslessConfig) ShouldCheckSwapAmount() bool {
	return cfg.BalanceCheckLevel >= BalanceCheckLevelSwapAmount
}

func (cfg *GaslessConfig) ShouldCheckSenderCode() bool {
	return cfg.BalanceCheckLevel >= BalanceCheckLevelAll
}
```
