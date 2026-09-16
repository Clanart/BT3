### Title
Gasless-swap bundle repayment can be lost to sandwiching because the LendTx is not atomically bound to a successful SwapTx - ([File: kaiax/gasless/impl/builder.go], [File: kaiax/gasless/impl/getter.go])

### Summary
Kaia's gasless-transaction feature (KIP-247) builds a non-atomic bundle `[LendTxGenerator, GaslessApproveTx?, GaslessSwapTx]` where the proposer-funded `LendTx` unconditionally transfers KAIA to the gasless sender before the `SwapTx` that is supposed to repay that amount is executed. Because the `SwapTx`'s slippage protection (`minAmountOut`) is fully user-controlled and the AMM swap can be sandwiched by any unprivileged transaction sender who observes the pending bundle, the swap can revert or under-deliver while the preceding `LendTx` payment to the user remains committed, causing the block proposer to lose the lent fee with no repayment. This mirrors the reported bug class: a permissionless flow where a caller-controlled trade/slippage parameter, combined with front-running incentives, causes the protocol-side beneficiary (here, the block proposer instead of a Balancer vault) to receive fewer/no assets than intended.

### Finding Description
The gasless module extracts a 2-3 tx bundle per sender: `LendTx` (proposer pays the user's expected fee up front) followed by an optional `ApproveTx` and the `SwapTx` that is expected to repay the lent amount out of swap proceeds. [1](#0-0) 

The bundle is constructed with `TargetRequired = false`, and `NewBundle`'s conflict logic only checks tx-set/target-hash overlap, not whether later members of the bundle actually succeed on-chain relative to earlier ones. [2](#0-1) [3](#0-2) 

`GetLendTxGenerator` creates and signs a plain `TxTypeEthereumDynamicFee` transaction sending `lendAmount(approveTxOrNil, swapTx)` in KAIA from the proposer's node key directly to the swap sender, with no on-chain hook that ties this transfer's finality to the subsequent `SwapTx` succeeding: [4](#0-3) 

The only repayment safety checks are performed off-chain/at mempool admission time (`VerifyExecutable`, `checkBalanceForSwap`), which validate that `minAmountOut >= amountRepay` and current token balances/allowances/deadline are satisfied at the time of check: [5](#0-4) [6](#0-5) 

These are point-in-time checks against current chain state, not guarantees that hold when the block is actually assembled and the AMM swap executes. Because `minAmountOut`, `amountIn`, and `deadline` are all supplied by the (untrusted) gasless sender in the raw `swapForGas` calldata, and the swap is executed against a public AMM pool (e.g., Uniswap-style router) at block-building time, any other unprivileged transaction sender observing the pending bundle can trade against the same pool ahead of the `SwapTx` (a sandwich/front-run) to push the realized output below `minAmountOut`, causing the `SwapTx` to revert. If the `LendTx` has already been included/committed earlier in the same block (which the non-atomic bundle construction permits), the proposer's lent KAIA transfer stands regardless of whether the swap succeeds.

### Impact Explanation
If the `SwapTx` fails after the `LendTx` has already been applied, the block proposer (who funded the lend from its own key/balance) permanently loses the lent amount with no repayment — a direct, protocol-level (here, proposer-level) value loss caused by a permissionless, caller-parameterized trade whose slippage tolerance is not enforced atomically against the preceding value transfer. This is analogous to the Balancer `reinvestReward` issue: a permissionless function lets an unprivileged party pick trade parameters that determine how much value the "vault"-equivalent party (the proposer) actually recovers, and third parties benefit from front-running/sandwiching at the proposer's expense.

### Likelihood Explanation
Any unprivileged transaction sender who can observe pending gasless bundles (visible in the mempool before block assembly, since bundling happens from `txs []*types.Transaction` seen by the block builder) can submit ordinary swap transactions against the same AMM pool immediately before the target `SwapTx.` This requires no special privilege, node access, or validator collusion — only normal public transaction submission — making the precondition (adverse price movement causing the swap to miss `minAmountOut`) readily triggerable by any searcher with capital to move the pool.

### Recommendation
Ensure the `LendTx`/`ApproveTx`/`SwapTx` bundle is executed atomically as a unit (i.e., if the `SwapTx` reverts, the `LendTx`'s effects must not be finalized in the block), or redesign the repayment mechanism so the proposer's lent amount is only released contingent on the swap's on-chain success (e.g., via an escrow/refund pattern enforced in the `GaslessSwapRouter` contract itself, or by making the bundle `TargetRequired`/atomic at the block-builder level).

### Proof of Concept
1. Attacker observes a pending gasless `[LendTx, SwapTx]` bundle targeting a specific AMM pool/token pair, where `SwapTx.minAmountOut` is only marginally above `amountRepay`. [7](#0-6) 
2. Attacker submits an ordinary swap transaction (regular, non-gasless) against the same pool to move the price such that the gasless `SwapTx`'s realized output would fall below `minAmountOut`.
3. The block builder includes the bundle non-atomically per `ExtractTxBundles`; the `LendTx` (proposer → user) executes and commits. [8](#0-7) 
4. The subsequent `SwapTx` reverts due to insufficient output vs `minAmountOut` (typical AMM router revert behavior), leaving the proposer's lent KAIA unrepaid while the user (or the attacker, indirectly) benefits from the pool trade.

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

**File:** work/builder/bundle.go (L40-48)
```go
func NewBundle(txs []*TxOrGen, targetTxHash common.Hash, targetRequired bool) *Bundle {
	b := &Bundle{
		BundleTxs:      txs,
		TargetTxHash:   targetTxHash,
		TargetRequired: targetRequired,
	}
	b.buildLookup()
	return b
}
```

**File:** kaiax/gasless/impl/getter.go (L142-180)
```go
func decodeSwapTx(tx *types.Transaction, signer types.Signer) (args *SwapArgs, ok bool) {
	to, inputs, ok := decodeFunctionCall(tx, routerSwapFunc)
	if !ok {
		return nil, false
	}
	token, ok := inputs["token"].(common.Address)
	if !ok {
		return nil, false
	}
	amountIn, ok := inputs["amountIn"].(*big.Int)
	if !ok {
		return nil, false
	}
	minAmountOut, ok := inputs["minAmountOut"].(*big.Int)
	if !ok {
		return nil, false
	}
	amountRepay, ok := inputs["amountRepay"].(*big.Int)
	if !ok {
		return nil, false
	}
	deadline, ok := inputs["deadline"].(*big.Int)
	if !ok {
		return nil, false
	}
	from, err := types.Sender(signer, tx)
	if err != nil {
		return nil, false
	}
	return &SwapArgs{
		Sender:       from,
		Router:       to,
		Token:        token,
		AmountIn:     amountIn,
		MinAmountOut: minAmountOut,
		AmountRepay:  amountRepay,
		Deadline:     deadline,
	}, true
}
```

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

**File:** kaiax/gasless/impl/tx_pool.go (L107-182)
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
}
```
