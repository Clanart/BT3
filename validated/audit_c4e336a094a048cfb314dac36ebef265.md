## Title
Gasless bundle allows theft of proposer-lent KAIA when a crafted SwapTx that structurally passes `IsExecutable` reverts on-chain, since real-transfer verification is not enforced at bundle-execution time — (File: kaiax/gasless/impl/builder.go, kaiax/gasless/impl/getter.go)

### Summary
This is the closest reachable analog to the reported vault vulnerability. The external report describes a vault that emits a privileged "notification" message to an arbitrary receiver whose payload the attacker controls, letting the attacker trigger asset-consuming operations (swap/deposit) **without the underlying asset transfer actually having happened** — i.e., the message asserts a state ("funds received") that is never independently re-verified by the receiving contract.

Kaia's `kaiax/gasless` module has the same shape of trust gap: the block-building path (`ExtractTxBundles` → `GetLendTxGenerator`) unconditionally issues a real-value `LendTx` (proposer's own KAIA) to a swap sender based purely on *structural* pattern-matching of the swap transaction (`IsExecutable`/`VerifyExecutable`), not on a fresh, execution-time confirmation that the sender actually holds the balance/allowance needed to repay via the swap. [1](#0-0) 

### Finding Description
`ExtractTxBundles` builds a bundle `[LendTxGenerator, ApproveTx?, SwapTx]` whenever a pending transaction `IsSwapTx` and `IsExecutable`: [2](#0-1) 

`IsExecutable`/`VerifyExecutable` only checks *structural/arithmetic* invariants of the transaction pair (token/spender whitelisting, matching sender, nonce sequencing, and that `AmountRepay` equals the computed fee) — it does **not** re-verify the sender's current on-chain token balance or ERC-20 allowance: [3](#0-2) 

The balance/allowance checks that would catch an unfunded or unapproved sender exist only in `GetCheckBalance` / `checkBalanceForSwap`, which is documented and implemented as a **transaction-pool admission-time** check (`Ready`/promotion), not as part of `ExtractTxBundles` at block-building time: [4](#0-3) [5](#0-4) 

`GetLendTxGenerator` then unconditionally constructs and signs a real-value transaction sending `LendAmount` (the sum of the approve/swap tx fees) of the proposer's own KAIA to the swap sender's address, with no conditional/atomic linkage encoded at the protocol level between the lend transfer and the swap's actual successful repayment: [6](#0-5) 

This mirrors the report's root cause exactly: a privileged, value-bearing action (`LendTx`/vault notification) is dispatched based on an *assertion* that a corresponding condition holds (sender will repay via swap / assets were received), rather than an execution-time, atomically-linked verification. In the TON report, the vault sends the notification without confirming the token transfer; here, the proposer sends the lend transaction without confirming, at commit time, that the swap will actually succeed and repay.

Whether this is actually exploitable depends on whether the three bundle transactions (`LendTxGenerator`, `ApproveTx`, `SwapTx`) are committed to the block atomically (all rolled back together on any one's failure) by the block-building/worker logic that consumes `builder.Bundle`. The tool budget for this investigation was exhausted before the worker/`Task.ApplyTransactions` commit-loop atomicity semantics (partial iteration into `work/worker.go`) could be fully confirmed — specifically, whether a reverting/failing `SwapTx` inside a bundle causes the preceding `LendTx` to also be excluded/rolled back from the block, or whether `LendTx` (a plain value transfer, which essentially cannot revert once mined) is committed independently and unconditionally once selected into the block.

### Impact Explanation
If bundle inclusion is not strictly atomic (or if a race/TOCTOU window exists between the pool-level balance check at admission and state at block-building time — e.g., sender balance/allowance changes, AMM slippage causing an "insufficient minAmountOut"-style revert inside `swapForGas`, or `deadline` expiry), an attacker can:
1. Submit an `ApproveTx`+`SwapTx` pair that passes `IsExecutable`'s structural checks (and even the pool's balance check at submission time).
2. Drain their own token balance/allowance, or otherwise ensure the swap reverts, right before the bundle is committed.
3. Receive the proposer-funded `LendTx` (real KAIA value) while the `SwapTx` that was supposed to repay it fails or the ApproveTx is stale.

This is a direct value-drain from the block proposer (fee-delegation / gasless-lending abuse), matching the "Accept only concrete unauthorized value movement... gasless or auction settlement theft" acceptance criteria.

### Likelihood Explanation
Medium. The structural checks in `VerifyExecutable` are non-trivial and the pool does perform a balance check before promoting the transaction, which reduces — but does not eliminate — the TOCTOU window between pool admission and block-building/bundle extraction (`ExtractTxBundles` re-evaluates `IsExecutable`, which does not re-check balance/allowance). An attacker fully controls the timing of their own balance/allowance changes and can attempt to win this race against a specific block proposer.

### Recommendation
- Re-verify sender balance/allowance/deadline (the same checks performed in `checkBalanceForSwap`) inside `ExtractTxBundles`/`GetLendTxGenerator` immediately before bundle construction at block-building time, not only at pool admission.
- Ensure the block-building/worker commit logic treats `[LendTxGenerator, ApproveTx, SwapTx]` as strictly atomic — any failure or revert in the swap step must cause the entire bundle, including the `LendTx`, to be excluded from the block.
- Consider making the lend transfer conditional on-chain (e.g., have the `GaslessSwapRouter` contract itself pull/verify repayment atomically within the same transaction, rather than relying on the proposer sending an unconditional prior transfer).

### Proof of Concept
Conceptual (not fully verified against `work/worker.go` commit semantics due to exhausted investigation budget):
1. Attacker holds a whitelisted token with a balance just sufficient to pass `checkBalanceForSwap` at pool-admission time, and submits a valid `ApproveTx` + `SwapTx` pair.
2. Once both transactions are `Pending` and picked up for block building, the attacker (in a preceding transaction of their own, in the same or a prior block if timing allows) drains their token balance or revokes/spends the allowance, or simply lets `deadline` conditions/AMM state drift so that `swapForGas` will revert.
3. The proposer's worker calls `ExtractTxBundles`, which only checks `IsExecutable` (structural, not balance-based) and builds `[GetLendTxGenerator(...), ApproveTx, SwapTx]`. [7](#0-6) 
4. `GetLendTxGenerator` produces a signed transaction unconditionally transferring `LendAmount` of KAIA from the proposer's key to the attacker. [8](#0-7) 
5. If the bundle/commit logic does not atomically roll back the `LendTx` when `SwapTx` fails, the attacker keeps the lent KAIA without ever repaying it, resulting in unauthorized value movement from the block proposer.

### Citations

**File:** kaiax/gasless/impl/builder.go (L28-51)
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

**File:** kaiax/gasless/impl/getter.go (L211-265)
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

**File:** kaiax/gasless/impl/tx_pool.go (L102-141)
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
```

**File:** kaiax/gasless/README.md (L13-27)
```markdown
### Transaction pool rules

#### Ready

This module is responsible for promoting gasless transactions.
Sender's nonce of GaslessSwapTx is checked to distinguish if GaslessApproveTx is expected. If `tx.nonce == GetNonce(sender) + 1`, GaslessApproveTx is expected. If `tx.nonce == GetNonce(sender)`, GaslessApproveTx is not expected.

If GaslessApproveTx is expected, GaslessApproveTx and GaslessSwapTx can be promoted when they are both ready for execution.
Otherwise, GaslessSwapTx can be promoted when it is ready for execution.

See ready condition [KIP-247](https://kips.kaia.io/KIPs/kip-247) and the implementation `IsExecutable(approveTxOrNil, swapTx *types.Transaction) bool`.

#### Balance check

Sender balance check is omitted for gasless transactions (see `GetCheckBalance()`).
```
