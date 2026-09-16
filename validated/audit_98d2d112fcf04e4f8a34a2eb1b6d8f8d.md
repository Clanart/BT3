### Title
Gasless bundle lend transaction is not atomic with the swap transaction, allowing loss of lent KAIA when the swap reverts due to stale precondition checks - (File: kaiax/gasless/impl/getter.go, kaiax/gasless/impl/tx_pool.go, kaiax/gasless/impl/builder.go)

### Summary
Kaia's gasless module (`kaiax/gasless`) lends a user native KAIA gas fee up-front via an unconditional `LendTx` and expects the `GaslessSwapTx` to repay it. The lend and swap are two *separate*, non-atomic transactions placed in a bundle. All swap preconditions (token balance, allowance, exchange rate) are validated once, at tx-pool admission time, against a state snapshot that can become stale by the time the bundle actually executes on-chain, exactly like the Compound V2 `closePosition()` bug where the close logic used a state snapshot from position-opening time without accounting for a later liquidation.

### Finding Description
`GaslessModule.checkBalanceForSwap` (called from `GetCheckBalance()`) validates the swap's preconditions — exchange rate (`GetAmountIn`), ERC20 allowance, and ERC20 balance — against the *current* state at the moment the transaction is admitted to the pending pool: [1](#0-0) 

These checks are also individually gated by config flags (`ShouldCheckSwapAmount`, `ShouldCheckToken`) and are not re-verified atomically with execution: [2](#0-1) 

At block-building time, `ExtractTxBundles` unconditionally prepends a `LendTxGenerator` transaction ahead of the `ApproveTx`/`SwapTx` pair, based only on `IsExecutable`/`VerifyExecutable`, which validate transaction *metadata* (nonce ordering, declared amounts, fee arithmetic) — not the live on-chain token balance/allowance/price at inclusion time: [3](#0-2) 

The generated `LendTx` unconditionally transfers `lendAmount(approveTxOrNil, swapTx)` (native KAIA) from the proposer to the sender, computed purely from the transactions' declared `Fee()` values, not contingent on the swap actually succeeding: [4](#0-3) [5](#0-4) 

Because `LendTx`, `ApproveTx`, and `SwapTx` are three independent transactions incorporated sequentially into the block (not an atomic multicall), a state change that occurs between the pool's admission-time balance check and the block's actual execution of the bundle — such as another transaction in the same block draining the sender's token balance/allowance, or moving the AMM price so `GetAmountIn` no longer matches `AmountIn` — causes the `SwapTx` to revert on-chain while the preceding `LendTx` has already unconditionally transferred KAIA to the sender and is not rolled back. This is the same root cause pattern as the reported bug: the settlement logic (repayment via swap) is only valid for a state snapshot taken earlier, and does not account for intervening state changes, leaving the "lend" side irrecoverably executed while the "repay" side fails.

### Impact Explanation
If the `SwapTx` reverts after `LendTx` has already paid the sender, the proposer's lent KAIA is not repaid: this is a direct fee-delegation/gasless settlement value-movement bug in a system contract-adjacent module reachable by any unprivileged sender submitting an approve+swap tx pair. Repeated or crafted exploitation (e.g., sending a second transaction from the same account within the same block that consumes the token balance/allowance right before the swap executes, or interacting with the swap-router pool to move price) allows an attacker to receive lent KAIA without repaying it, directly draining value from the proposer/protocol.

### Likelihood Explanation
The precondition checks (`checkBalanceForSwap`) are performed once at pool admission and are individually optional per `GaslessConfig`; even when enabled, they use a state snapshot that is not guaranteed to still hold by the time of block inclusion, since other transactions (including the sender's own subsequent transactions, or third-party interactions with the same token/pool) can execute first within the same block and invalidate the balance/allowance/price assumptions. Any public RPC caller who can submit a normal `Approve`+`Swap` transaction pair can attempt this, requiring no special privileges.

### Recommendation
Make the lend and repay atomic, or verify the swap's preconditions against the state immediately preceding execution (post prior in-block transactions) rather than relying solely on pool-admission-time checks. Consider either: (1) re-validating `checkBalanceForSwap`-equivalent conditions right before including the bundle in the block/at execution time using the exact state the bundle will execute against, or (2) structuring the lend/swap so that if the swap fails, the lend is not persisted (e.g., using a wrapping contract call that can revert both atomically), and (3) making `ShouldCheckSwapAmount`/`ShouldCheckToken` mandatory rather than optionally disabled via config.

### Proof of Concept
1. Attacker submits `ApproveTx` (approve router for `AmountIn`) then `GaslessSwapTx` for token `T`, satisfying `checkBalanceForSwap` at admission time (balance/allowance sufficient).
2. Before the bundle is incorporated into a block, attacker submits (or arranges) another transaction from the same account (or a related account interacting with token `T`) that reduces the sender's balance/allowance of `T` below `AmountIn`, or shifts the swap-router's exchange rate so `GetAmountIn(minAmountOut) > AmountIn`.
3. The block builder's `ExtractTxBundles` (`kaiax/gasless/impl/builder.go`) still includes the bundle `[LendTx, ApproveTx, SwapTx]` because `IsExecutable`/`VerifyExecutable` only check transaction metadata, not live balances.
4. `LendTx` executes first and unconditionally transfers `lendAmount` KAIA from proposer to sender.
5. `SwapTx` reverts on execution due to insufficient balance/allowance/slippage, so the repayment mechanism inside the swap router never runs.
6. The proposer's lent KAIA is not recovered, while the attacker's `ApproveTx`/prior state manipulation stands — mirroring the reported bug where the closing/repayment logic did not account for an intervening state change and left recoverable value stranded/misappropriated.

### Citations

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

**File:** kaiax/gasless/impl/builder.go (L28-71)
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

**File:** kaiax/gasless/impl/getter.go (L346-359)
```go
func lendAmount(approveTxOrNil, swapTx *types.Transaction) *big.Int {
	r := new(big.Int)

	// R2 = ApproveTx.Fee() if exists
	if approveTxOrNil != nil {
		r.Add(r, approveTxOrNil.Fee())
	}

	// R3 = SwapTx.Fee()
	r.Add(r, swapTx.Fee())

	// LendAmount = R2 + R3
	return r
}
```
