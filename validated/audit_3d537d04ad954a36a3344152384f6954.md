## Title
Gasless Swap Balance/Allowance Checks Use Stale State, Allowing Repeated Bundles to Drain Unrepaid Lent Gas from the Block Proposer - (File: `kaiax/gasless/impl/tx_pool.go`)

### Summary
The `kaiax/gasless` module admits `GaslessApproveTx`/`GaslessSwapTx` pairs into the tx pool by checking a sender's on-chain token `balanceOf`/`allowance` against the *chain head state at admission time*, without any accounting for consumption by other gasless bundles from the same sender that will execute earlier in the same block. Because a sender can build multiple sequential nonce pairs of `(ApproveTx, SwapTx)`, each individually validated against the same stale balance/allowance snapshot, more bundles can be admitted and built than the sender's real token balance/allowance supports. Each bundle unconditionally prepends a `LendTxGenerator` transaction that pays out proposer-funded KAIA to the sender before the swap even runs. When a later bundle's `SwapTx` reverts on-chain (insufficient balance/allowance already consumed by the earlier bundle), the lent KAIA is never repaid, resulting in direct value loss to the block proposer. This is structurally identical to the reported `QVSimpleStrategy` bug: a "credits remaining" check (`_hasVoiceCreditsLeft` / `checkBalanceForSwap`) is evaluated repeatedly against a value that is never decremented to reflect prior consumption, letting the actor exceed the true limit multiple times.

### Finding Description
`GaslessModule.checkBalanceForSwap` validates a `GaslessSwapTx` at pool-admission time by reading the *current, already-finalized* chain state: [1](#0-0) 

Specifically, the allowance check and balance check query `tokenContract.Allowance(...)` and `tokenContract.BalanceOf(...)` on the state as of the last block, not as it would be after previously-queued gasless bundles from the same sender execute earlier in the same upcoming block: [2](#0-1) 

Nothing in the module enforces a hard cap of one pending gasless bundle per sender. `ExtractTxBundles` merely *assumes* "there are only at most two gasless transactions in pending for a sender" as a comment, but does not enforce it — it builds a bundle for every `IsExecutable(approveTx, swapTx)` pair it encounters in the pending list: [3](#0-2) 

Since Kaia's tx pool promotes transactions strictly by consecutive nonce, a single sender can submit `Approve(n), Swap(n+1), Approve(n+2), Swap(n+3), ...` — a series of independent, fully valid-looking gasless bundles, each of which independently satisfies `checkBalanceForSwap` at admission because that check is evaluated against the same static on-chain balance/allowance (it does not simulate the effect of the earlier, not-yet-executed bundles).

Each bundle unconditionally begins with a proposer-funded `LendTxGenerator` transaction — a plain, unconditional KAIA transfer from the proposer's node key to the sender — that funds the sender's gas *before* the swap executes: [4](#0-3) [5](#0-4) 

Per the module's documented design, repayment of the lent amount only happens as a side effect of the `GaslessSwapTx` succeeding on-chain: [6](#0-5) 

If the sender's real token balance/allowance is only sufficient for a single swap, then only the first bundle's `SwapTx` in the block will actually succeed and repay the lender; every subsequent bundle's `SwapTx` will revert on execution because the balance/allowance was already consumed — yet the corresponding `LendTxGenerator` transaction preceding it still unconditionally transfers KAIA to the sender, since it is a plain value transfer independent of the swap's outcome.

### Impact Explanation
This allows an unprivileged transaction sender (an ordinary gasless user, requiring no special privileges — merely the ability to submit multiple transactions with sequential nonces) to receive multiple proposer-funded "loans" of KAIA gas while only ever repaying (at most) one of them. Each unrepaid `LendTxGenerator` payout is a direct, unrecoverable transfer of value out of the block proposer's account with no compensating repayment — concrete unauthorized value movement / fee-delegation-style theft from the gasless-lending mechanism, scalable to the number of sequential bundles the sender can afford to have promoted into a single block (bounded only by `MaxBundleTxsInPending`/`MaxBundleTxsInQueue`, which are configured in the hundreds by default).

### Likelihood Explanation
High reachability: the attack requires only submitting ordinary, properly-signed `GaslessApproveTx`/`GaslessSwapTx` pairs at sequential nonces from a single EOA — something any public RPC caller can do without special permissions. It does not require compromising a validator, peer, or node key. The only constraint is that the sender's real (single-use) token balance/allowance be sufficient for at least one swap, which is trivially achievable, and that multiple such bundles get included/ordered into the same block by the proposer, which the `ExtractTxBundles`/pending-promotion logic does not prevent.

### Recommendation
- Track and decrement an in-flight/simulated balance and allowance for gasless senders across all bundles queued for the same block (analogous to properly updating `allocator.voiceCredits` in the original report), instead of re-reading the static chain-head state for every candidate `SwapTx`.
- Enforce, and not merely assume in a comment, that at most one gasless bundle per sender can be pending/built per block (the `ExtractTxBundles` "at most two gasless transactions in pending for a sender" invariant should be actively checked and rejected/dropped otherwise).
- Consider linking `LendTxGenerator` payout success to the outcome of the paired `SwapTx` (e.g., only include the lend transaction if repayment can be guaranteed, or reclaim/skip subsequent bundles once an earlier bundle from the same sender in the same block has been observed to consume the relevant balance/allowance).

### Proof of Concept
1. User approves the `GaslessSwapRouter` for exactly enough tokens for one swap and holds exactly enough token balance for one swap (`AmountIn = X`).
2. User submits, at sequential nonces `n, n+1, n+2, n+3`, two gasless bundles for the same token: `Approve(n, amount=X)`, `Swap(n+1, amountIn=X)`, `Approve(n+2, amount=X)`, `Swap(n+3, amountIn=X)` — note the second `Approve` is submitted before the first `Swap` executes, so it is accepted since it only needs to be signed/valid, not executed yet.
3. Tx-pool admission (`checkBalanceForSwap`, `kaiax/gasless/impl/tx_pool.go:107-182`) validates each `Swap` independently against the *same* on-chain balance/allowance of `X`, both pass.
4. `ExtractTxBundles` (`kaiax/gasless/impl/builder.go:28-72`) builds two bundles: `[Lend1, Approve(n), Swap(n+1)]` and `[Lend2, Approve(n+2), Swap(n+3)]`.
5. During block execution, `Swap(n+1)` succeeds and repays `Lend1`; but by then the token balance/allowance is exhausted, so `Swap(n+3)` reverts. `Lend2`, however, is a simple unconditional value transfer and still succeeds, transferring KAIA from the proposer's node-key balance to the sender with no repayment.
6. Repeating this pattern with more sequential `Approve`/`Swap` pairs multiplies the unrepaid loss to the proposer.

### Citations

**File:** kaiax/gasless/impl/tx_pool.go (L107-172)
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

**File:** kaiax/gasless/impl/getter.go (L346-367)
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

func repayAmount(approveTxOrNil, swapTx *types.Transaction) *big.Int {
	// R1 = LendTx.Fee() = SwapTx.GasPrice() * TxGas
	r1 := new(big.Int).Mul(swapTx.GasPrice(), new(big.Int).SetUint64(params.TxGas))

	// RepayAmount = R1 + R2 + R3
	return new(big.Int).Add(r1, lendAmount(approveTxOrNil, swapTx))
}
```

**File:** kaiax/gasless/README.md (L7-11)
```markdown
Gasless transaction (GaslessTx) consists of two types: gasless approve transaction (GaslessApproveTX), and gasless swap transaction (GaslessSwapTx).

Note that gasless transaction does not mean the gas prices of gasless transactions are zero, but it means that proposer will lend the user with gas fee and user will pay back during gasless swap.

The gas fee of gasless transaction's is funded by block proposer (i.e., lend transaction generated by `GetLendTxGenerator`), and user repays the lent amount during gasless swap.
```
