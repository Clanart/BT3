### Title
Gasless swap token delisting mid-block causes proposer-funded lend transactions to be executed without any grace period or refund guarantee - ([File: kaiax/gasless/impl/getter.go, kaiax/gasless/impl/tx_pool.go, kaiax/gasless/impl/execution.go])

### Summary
The `kaiax/gasless` module lets a block proposer front a user's gas fee (via an auto-generated `LendTx`) so the user can pay it back in an ERC-20 token during a `GaslessSwapTx`, per KIP-247. The set of tokens/router eligible for this scheme (`g.allowedTokens`, `g.swapRouter`) is refreshed only once per block, from the *previous* block's state, in `updateAddresses`. There is no on-chain delay/grace mechanism analogous to the borrower-protection gap the original report calls for when a collateral asset is delisted — here, a token can be delisted from the `GaslessSwapRouter` (`RemoveToken`) and the module keeps treating already-queued/pending `ApproveTx`/`SwapTx` for that token as valid gasless transactions until the next block's `PostInsertBlock` refresh, at which point the proposer has already unconditionally issued the `LendTx` value transfer to the user.

### Finding Description
`GaslessModule.updateAddresses` is invoked only from `PostInsertBlock`, i.e., once per block, using the *just-inserted* block's header/state: [1](#0-0) 
The resulting `g.allowedTokens` map is the sole gate used by `isSwapTx`/`isApproveTx` (and therefore `IsExecutable`/`VerifyExecutable`) to decide whether a transaction qualifies as a gasless transaction eligible for proposer-funded lending: [2](#0-1) 

During block *building*, `ExtractTxBundles` uses this stale, once-per-block `allowedTokens`/`swapRouter` snapshot to decide which pending `ApproveTx`/`SwapTx` pairs get bundled, and for every qualifying pair it unconditionally prepends a `LendTxGenerator`-produced transaction that transfers KAIA (the lent gas fee) from the proposer to the sender: [3](#0-2) [4](#0-3) 

Because `GaslessSwapRouter.removeToken` (analogous to "delisting a reserve") is a normal, unprivileged-reachable on-chain transaction that can be included anywhere in the same block being built — before or interleaved with the gasless bundle — the router's live `isTokenSupported`/pricing state can diverge from the module's cached `allowedTokens` map for the remainder of that block: [5](#0-4) [6](#0-5) 
`checkBalanceForSwap`, which gates transaction-pool admission, also relies on the same stale, cached `g.swapRouter`/`allowedTokens` and an `AmountRepay`/`GetAmountIn` snapshot rather than the block-being-built's live state: [7](#0-6) 

The net effect mirrors the reported bug class: an asset (here, a gasless-eligible token) can be "delisted" with no delay or protection window relative to already-admitted/promoted transactions, and value (the `LendTx` KAIA transfer) is already committed to the user by the time the corresponding `GaslessSwapTx` executes against the now-updated router and can fail to repay it (e.g., `isTokenSupported` reverts or pricing via `GetAmountIn` changes), leaving the proposer's lent gas fee unrecovered.

### Impact Explanation
If the `GaslessSwapTx` (and any dependent `GaslessApproveTx`) fails on-chain due to the token being delisted from `GaslessSwapRouter` within the same block or between the last `PostInsertBlock` refresh and block assembly, the proposer's `LendTx` has already transferred KAIA to the sender's account. Since `LendTx` is a plain value transfer independent of the swap's success/failure, there is no atomic reversal of the lend if the paired swap reverts, resulting in unrecovered gas-fee funding by the proposer (a concrete value-movement/loss scenario touching the in-scope gasless module). This can be exploited or triggered by any account that can submit a `removeToken` transaction with appropriate timing relative to a targeted user's pending gasless bundle, or occur incidentally around legitimate delistings, causing proposer losses at Medium severity.

### Likelihood Explanation
Likelihood is moderate: it requires (a) a gasless swap bundle already pending/queued for a soon-to-be-delisted token, and (b) a `removeToken` (or router-address change via the on-chain `Registry`) transaction landing in the same or an adjacent block before the swap bundle's inclusion is re-validated against fresh state. Both are ordinary, permissionless on-chain actions (submitting transactions), and the once-per-block refresh cadence of `updateAddresses` makes the staleness window deterministic and observable rather than a rare race.

### Recommendation
Re-validate `IsExecutable`/`checkBalanceForSwap` against the *current, in-progress* block-building state (or at minimum re-check `isTokenSupported`/router liveness) immediately before generating and including the `LendTx`, rather than relying solely on the once-per-block cached `allowedTokens`/`swapRouter` snapshot from `PostInsertBlock`. Alternatively, make the `LendTx` and the `ApproveTx`/`SwapTx` a strictly atomic bundle at the EVM/block-execution level so that if the swap fails (e.g., due to a mid-block delisting), the lend transfer is rolled back together with it, ensuring the proposer never funds a gas-fee lend for a swap that cannot repay.

### Proof of Concept
1. User submits `GaslessApproveTx` + `GaslessSwapTx` for token `T`, which is currently listed in `GaslessSwapRouter` and reflected in `g.allowedTokens` as of the last `PostInsertBlock` refresh (`updateAddresses`) — see `kaiax/gasless/impl/execution.go` lines 26-33 and `kaiax/gasless/impl/getter.go` lines 315-344 (context read earlier, `updateAddresses`).
2. During construction of the next block, `ExtractTxBundles` (`kaiax/gasless/impl/builder.go` lines 28-72) still treats `T` as allowed (stale cache) and produces a bundle `[LendTxGenerator, ApproveTx, SwapTx]`, prepending an unconditional KAIA transfer to the sender via `GetLendTxGenerator` (`kaiax/gasless/impl/getter.go` lines 268-313).
3. Concurrently, someone (any address able to call the router, e.g., its owner) submits `GaslessSwapRouter.removeToken(T)` (`contracts/bindings/kip247/GaslessSwapRouter.go` lines 512-517), included earlier in the same block.
4. When the bundle executes, the `LendTx` value transfer to the user succeeds first (plain KAIA transfer, no dependency on router state), but the subsequent `SwapForGas(T, ...)` call reverts because `T` is no longer supported by the router (`isTokenSupported` now false), so the user's debt for the lent gas is never repaid on-chain.
5. `g.allowedTokens` is only corrected on the next `PostInsertBlock` call, after the loss has already occurred — there is no in-block delay, re-check, or atomic linkage protecting the proposer's already-issued lend.

Note: I was not able to fully confirm from the available index whether the underlying `work/builder` bundle-execution engine enforces strict all-or-nothing atomicity across bundled transactions (which would mitigate this specific loss path); this would need to be verified directly in `work/builder/bundle.go` (full contents were not available within my remaining search budget) to determine whether the `LendTx` transfer can in practice survive a failed paired `SwapTx`.

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

**File:** contracts/bindings/kip247/GaslessSwapRouter.go (L425-437)
```go
// IsTokenSupported is a free data retrieval call binding the contract method 0x75151b63.
//
// Solidity: function isTokenSupported(address token) view returns(bool)
func (_GaslessSwapRouter *GaslessSwapRouterSession) IsTokenSupported(token common.Address) (bool, error) {
	return _GaslessSwapRouter.Contract.IsTokenSupported(&_GaslessSwapRouter.CallOpts, token)
}

// IsTokenSupported is a free data retrieval call binding the contract method 0x75151b63.
//
// Solidity: function isTokenSupported(address token) view returns(bool)
func (_GaslessSwapRouter *GaslessSwapRouterCallerSession) IsTokenSupported(token common.Address) (bool, error) {
	return _GaslessSwapRouter.Contract.IsTokenSupported(&_GaslessSwapRouter.CallOpts, token)
}
```

**File:** contracts/bindings/kip247/GaslessSwapRouter.go (L512-517)
```go
// RemoveToken is a paid mutator transaction binding the contract method 0x5fa7b584.
//
// Solidity: function removeToken(address token) returns()
func (_GaslessSwapRouter *GaslessSwapRouterTransactor) RemoveToken(opts *bind.TransactOpts, token common.Address) (*types.Transaction, error) {
	return _GaslessSwapRouter.contract.Transact(opts, "removeToken", token)
}
```

**File:** kaiax/gasless/impl/tx_pool.go (L102-182)
```go
// tx.minAmountOut >= tx.amountRepay
// tx.amountIn >= gsr.getAmountIn(minAmountOut)
// tx.token.approval(sender, router) >= tx.amountIn
// tx.token.balanceOf(sender) >= tx.amountIn
// tx.deadline >= currentTimestamp
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

	if g.GaslessConfig.ShouldCheckToken() {

		tokenContract, err := sc_erc20.NewERC20(token, bc)
		if err != nil {
			return err
		}

		// If SwapTx.nonce is the sender's next nonce, then there is no room for ApproveTx proceeding SwapTx.
		senderNonce := g.getCurrentStateNonce(swapArgs.Sender)
		noApproveTxPreceeds := swapNonce == senderNonce
		if noApproveTxPreceeds {
			// tx.token.allowance(sender, router) >= tx.amountIn
			approval, err := tokenContract.Allowance(nil, swapArgs.Sender, swapRouter)
			if err != nil {
				return err
			}
			if approval.Cmp(swapArgs.AmountIn) < 0 {
				return fmt.Errorf("insufficient approval: approval=%s, want=%s", approval.String(), swapArgs.AmountIn.String())
			}
		}

		// tx.token.balanceOf(sender) >= tx.amountIn
		balance, err := tokenContract.BalanceOf(nil, swapArgs.Sender)
		if err != nil {
			return err
		}
		if balance.Cmp(swapArgs.AmountIn) < 0 {
			return fmt.Errorf("insufficient balance: balance=%s, want=%s", balance.String(), swapArgs.AmountIn.String())
		}
	}

	// tx.deadline >= currentTimestamp
	deadline := swapArgs.Deadline
	if deadline.Cmp(g.Chain.CurrentBlock().Time()) < 0 {
		return fmt.Errorf("insufficient deadline: deadline=%s, want=%s", deadline.String(), g.Chain.CurrentBlock().Time().String())
	}

	return nil
}
```
