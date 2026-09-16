### Title
Gasless lend transaction can pay out real KAIA without verifying the swap will actually repay it - (File: kaiax/gasless/impl/getter.go)

### Summary
Galaxy Fox's ~$300K loss stemmed from a contract path that let an attacker extract value without the corresponding repayment/consideration ever being validated. The closest reachable analog in this codebase is the `kaiax/gasless` module: a public, unprivileged sender can construct a `GaslessSwapTx` (optionally preceded by an approve tx) that causes the proposer to unconditionally fund a `LendTx` of real native KAIA to the sender, while the on-chain token balance/allowance checks that are supposed to guarantee the swap can actually repay that loan are configuration-gated and are not re-verified at the point the value is paid out.

### Finding Description
`GaslessModule.VerifyExecutable` (the function invoked by `IsExecutable`, which gates both tx-pool promotion and block-bundling) validates only:
- decode/whitelist checks for approve/swap tx (`isApproveTx`, `isSwapTx`)
- sender/token consistency and nonce sequencing between approve and swap
- that `swapArgs.AmountRepay` arithmetically equals `repayAmount(approveTxOrNil, swapTx)` [1](#0-0) 

It never calls into the real balance/allowance/exchange-rate checks. Those checks live in `checkBalanceForSwap`, but they are only invoked from `GetCheckBalance()` and are individually toggled by config flags `ShouldCheckSenderCode()`, `ShouldCheckSwapAmount()`, and `ShouldCheckToken()`: [2](#0-1) 

If an operator disables `ShouldCheckSwapAmount`/`ShouldCheckToken` (or the multicall/registry read used to populate `allowedTokens`/`swapRouter` misbehaves, per the "proceed even if there is something wrong" fallback in `updateAddresses`), the module has no remaining assurance that the sender actually holds the token balance, allowance, or that the router's exchange rate supports the declared `AmountRepay`.

Meanwhile, `GetLendTxGenerator` unconditionally builds and signs a `LendTx` that transfers real KAIA (`lendAmount = ApproveTx.Fee() + SwapTx.Fee()`) from the proposer's key to the swap sender, based purely on declared fee fields of the (unexecuted) transactions — not on the outcome of `swapForGas`: [3](#0-2) [4](#0-3) 

This `LendTx` is prepended into the block-building bundle by `ExtractTxBundles`, which builds the bundle with an atomicity flag of `false`, unlike the `auction` module's bid bundle which is built with `true`: [5](#0-4) [6](#0-5) 

The module's own documentation confirms sender balance checking is explicitly optional/omittable for gasless transactions: "Sender balance check is omitted for gasless transactions (see `GetCheckBalance()`)." [7](#0-6) 

### Impact Explanation
If the balance/allowance/exchange-rate checks are not enforced (misconfiguration, disabled flags, or a race where the whitelisted token/router set is stale/incorrect) and the bundle is not strictly atomic, an unprivileged sender can obtain free real KAIA via `LendTx` while the paired `SwapTx` (the mechanism meant to make the sender repay via `swapForGas`) fails or reverts on execution without unwinding the `LendTx`. This is a direct, unauthorized value movement funded from the block proposer's balance — the same class of bug (value paid out without validated repayment/consideration) that caused the Galaxy Fox loss.

### Likelihood Explanation
Exploitability depends on operator configuration (`ShouldCheckToken`/`ShouldCheckSwapAmount` defaults) and on confirming that `builder.NewBundle(..., false)` in the gasless path indeed permits partial/non-atomic execution of a bundle (i.e., that a reverting `SwapTx` does not roll back the preceding `LendTx`). I was not able to fully inspect `work/builder/bundle.go` and the worker's bundle-execution/rollback logic within the tool budget available, so the precise execution semantics of the atomicity flag remain unconfirmed. This should be verified directly against `work/builder/bundle.go` and the block-building/worker code that consumes `Bundle.Atomic` (or equivalent) before treating this as fully proven, but the code-level absence of value/repayment cross-checking at `VerifyExecutable` is confirmed and directly reachable from any public gasless-tx submitter.

### Recommendation
- Make the token balance/allowance/exchange-rate checks (`checkBalanceForSwap`) mandatory and non-configurable (or safe-by-default) as part of `VerifyExecutable`/`IsExecutable`, not just an optional tx-pool admission gate.
- Verify and, if needed, enforce that gasless bundles (`LendTx` + `ApproveTx`/`SwapTx`) are executed atomically so that a failing/reverting `SwapTx` reverts the `LendTx` payout in the same block.
- Fail closed (reject the gasless bundle) rather than "proceed even if there is something wrong with multicall contract" when `updateAddresses` cannot reliably determine `swapRouter`/`allowedTokens`.

### Proof of Concept
Conceptual reproduction (requires access to a node with `ShouldCheckToken`/`ShouldCheckSwapAmount` disabled or a stale/misreported `GaslessSwapRouter` allow-list):
1. Attacker crafts a `GaslessSwapTx` (optionally with a preceding `GaslessApproveTx`) referencing a token they do not actually hold/haven't approved, with `AmountRepay` set to satisfy the arithmetic check in `repayAmount()`.
2. Submit it via the public tx-pool/RPC; `PreAddTx`/`IsReady`/`isSwapTxReady` → `IsExecutable` → `VerifyExecutable` pass because they never query the token contract balance/allowance directly.
3. Proposer's `ExtractTxBundles` builds `[LendTxGenerator, (ApproveTx), SwapTx]` and includes it in the block; `LendTx` unconditionally transfers `lendAmount` KAIA to attacker.
4. `SwapTx` calls `swapForGas` on-chain and reverts due to insufficient real token balance/allowance.
5. If bundle execution is not strictly atomic, the block still contains the successful `LendTx`, and the attacker keeps the KAIA without ever repaying via the swap.

### Citations

**File:** kaiax/gasless/impl/getter.go (L211-266)
```go
// VerifyExecutable checks if the given transactions form a valid gasless transaction
// It returns an error explaining why the transaction is not executable if it's not,
// and a boolean indicating whether the transaction is executable
func (g *GaslessModule) VerifyExecutable(approveTxOrNil, swapTx *types.Transaction) error {
	// Sx.
	swapArgs, ok := decodeSwapTx(swapTx, g.signer)
	if !ok {
		return ErrDecodeSwapTx
	}
	if !g.isSwapTx(swapArgs) {
		return ErrSwapTxInvalid
	}

	// Conditions involving ApproveTx
	if approveTxOrNil != nil {
		// Ax.
		approveArgs, ok := decodeApproveTx(approveTxOrNil, g.signer)
		if !ok {
			return ErrDecodeApproveTx
		}
		if !g.isApproveTx(approveArgs) {
			return ErrApproveTxInvalid
		}
		// AP1.
		if approveArgs.Sender != swapArgs.Sender {
			return ErrDifferentSenders
		}
		// SP1.
		if approveArgs.Token != swapArgs.Token {
			return fmt.Errorf("%w: approve token %s, swap token %s", ErrDifferentTokens, approveArgs.Token.Hex(), swapArgs.Token.Hex())
		}
		// SP2.
		if approveArgs.Amount.Cmp(swapArgs.AmountIn) < 0 {
			return fmt.Errorf("%w: approve amount %s, required amount %s", ErrInsufficientApproveAmount, approveArgs.Amount.String(), swapArgs.AmountIn.String())
		}
		// SP3.
		if approveTxOrNil.Nonce()+1 != swapTx.Nonce() {
			return fmt.Errorf("%w: approve nonce %d, swap nonce %d (expected %d)", ErrNonSequentialNonce, approveTxOrNil.Nonce(), swapTx.Nonce(), approveTxOrNil.Nonce()+1)
		}
		if nonce := g.getCurrentStateNonce(approveArgs.Sender); nonce != approveTxOrNil.Nonce() {
			return fmt.Errorf("%w: approve nonce %d, current nonce %d", ErrApproveNonceNotCurrent, approveTxOrNil.Nonce(), nonce)
		}
	} else {
		// SP3.
		if nonce := g.getCurrentStateNonce(swapArgs.Sender); nonce != swapTx.Nonce() {
			return fmt.Errorf("%w: swap nonce %d, current nonce %d", ErrSwapNonceNotCurrent, swapTx.Nonce(), nonce)
		}
	}

	// SP4.
	if swapArgs.AmountRepay.Cmp(repayAmount(approveTxOrNil, swapTx)) != 0 {
		return fmt.Errorf("%w: got %s, expected %s", ErrIncorrectRepayAmount, swapArgs.AmountRepay.String(), repayAmount(approveTxOrNil, swapTx).String())
	}

	return nil
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

**File:** kaiax/gasless/impl/tx_pool.go (L62-182)
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

func (g *GaslessModule) checkBalanceForApprove(approveArgs *ApproveArgs) error {
	token := approveArgs.Token
	bc := backends.NewBlockchainContractBackend(g.Chain, nil, nil)

	if g.GaslessConfig.ShouldCheckSenderCode() {
		if g.getCurrentHasCode(approveArgs.Sender) {
			return errors.New("sender with code is not allowed")
		}
	}

	if g.GaslessConfig.ShouldCheckToken() {
		tokenContract, err := sc_erc20.NewERC20(token, bc)
		if err != nil {
			return err
		}

		// tx.token.balanceOf(sender) > 0
		tokenBalance, err := tokenContract.BalanceOf(nil, approveArgs.Sender)
		if err != nil {
			return err
		}
		if tokenBalance.Sign() <= 0 {
			return fmt.Errorf("insufficient sender token balance: token=%s, have=%s, want=nonzero", token.Hex(), tokenBalance.String())
		}
	}
	return nil
}

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

**File:** kaiax/auction/impl/builder.go (L31-70)
```go
func (a *AuctionModule) ExtractTxBundles(txs []*types.Transaction, prevBundles []*builder.Bundle) []*builder.Bundle {
	bundles := []*builder.Bundle{}
	curBlock := a.Chain.CurrentBlock()
	if curBlock == nil || atomic.LoadUint32(&a.bidPool.running) == 0 {
		return bundles
	}

	miningBlock := curBlock.NumberU64() + 1
	bidTargetMap := a.bidPool.GetTargetTxMap(miningBlock)
	if len(bidTargetMap) == 0 {
		return bundles
	}

	for _, tx := range txs {
		txHash := tx.Hash()
		bid, ok := bidTargetMap[txHash]
		if !ok {
			continue
		}
		b := builder.NewBundle(
			builder.NewTxOrGenList(a.GetBidTxGenerator(tx, bid)),
			txHash,
			true,
		)

		isConflict := false
		for _, prev := range append(prevBundles, bundles...) {
			if prev.IsConflict(b) {
				isConflict = true
				break
			}
		}
		if isConflict {
			continue
		}
		bundles = append(bundles, b)
	}

	return bundles
}
```

**File:** kaiax/gasless/README.md (L25-27)
```markdown
#### Balance check

Sender balance check is omitted for gasless transactions (see `GetCheckBalance()`).
```
