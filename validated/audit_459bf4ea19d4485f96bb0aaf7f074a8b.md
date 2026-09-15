### Title
Gasless module caches `GaslessSwapRouter`/allowed-token whitelist only at `PostInsertBlock`, letting swap bundles be validated against a stale router while `swapTx.to` is immutably fixed at signing time - (File: `kaiax/gasless/impl/getter.go`)

### Summary
This is a direct analog of the reported bug class: a "factory"-like registry (`Registry`/`GaslessSwapRouter` record) can be updated to point at a new trusted address, but a downstream artifact that already captured the *old* address (here, the already-signed `swapTx`, whose `to` field is immutable, plus the node's locally cached `g.swapRouter`) is not guaranteed to be kept in sync at exactly the same block boundary. The original Quest Protocol bug arose because `Quest.rabbitHoleReceiptContract` was immutable while `QuestFactory.rabbitholeReceiptContract` could be updated independently. In Kaia's gasless module, the equivalent split-source-of-truth exists between the on-chain `Registry` record for `GaslessSwapRouterName` and the module's in-memory cache `g.swapRouter`, which is refreshed lazily and only after a full block is inserted.

### Finding Description
`GaslessModule.updateAddresses` is the sole writer of `g.swapRouter` / `g.allowedTokens`, and it is only invoked from `PostInsertBlock`, i.e., strictly after a block has been fully executed and inserted into the chain: [1](#0-0) 

That cached value is then used as the sole authority to decide whether a pending transaction is a legitimate approve/swap transaction, both for mempool admission (`isApproveTx`/`isSwapTx`) and for bundle construction and lending: [2](#0-1) 

The same stale, mutex-protected cache is reused to compute the required `amountIn` and allowance directly against a live `GaslessSwapRouterCaller` bound to `swapRouter` (the cached address), not the address embedded in the registry at the *current* block: [3](#0-2) 

Crucially, the actual transaction that will move value (`swapTx.to`) is fixed by the sender at signing time and is never revalidated against the live Registry state at inclusion/execution time — the module only checks equality against its own cached `g.swapRouter` (`getter.go` lines 97-103, `S1`). If the `GaslessSwapRouterName` record in the KIP-149 `Registry` is changed (`register(name, addr, activation)`), the module's view of "the" trusted router can diverge from the chain's canonical registry state for as long as one block, because the cache is refreshed only in `PostInsertBlock` using that specific block's header/state: [4](#0-3) 

This mirrors the reported root cause exactly: a privileged party (Registry owner) can repoint the canonical "GaslessSwapRouter" address, but a component that already captured the old address (the module's cache, and any already-signed `swapTx`/`approveTx` targeting the old router) continues to be treated as valid by the node building/validating the block, independent of what the Registry currently says.

### Impact Explanation
Since `g.swapRouter` gates whether the proposer will front real KAIA via `GetLendTxGenerator` (the "lend" leg of the gasless bundle) expecting repayment through `swapForGas` on that same router, a window where the cached address is stale means:
- Bundles referencing a since-deprecated or since-replaced router can still be admitted, bundled, and lent against by the proposer, based on trust that has already been revoked on-chain.
- Different Kaia nodes can hold different cached values of `g.swapRouter`/`allowedTokens` depending on exactly when each one last executed `PostInsertBlock`, which can cause a proposer to accept a bundle as valid (`IsExecutable`/`VerifyExecutable`) while another node's block-validation view of the same content would evaluate `isSwapTx` differently once its own cache catches up — a state/decision divergence that is reachable purely from an ordinary user submitting an approve+swap bundle transaction.
- Worst case, the proposer's `LendTx` value is not properly repaid if the swap silently fails or behaves unexpectedly against a router whose current-block trust status has already changed, resulting in fee/value loss to the block proposer analogous to the "admin must manually cover the cost" impact in the original report.

### Likelihood Explanation
Registry updates to `GaslessSwapRouterName` are admin/governance-only actions (as in the original finding, downgraded to Medium because it requires an admin action), but the resulting mismatch is triggered and observed purely through ordinary, unprivileged transaction submission (an approve/swap gasless bundle) — no special access is required by the exploiting party once the router changes. The one-block lag is deterministic given the current `PostInsertBlock`-only refresh design, making the window reliably reproducible around any router rotation event.

### Recommendation
Re-validate `swapTx`/`approveTx` against the live Registry-resolved `GaslessSwapRouter` address at the exact state used for block execution (not a cache populated by the previous block), or explicitly track the record's `activation` block and refuse to admit/bundle/lend against any router whose activation has not yet reached finality at the transaction's execution height — analogous to fetching the trusted contract "directly" rather than trusting a separately-cached copy, as recommended in the original report for `QuestFactory`/`Quest`.

### Proof of Concept
1. Registry owner calls `Registry.register("GaslessSwapRouter", newRouter, activation=N)`.
2. A node's `g.swapRouter` cache still reflects `oldRouter` because `updateAddresses` was last invoked in `PostInsertBlock` for block `N-1` (state prior to the new record's activation visibility) — see `kaiax/gasless/impl/execution.go` lines 26-33 and `getGaslessInfo` in `kaiax/gasless/impl/getter.go` lines 369-389.
3. A user submits an approve/swap bundle whose `to`/`Spender` equals `oldRouter`. `isApproveTx`/`isSwapTx` (`getter.go` lines 79-103) still return `true` because they check against the stale cache, so `checkBalanceForSwap` (`tx_pool.go` lines 107-142) and `ExtractTxBundles`/`IsExecutable` (`builder.go` lines 28-72) admit and bundle it, and the proposer issues a `LendTx` funding the user.
4. Depending on why `oldRouter` was rotated out (e.g., a discovered flaw in `oldRouter`'s repayment accounting), the `swapForGas` call executed on-chain may not properly repay the proposer, while other nodes whose cache already reflects `newRouter` would have rejected the same bundle — producing the value-loss / cross-node divergence described above.

### Citations

**File:** kaiax/gasless/impl/execution.go (L26-33)
```go
func (g *GaslessModule) PostInsertBlock(block *types.Block) error {
	currentState, err := g.Chain.StateAt(block.Header().Root)
	if err != nil {
		return err
	}
	g.setCurrentState(currentState)
	return g.updateAddresses(block.Header())
}
```

**File:** kaiax/gasless/impl/getter.go (L79-103)
```go
func (g *GaslessModule) isApproveTx(args *ApproveArgs) bool {
	g.gaslessInfoMu.RLock()
	defer g.gaslessInfoMu.RUnlock()

	return g.allowedTokens[args.Token] && // A1
		g.swapRouter == args.Spender && // A3
		args.Amount.Cmp(abi.MaxUint256) == 0 // A4
}

// IsSwapTx checks following conditions:
// S1. tx.to is a whitelisted SwapRouter contract.
// S2. tx.data is `swapForGas(token, amountIn, minAmountOut, amountRepay)`.
// S3. token is a whitelisted ERC20 token.
func (g *GaslessModule) IsSwapTx(tx *types.Transaction) bool {
	args, ok := decodeSwapTx(tx, g.signer)
	return ok && g.isSwapTx(args)
}

func (g *GaslessModule) isSwapTx(args *SwapArgs) bool {
	g.gaslessInfoMu.RLock()
	defer g.gaslessInfoMu.RUnlock()

	return g.swapRouter == args.Router && // S1
		g.allowedTokens[args.Token] // S3
}
```

**File:** kaiax/gasless/impl/getter.go (L369-389)
```go
func getGaslessInfo(bc backends.BlockChainForCaller, header *types.Header) (common.Address, []common.Address, error) {
	statedb, err := bc.StateAt(header.Root)
	if err != nil {
		return common.Address{}, nil, err
	}

	// If Registry is not installed, do not query GaslessSwapRouter contract.
	if statedb.GetCode(system.RegistryAddr) == nil || bc.Config().IsRandaoForkBlockParent(header.Number) {
		return common.Address{}, nil, nil
	}

	caller, err := system.NewMultiCallContractCaller(statedb, bc, header)
	if err != nil {
		return common.Address{}, nil, err
	}

	opts := &bind.CallOpts{BlockNumber: header.Number}
	info, err := caller.MultiCallGaslessInfo(opts)

	return info.Gsr, info.Tokens, err
}
```

**File:** kaiax/gasless/impl/tx_pool.go (L107-142)
```go
func (g *GaslessModule) checkBalanceForSwap(swapArgs *SwapArgs, swapNonce uint64) error {
	token := swapArgs.Token
	bc := backends.NewBlockchainContractBackend(g.Chain, nil, nil)

	g.gaslessInfoMu.RLock()
	swapRouter := g.swapRouter
	g.gaslessInfoMu.RUnlock()

	// tx.minAmountOut >= tx.amountRepay
	minAmountOut := swapArgs.MinAmountOut
	amountRepay := swapArgs.AmountRepay
	if minAmountOut.Cmp(amountRepay) < 0 {
		return fmt.Errorf("insufficient minAmountOut: minAmountOut=%s, amountRepay=%s", minAmountOut.String(), amountRepay.String())
	}

	if g.GaslessConfig.ShouldCheckSenderCode() {
		if g.getCurrentHasCode(swapArgs.Sender) {
			return errors.New("sender with code is not allowed")
		}
	}

	if g.GaslessConfig.ShouldCheckSwapAmount() {
		// tx.amountIn >= gsr.getAmountIn(minAmountOut)
		routerContract, err := kip247.NewGaslessSwapRouterCaller(swapRouter, bc)
		if err != nil {
			return err
		}
		// Required token amountIn, given the current exchange rate and the declared minAmountOut.
		requiredAmountIn, err := routerContract.GetAmountIn(nil, token, minAmountOut)
		if err != nil {
			return err
		}
		if swapArgs.AmountIn.Cmp(requiredAmountIn) < 0 {
			return fmt.Errorf("insufficient amountIn: have=%s, want=%s", swapArgs.AmountIn.String(), requiredAmountIn.String())
		}
	}
```
