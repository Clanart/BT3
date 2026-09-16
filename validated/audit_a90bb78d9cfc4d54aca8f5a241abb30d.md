## Title
Gasless bundle lend-and-repay is not atomically enforced, allowing the sponsored gas loan to be stolen without repayment - (File: kaiax/gasless/impl/builder.go, kaiax/gasless/impl/getter.go)

### Summary
The OpenDollar report describes a case where a fee/tax-collection mechanism was implemented as an *optional* step in a call chain the user controls (a delegatecall target), so the user could simply skip that step and never pay the tax. The reachable analog in this Kaia repo is the `kaiax/gasless` module's "lend-then-repay" flow: the node lends the sponsored gas cost to the sender via a generated value-transfer transaction, and repayment (the "fee"/commission owed back to the lender) is only supposed to occur inside the subsequent, separately-executed `swapForGas` transaction on the `GaslessSwapRouter`. Nothing in the bundle-construction code that I could inspect enforces that the lend transaction is atomically bound to successful repayment; the guarantee instead rests on off-chain, pre-execution checks that a malicious sender's on-chain state can be arranged to defeat.

### Finding Description
When a user submits a gasless approve+swap pair, `GaslessModule.ExtractTxBundles` (`kaiax/gasless/impl/builder.go:28-72`) builds a bundle consisting of a generated "lend" transaction followed by the (optional) approve tx and the swap tx: [1](#0-0) 

The lend transaction is produced by `GetLendTxGenerator`, which transfers KAIA (`tx.Fee()` of the swap, plus the approve tx's fee if present) directly to the sender's own address, i.e. the sender receives real native-currency value up-front: [2](#0-1) 

The intended repayment mechanism — the sender's `amountRepay` being pulled back to the lender by the `GaslessSwapRouter` contract during `swapForGas` — is a completely separate, subsequently-executed transaction. The bundling/pool logic that decides whether the swap transaction is admissible only performs *pre-execution* checks: token balance, allowance, computed `amountIn` versus DEX quote, deadline, and repay-amount-vs-min-out arithmetic (`kaiax/gasless/impl/getter.go:195-266`, `kaiax/gasless/impl/tx_pool.go:102-182`): [3](#0-2) [4](#0-3) 

None of these checks execute the actual swap or confirm on-chain that the repayment will succeed; they are static/pre-flight validations performed at admission time (in the tx pool and in bundle extraction), similar in spirit to how OpenDollar's `BasicActions` tax logic was an off-path step a user could omit. Just as the OpenDollar `ODProxy` blindly delegatecalls to whatever contract address is supplied without verifying that the tax-collection code actually ran, Kaia's block-building bundling logic here similarly assumes — but does not cryptographically or atomically guarantee — that the swap transaction which repays the lender will execute successfully in the same block, immediately after the lend transfer, and for the exact expected amount. If the swap transaction can be made to revert, be dropped, reordered away from the lend tx, or excluded after the lend tx lands (e.g., due to state changes between admission-time checks and inclusion-time execution — DEX price moving, allowance revoked, token balance withdrawn between the `checkBalanceForSwap` pre-check and actual block assembly, or the bundle simply not being atomic in the builder), the sender keeps the lent KAIA without ever paying the commission/fee that funds it, exactly mirroring the "tax bypass" bug class: a value-owed step that is not enforced by the core protocol/consensus path but by a separable, best-effort application-layer sequence.

I was not able to locate and confirm the exact atomicity guarantee of `work/builder.Bundle` execution (i.e., whether the builder guarantees all constituent transactions of a bundle either all land or none do, and whether it re-validates state immediately before inclusion) within the available indexed content — `work/builder/builder.go` and `work/builder/bundle.go` internals were not retrievable in this session. This is the key uncertain point: if bundle execution is strictly atomic and re-validated at inclusion time, the impact would be reduced to a liveness/DoS issue (dropped bundles) rather than value theft. Given the indexing limits, a Devin session with full file access should verify:
- Whether `builder.NewBundle`/its consumer in `work/worker.go` enforces atomic all-or-nothing inclusion of `[LendTx, ApproveTx?, SwapTx]`.
- Whether checks in `checkBalanceForSwap` are re-run immediately before block inclusion (not just at tx-pool admission).

### Impact Explanation
If the lend-then-repay sequence is not atomically guaranteed, an attacker (any unprivileged transaction sender interacting with the public gasless RPC) can receive a real KAIA transfer from the node/lender key (`GetLendTxGenerator`, funded from `NodeKey`) and then cause the paired swap transaction to fail or be excluded, walking away with the lent gas funds while the lender absorbs the loss. This is unauthorized value movement / fee-delegation abuse reachable from a single submitted gasless transaction pair, which the Validate criteria explicitly calls out as acceptable impact (fee/fee-delegation abuse, gasless settlement theft).

### Likelihood Explanation
The gasless RPC (`gasless_isGaslessTx`, and the tx-pool admission path in `kaiax/gasless/impl/tx_pool.go`) is a public-facing entry point reachable by any endpoint-node client submitting raw approve/swap transactions — no special privilege is required. Constructing a swap that passes admission-time checks but reverts or becomes invalid by inclusion time (e.g., by front-running one's own allowance revocation, or by racing DEX price/slippage against `minAmountOut`) is a realistic, sender-controlled sequence, making this readily reachable, assuming the atomicity gap described above indeed exists.

### Recommendation
- Ensure the builder enforces true atomic bundling for gasless bundles: either all of `[LendTx, ApproveTx?, SwapTx]` land in the same block and succeed, or none do, with the lend transaction's execution made conditional (in-block) on the router successfully collecting `amountRepay` from the swap.
- Re-validate `checkBalanceForSwap` conditions (balance, allowance, DEX quote, deadline) at the point of block inclusion/execution, not only at tx-pool admission, to close the window between validation and execution.
- Consider having the router contract itself receive/hold the lent amount and only forward it to the sender after `amountRepay` is confirmed collected, rather than performing an unconditional up-front value transfer from the node key.

### Proof of Concept
Not independently reproduced in this session (no execution environment available). The conceptual PoC, mirroring the original report's methodology, would be:
1. Submit a valid `approveTx` + `swapTx` gasless pair that passes `GaslessAPI.IsGaslessTx` / `checkBalanceForApprove` / `checkBalanceForSwap` at admission time (`kaiax/gasless/impl/api.go:72-126`, `kaiax/gasless/impl/tx_pool.go:74-182`).
2. Before the bundle is included in a block, alter on-chain state so the `swapTx` will revert or be excluded while the `LendTx` (which unconditionally transfers KAIA to the sender, per `GetLendTxGenerator`) still lands — e.g., revoke the ERC-20 allowance to the router in a competing transaction, or manipulate the DEX pool to violate `minAmountOut`/`amountRepay` at execution time.
3. Confirm on-chain that the sender's KAIA balance increased by the lend amount while no corresponding `amountRepay` was collected by the router, replicating the "value received without paying the required fee" pattern from the original report.

This PoC step could not be executed against the actual repository in this session; verifying builder/bundle atomicity semantics (`work/builder/bundle.go`, `work/worker.go`) is required to confirm exploitability with certainty.

### Citations

**File:** kaiax/gasless/impl/builder.go (L28-52)
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

```

**File:** kaiax/gasless/impl/getter_test.go (L254-264)
```go
			generator := g.GetLendTxGenerator(tc.approve, tc.swap)
			tx, err := generator.GetTx(0)
			require.NoError(t, err)

			// tx contents test
			require.Equal(t, crypto.PubkeyToAddress(privkey.PublicKey).Bytes(), tx.To().Bytes())
			lendAmount := tc.swap.Fee()
			if tc.approve != nil {
				lendAmount.Add(lendAmount, tc.approve.Fee())
			}
			require.Zero(t, lendAmount.Cmp(tx.Value()))
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
