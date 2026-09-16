### Title
Stale in-memory `allowedTokens`/`swapRouter` cache in the gasless module lets a delisted-token swap pass gating checks after the token has been removed on-chain - (File: kaiax/gasless/impl/getter.go, kaiax/gasless/impl/tx_pool.go, kaiax/gasless/impl/execution.go)

### Summary
The `kaiax/gasless` module gates whether a submitted transaction is treated as a "GaslessApproveTx"/"GaslessSwapTx" (and therefore eligible to have the node pre-lend gas fees via `GetLendTxGenerator`) using an in-memory cache (`g.allowedTokens`, `g.swapRouter`) that is refreshed only once per block, in `PostInsertBlock` → `updateAddresses`. Because eligibility gating (`isApproveTx`/`isSwapTx` in `kaiax/gasless/impl/getter.go`) reads this cache rather than the live contract state, a token that has just been removed from the on-chain `GaslessSwapRouter` allowlist (via `removeToken`) can still be treated as allowed by any node whose cache has not yet been refreshed for that block, letting an unprivileged sender get the node to front (lend) gas fees for a swap that the router will reject on execution.

### Finding Description
`GaslessModule.isApproveTx` and `isSwapTx` only consult the cached `g.allowedTokens` map and `g.swapRouter` address, guarded by `g.gaslessInfoMu`: [1](#0-0) 

This cache is populated/refreshed exactly once per block, from execution processing of the previous block's final state, via: [2](#0-1) [3](#0-2) 

An unprivileged sender can submit a `GaslessApproveTx`/`GaslessSwapTx` for a token to the tx pool at any time; `IsModuleTx`/`GetCheckBalance` in `kaiax/gasless/impl/tx_pool.go` use this same stale cache to decide eligibility and to build the fee-lending bundle (`GetLendTxGenerator`), which creates a `TxTypeEthereumDynamicFee` "LendTx" signed with the node's own key that unconditionally transfers `lendAmount(...)` KAIA to the swap sender: [4](#0-3) 

If the token/router allowlist is updated on-chain in block N (e.g., governance removes a token or the `GaslessSwapRouter`/`Registry` address changes), the module's cache for validating and bundling transactions still reflects block N-1's state until `PostInsertBlock` for block N completes. During that window, an attacker can still get a swap on the just-removed token accepted as a "gasless" bundle (`LendTxGenerator` + `SwapTx`), producing a `LendTx` that pays out KAIA from the node's fee-lending flow. If the actual `GaslessSwapRouter` contract call reverts on execution (because the token is no longer recognized on-chain), the preceding `LendTx` — a separate, already-valid transaction — still executes and transfers value, while the `SwapTx` that was supposed to repay it fails, breaking the intended repay invariant and causing an unauthorized value transfer out of the lending flow.

### Impact Explanation
This breaks the core invariant of the gasless/fee-delegation module: `LendTx` value should only be advanced when the paired `SwapTx` is guaranteed to execute and repay via the router. Reliance on a per-block-stale cache for the authorization decision (rather than live/atomic on-chain state at inclusion time) allows an unprivileged transaction sender to obtain fee-delegation lending for a transaction that the canonical, up-to-date chain state would reject, resulting in fund loss to the lending party (the block-producing node) without corresponding repayment — a fee-delegation abuse / unauthorized value movement.

### Likelihood Explanation
Requires precise timing: the attacker's swap for a token must be accepted into the tx pool (or built into a bundle) using the stale cache before/around the same block where the token is delisted on-chain, and the resulting LendTx/SwapTx ordering must let LendTx execute independently of SwapTx's revert. This is a narrow race window (bounded by one block of cache staleness) and depends on mempool/bundle ordering, making exploitation non-trivial but concretely reachable by any transaction sender without special privileges, since gasless swap submission is a public entry point.

### Recommendation
Validate token/router eligibility against the live state at block-inclusion time (or make LendTx conditional/atomic with the SwapTx outcome) rather than relying solely on the per-block cached `allowedTokens`/`swapRouter` snapshot for both mempool admission and bundle generation. At minimum, re-check allowlist membership against current state immediately before generating/including the `LendTxGenerator` transaction in the bundle.

### Proof of Concept
1. Governance/owner calls `GaslessSwapRouter.removeToken(T)` in block N (unprivileged actor doesn't need to trigger this — merely needs to observe/anticipate it, e.g. via mempool inspection of the pending `removeToken` tx).
2. Before the node's `PostInsertBlock` hook for block N updates `g.allowedTokens` (i.e., using the cache still reflecting block N-1's allowlist that still includes T), the attacker submits `GaslessApproveTx`/`GaslessSwapTx` for token T.
3. `isApproveTx`/`isSwapTx` (`kaiax/gasless/impl/getter.go:79-103`) pass because they check the stale cache, so `IsModuleTx` returns true and the bundle including `GetLendTxGenerator` (`kaiax/gasless/impl/getter.go:273-313`) is built and included, transferring `lendAmount` KAIA to the attacker via `LendTx`.
4. On execution, the `SwapTx` call into `GaslessSwapRouter.swapForGas` reverts because token T is no longer allowed on-chain, so the attacker never repays, while the `LendTx` value transfer has already completed.

Note: full confirmation that the bidding/ordering (worker/bundle builder) allows `LendTx` to be included and committed independently of the paired `SwapTx`'s success was not fully verifiable from the indexed files reviewed (the builder/bundle atomicity logic outside `kaiax/gasless` was not available in the index). A Devin session with full repository access should verify `kaiax/builder`'s bundle-commit semantics to confirm atomicity guarantees before treating this as fully proven.

### Citations

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

**File:** kaiax/gasless/impl/getter.go (L268-313)
```go
// MakeLendTx creates a transaction with following properties:
// L1. LendTx.type = 0x7802 (TxTypeEthereumDynamicFee)
// L2. LendTx.from = proposer
// L3. LendTx.to = SwapTx.from
// L4. LendTx.value = LendAmount(approveTxOrNil, swapTx)
func (g *GaslessModule) GetLendTxGenerator(approveTxOrNil, swapTx *types.Transaction) *builder.TxOrGen {
	var src []byte
	if approveTxOrNil != nil {
		src = append(src, approveTxOrNil.Hash().Bytes()...)
	}
	src = append(src, swapTx.Hash().Bytes()...)
	bundleHash := crypto.Keccak256Hash(src)

	gen := func(nonce uint64) (*types.Transaction, error) {
		var (
			chainId = g.InitOpts.ChainConfig.ChainID
			signer  = types.LatestSignerForChainID(chainId)
			key     = g.InitOpts.NodeKey
		)

		to, err := types.Sender(signer, swapTx)
		if err != nil {
			return nil, err
		}

		tx, err := types.NewTransactionWithMap(types.TxTypeEthereumDynamicFee, map[types.TxValueKeyType]interface{}{
			types.TxValueKeyNonce:      nonce,
			types.TxValueKeyTo:         &to,
			types.TxValueKeyAmount:     lendAmount(approveTxOrNil, swapTx),
			types.TxValueKeyData:       common.Hex2Bytes("0x"),
			types.TxValueKeyGasLimit:   params.TxGas,
			types.TxValueKeyGasFeeCap:  swapTx.GasFeeCap(),
			types.TxValueKeyGasTipCap:  swapTx.GasTipCap(),
			types.TxValueKeyAccessList: types.AccessList{},
			types.TxValueKeyChainID:    chainId,
		})
		if err != nil {
			return nil, err
		}

		err = tx.Sign(signer, key)
		return tx, err
	}

	return builder.NewTxOrGenFromGen(gen, bundleHash)
}
```

**File:** kaiax/gasless/impl/getter.go (L315-344)
```go
func (g *GaslessModule) updateAddresses(header *types.Header) error {
	g.gaslessInfoMu.Lock()
	defer g.gaslessInfoMu.Unlock()

	swapRouter, tokens, err := getGaslessInfo(g.Chain, header)
	// proceed even if there is something wrong with multicall contract
	if err != nil {
		g.swapRouter = common.Address{}
		g.allowedTokens = map[common.Address]bool{}
		logger.Warn("there is something wrong with multicall contract", "err", err.Error())
		return nil
	}

	g.swapRouter = swapRouter

	g.allowedTokens = map[common.Address]bool{}
	for _, addr := range tokens {
		// all tokens are allowed if nil
		if g.GaslessConfig.AllowedTokens == nil {
			g.allowedTokens[addr] = true
		}
		for _, allowed := range g.GaslessConfig.AllowedTokens {
			if addr == allowed {
				g.allowedTokens[addr] = true
			}
		}
	}

	return nil
}
```

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
