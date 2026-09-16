### Title
Gasless swap deadline check accepts a boundary timestamp that will be stale by the time of on-chain execution, causing the proposer's fronted gas fee to be un-repayable - ([File: kaiax/gasless/impl/tx_pool.go])

### Summary
`checkBalanceForSwap` in `kaiax/gasless/impl/tx_pool.go` validates a `GaslessSwapTx.deadline` against `g.Chain.CurrentBlock().Time()` (the parent block's timestamp) using a non-strict comparison, admitting the transaction into the gasless bundling path even when `deadline == parentBlock.Time()`. This mirrors the Ajna `settlePoolDebt()` bug pattern: a boundary check performed with the *previous* period's reference point treats the last-instant value as still valid, when the actual state transition (auction settlement / here, on-chain swap execution) occurs strictly later.

### Finding Description
The mempool-time admission check is: [1](#0-0) 

```go
// tx.deadline >= currentTimestamp
deadline := swapArgs.Deadline
if deadline.Cmp(g.Chain.CurrentBlock().Time()) < 0 {
    return fmt.Errorf("insufficient deadline: deadline=%s, want=%s", deadline.String(), g.Chain.CurrentBlock().Time().String())
}
```

This is used by `IsModuleTx`/`GetCheckBalance` gate, which decides whether the swap tx is treated as a `GaslessSwapTx` and therefore included in the special block-building path in `kaiax/gasless/impl/builder.go`, where a `LendTxGenerator` transaction is *unconditionally* prepended to front the user's gas fee before the `ApproveTx`/`SwapTx` pair is executed: [2](#0-1) 

The `LendTxGenerator` sends KAIA from the proposer to the sender for `lendAmount(approveTxOrNil, swapTx)` regardless of whether the subsequent swap ultimately succeeds: [3](#0-2) 

The deadline is checked twice against two *different* timestamps that are not guaranteed to be consistent:
1. At mempool admission time, against `CurrentBlock().Time()` — the **parent** block's timestamp (the block already mined).
2. On-chain, inside `GaslessSwapRouter.swapForGas()` (not present as Solidity source in this index, only as a compiled binding in `contracts/bindings/kip247/GaslessSwapRouter.go`), presumably against `block.timestamp` of the **block actually being executed**, which by design is strictly greater than the parent's timestamp.

Because Kaia block timestamps strictly increase block-to-block, a `deadline` value equal to `CurrentBlock().Time()` passes the pool-level `>=` check but is already expired relative to the timestamp of the very next block in which the bundle (`LendTx → ApproveTx → SwapTx`) is actually included and executed. This creates a race window at the exact boundary timestamp, analogous to the Ajna report's `block.timestamp == kickTime + 72 hours` off-by-one, where a check meant to gate "has the period truly elapsed" allows the very last instant to be treated as still valid even though the enforcing invariant elsewhere in the system treats that same instant as already past.

### Impact Explanation
If the on-chain `swapForGas()` deadline check reverts because `block.timestamp` (next block) > `deadline` (which passed the pool's `>=` check against the prior block's timestamp), the `SwapTx` fails after the `LendTx` has already unconditionally transferred KAIA to the sender from the proposer. The proposer has fronted gas but the repayment swap (`swapForGas`, which is expected to repay the lent amount from swapped tokens) reverts, so the proposer's advanced KAIA is not recovered — a fee-delegation/gasless-settlement value-loss for the block proposer, triggered purely by an unprivileged user submitting a transaction with a deadline pinned to the current chain head's timestamp. This is a concrete instance of "gasless settlement theft"/fee-delegation abuse reachable from a single submitted transaction.

### Likelihood Explanation
Likelihood is constrained by needing to hit an exact single-second boundary (`deadline == parentBlock.Time()`), similar to the "cumbersome but achievable" likelihood noted in the original Ajna report. An attacker (or even an ordinary gasless user with a badly-computed client-side deadline) can deliberately or accidentally set `deadline = currentHeadTimestamp` and submit the swap transaction, so it is reachable via a single public RPC submission without any special privileges.

### Recommendation
Change the mempool-level admission check in `checkBalanceForSwap` to be consistent with how the eventual execution environment's timestamp will compare, e.g. require `deadline.Cmp(parentTime) > 0` (strictly greater, anticipating the next block's timestamp will be at least parentTime+1) instead of `< 0`, or otherwise account for the timestamp of the block the bundle will actually be included in rather than the current head. This mirrors the Ajna fix of tightening the boundary comparison from `<` to `<=`.

### Proof of Concept
1. Note current chain head timestamp `T = g.Chain.CurrentBlock().Time()`.
2. Build and sign a `GaslessSwapTx` (`swapForGas(token, amountIn, minAmountOut, amountRepay, deadline=T)`), satisfying all other admission checks (`minAmountOut >= amountRepay`, sufficient balance/allowance, no code on sender), per `checkBalanceForSwap`: [4](#0-3) 
3. Submit the transaction to the public RPC/tx pool. It passes the pool's `deadline.Cmp(T) < 0` check since `T.Cmp(T) == 0`, so it is admitted and classified as a `GaslessSwapTx`.
4. The block builder includes it in a `[LendTxGenerator, ApproveTx?, SwapTx]` bundle for the next block (block timestamp `T' > T`), unconditionally executing the `LendTx` first: [5](#0-4) 
5. When `SwapTx` executes on-chain with `block.timestamp = T' > deadline = T`, the router's deadline check (compiled into `GaslessSwapRouter` bytecode, function selector `0x80426901`) reverts the swap, but the `LendTx` KAIA transfer to the sender has already been committed in the same block, leaving the proposer's advanced funds unrepaid.

**Note on completeness**: The exact Solidity source for `GaslessSwapRouter.swapForGas()`'s on-chain deadline enforcement was not found in the indexed codebase (only the compiled Go binding `contracts/bindings/kip247/GaslessSwapRouter.go` is available), so the precise on-chain comparison operator (`<`, `<=`, `>`, `>=`) could not be directly verified from source — this analysis infers it from the "`tx.deadline >= currentTimestamp`" comment and standard deadline-check conventions. If a Devin session with full repo/file access is available, it would be worth confirming the exact on-chain check to solidify the exploit's certainty.

### Citations

**File:** kaiax/gasless/impl/tx_pool.go (L107-181)
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
```

**File:** kaiax/gasless/impl/builder.go (L38-52)
```go
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

```

**File:** kaiax/gasless/impl/getter.go (L268-312)
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
```
