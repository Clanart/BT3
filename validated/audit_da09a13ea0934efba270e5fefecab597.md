### Title
Gasless swap admission check validates token balance/allowance against stale on-chain state, allowing multiple sender-issued SwapTx to double-spend the same balance/allowance and leave the block proposer unrepaid - (File: kaiax/gasless/impl/tx_pool.go)

### Summary
`checkBalanceForSwap` in the gasless module validates a `GaslessSwapTx` by reading the sender's *current on-chain* token balance/allowance via `ERC20.BalanceOf`/`Allowance` [1](#0-0) . This mirrors the audited `LienToken.createLien` bug pattern: a limit/sufficiency check is computed from the pre-existing state total, without folding in the effect of the very transaction(s) already accepted from the same sender that are concurrently pending in the pool and will consume the same balance/allowance before this one executes.

### Finding Description
`GetCheckBalance()` overrides the tx-pool's default KAIA balance check for gasless module transactions and instead calls `checkBalanceForApprove`/`checkBalanceForSwap` [2](#0-1) . For a swap tx, `checkBalanceForSwap` checks:
- `tokenContract.Allowance(sender, router) >= AmountIn` (only when no approve tx precedes it in the same nonce sequence) [3](#0-2) 
- `tokenContract.BalanceOf(sender) >= AmountIn` [4](#0-3) 

Both reads are taken from `g.Chain` current state via `backends.NewBlockchainContractBackend`, i.e., the confirmed on-chain balance/allowance — not adjusted for any other GaslessSwapTx from the same sender that is already accepted into the pool (pending/queued) and will consume that same balance/allowance first. Because this check is only run once, at admission time (`PreAddTx`/`validateTx`), each subsequent sequential-nonce SwapTx from the same sender is validated independently against the same un-decremented on-chain balance and allowance, exactly like `LienToken.createLien` checking `maxPotentialDebt` against `totalDebt` without adding the new loan amount for.

This is enabled by the design that IsExecutable/VerifyExecutable's checks (SP1–SP4) validate structural correctness (nonce ordering, token match, repay-amount formula) but never re-derive `AmountIn`/`AmountRepay` sufficiency against a state that accounts for prior sibling gasless transactions in the same pending window [5](#0-4) .

### Impact Explanation
Gasless transactions are funded up front by the block proposer: `GetLendTxGenerator` creates a `LendTx` that pays the sender `LendAmount` (covering ApproveTx.Fee + SwapTx.Fee) *before* the ApproveTx/SwapTx execute [6](#0-5) . The proposer expects to be repaid via the swap's `amountRepay` when the router executes the swap on-chain. If two sequential gasless bundles from the same sender both pass admission because the balance/allowance check for the second was computed against stale (pre-first-swap) state, the second SwapTx will revert on execution (insufficient balance/allowance actually available after the first swap consumes it), while the proposer has already paid out `LendAmount` for it via the prepended `LendTx`. This is a concrete fee-delegation/gasless-lending abuse: value paid by the proposer with no corresponding repayment — matching the "unauthorized value movement / gasless settlement theft" impact class this scan targets.

### Likelihood Explanation
Reachable by any unprivileged token holder acting as a "gasless user" without needing validator/proposer/peer privileges — the report explicitly allows "gasless user" as an in-scope actor. The attacker only needs to submit two or more sequential nonce SwapTx (reusing existing allowance so no ApproveTx is required, i.e. `noApproveTxPreceeds` path) referencing amounts that in aggregate exceed the real balance/allowance, all via a normal RPC submission (`eth_sendRawTransaction`/`kaia_sendRawTransaction`), which is fully within a single unprivileged actor's control.

### Recommendation
When validating `checkBalanceForSwap` (and `checkBalanceForApprove`), the balance/allowance check must account for the cumulative amount already committed by earlier pending/queued transactions from the same sender in the txpool (i.e., subtract the sum of `AmountIn` of all lower-nonce, not-yet-executed GaslessSwapTx of that sender from the on-chain balance/allowance before comparing), analogous to how the LienToken fix folds `params.amount` into `totalDebt` before comparing to `maxPotentialDebt`. Alternatively, restrict the gasless module to at most one outstanding (pending+queued) bundle per sender at a time, or re-validate balance sufficiency for the whole sequential nonce chain as a batch before promoting any of them to ready/pending.

### Proof of Concept
1. Attacker holds exactly `X` tokens with existing allowance `X` granted to the `GaslessSwapRouter`.
2. Attacker signs `SwapTx_0` (nonce N) with `AmountIn = X` and `SwapTx_1` (nonce N+1) with `AmountIn = X`, both omitting a preceding `ApproveTx` (relying on the existing allowance/balance).
3. Both are submitted via RPC. `checkBalanceForSwap` is invoked independently for each: at the moment of admission for `SwapTx_1`, on-chain balance/allowance is still `X` (since `SwapTx_0` has not executed yet), so the check passes for both [1](#0-0) .
4. `ExtractTxBundles` builds two bundles `[LendTx_0, SwapTx_0]` and `[LendTx_1, SwapTx_1]`, each `LendTx` paying the sender the lent gas amount from the proposer's funds up front [7](#0-6) .
5. During block execution, `SwapTx_0` consumes the full `X` balance/allowance; `SwapTx_1` then reverts on-chain due to insufficient balance, so the router never collects `amountRepay` for the proposer, while `LendTx_1` already paid out — a net loss to the proposer with no compensating repayment.

Note: I was not able to fully trace the `GaslessSwapRouter.sol` contract source (only Go bindings were indexed) to confirm the exact revert/repayment-collection order inside `swapForGas`; the impact analysis above is based on the documented flow in `kaiax/gasless/README.md` and `getter.go`/`builder.go`. A Devin session with full repo access would be needed to confirm the exact on-chain repayment mechanics in the router contract.

### Citations

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

**File:** kaiax/gasless/impl/tx_pool.go (L144-172)
```go
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
