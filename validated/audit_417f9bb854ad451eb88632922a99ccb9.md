## Analysis

The Sherlock finding is about a **stale/incorrect "is-actionable" oracle**: `bountyIsClaimable` only checks `status == OPEN`, but the real claim path for tiered bounties transitions status to `CLOSED` *before* the claim executes, so the oracle diverges from the actual claim-eligibility logic used by the mutating function. The reusable bug class is: *a view/gating predicate used to decide whether an irreversible value-moving action is safe does not implement the same conditions as the code that actually performs/relies on that action, so the gating predicate can say "OK" while the real settlement fails (or vice-versa)*.

In this Kaia repo, the closest reachable analog is in the **gasless module** (KIP-247), which is directly reachable by any transaction sender (an unauthorized user can craft an approve/swap tx pair).

### Root cause

The gasless module gates the irreversible `LendTx` (a real, proposer-funded native-KAIA transfer, unconditionally included once bundled) using `IsExecutable`/`VerifyExecutable`, which only checks structural/decoding conditions (whitelisted token/router, sender/nonce/amount matching, and the repay-amount arithmetic): [1](#0-0) 

This same `IsExecutable` predicate is reused both to promote txs in the pool (`isApproveTxReady`/`isSwapTxReady`) and, critically, to decide block-building bundling in `ExtractTxBundles`, where the `LendTxGenerator` (unconditional value transfer from proposer to sender) is prepended before the actual `SwapTx`: [2](#0-1) [3](#0-2) 

However, the *real* precondition for the swap to succeed and actually repay the proposer (token balance, allowance/approval sufficiency, AMM exchange-rate sufficiency, and swap deadline) is implemented in a **separate function**, `checkBalanceForSwap`/`checkBalanceForApprove`, which is only invoked through `GetCheckBalance()` at initial tx-pool admission time: [4](#0-3) [5](#0-4) 

The module's own README confirms this split of responsibilities and that the readiness/executability check (`IsExecutable`) is deliberately distinct from the balance check: [6](#0-5) 

`VerifyExecutable` never re-validates balance/allowance/deadline against the state at bundling/inclusion time — it is called again by `isApproveTxReady`/`isSwapTxReady` on each pool reset only for pool promotion, and by `ExtractTxBundles` for actual block inclusion, but neither path calls `checkBalanceForSwap`: [7](#0-6) 

Because token balance/allowance can legitimately change between initial pool admission (when `checkBalanceForSwap` was last satisfied) and the moment the block is actually built/sealed (e.g., attacker transfers away the token, revokes approval via another transaction, or simply waits until conditions decay before the swap is finally included), `VerifyExecutable`/`IsExecutable` can return "executable" for a swap that will in fact revert on-chain. Since `GetLendTxGenerator` produces an **unconditional, independently-signed native-value transfer** from the proposer to the swap sender (not contingent on the swap's success), the bundle can execute as `[LendTx, ApproveTx?, SwapTx]` where `LendTx` succeeds and transfers real KAIA to the sender, while `SwapTx` reverts — meaning the on-chain repayment inside `swapForGas` (`SwappedForGas` event / repay-to-proposer logic) never happens: [8](#0-7) [9](#0-8) 

The project's own integration test explicitly demonstrates that a swap can be made to revert purely due to insufficient balance/approval/exchange-rate conditions that are only checked in `checkBalanceForSwap`, not in `VerifyExecutable`: [10](#0-9) 

### Title
`GaslessModule.IsExecutable`/`VerifyExecutable` used to gate irrevocable `LendTx` inclusion omits the real settlement preconditions checked by `checkBalanceForSwap`, enabling proposer fund loss via unrepaid gasless swaps - (File: kaiax/gasless/impl/getter.go, kaiax/gasless/impl/tx_pool.go, kaiax/gasless/impl/builder.go)

### Summary
The gasless bundling logic decides whether to prepend an unconditional native-KAIA `LendTx` to a swap transaction using `IsExecutable`/`VerifyExecutable`, which validates only structural/arithmetic conditions and never re-checks token balance, allowance, AMM rate sufficiency, or swap deadline against the state at actual inclusion time. Those checks exist only in `checkBalanceForSwap`, invoked solely at initial tx-pool admission via `GetCheckBalance`. Any unprivileged sender can construct an approve/swap pair that is accepted into the pool, get it re-validated as "executable" purely on structural grounds during block building, and have the state (balance/allowance) invalidated in the interim — causing the `LendTx` to unconditionally transfer real value to the sender while the paired `SwapTx` reverts, so the on-chain repayment to the proposer never occurs.

### Finding Description
`VerifyExecutable` (`kaiax/gasless/impl/getter.go:214-266`) implements conditions Ax/Sx/AP1/SP1/SP2/SP3/SP4 (whitelisting, sender/token/nonce matching, amount arithmetic) but does not check actual sender token balance, actual allowance, actual AMM-quoted `amountIn` sufficiency, or the swap deadline against live chain state. Those are only enforced in `checkBalanceForSwap`/`checkBalanceForApprove` (`kaiax/gasless/impl/tx_pool.go:74-182`), which are wired up solely through `GetCheckBalance()` — used at initial pool admission, per the module's own documented design ("Balance check is omitted for gasless transactions [structural checks]; see `GetCheckBalance()`" in `kaiax/gasless/README.md:25-27`).

`IsExecutable`/`VerifyExecutable` is reused, unmodified, both for tx-pool promotion (`isApproveTxReady`/`isSwapTxReady`, `kaiax/gasless/impl/tx_pool.go:251-290`) and for block-building bundling (`ExtractTxBundles`, `kaiax/gasless/impl/builder.go:28-72`), where it directly gates whether `GetLendTxGenerator` (`kaiax/gasless/impl/getter.go:268-313`) prepends an unconditional value transfer to the bundle. Because balance/allowance/rate/deadline can decay between the last time `checkBalanceForSwap` succeeded (pool admission) and the time the bundle is actually assembled and included in a block, `IsExecutable` can wrongly report "executable" even though the `SwapTx` will revert on-chain, exactly mirroring the audited bug class where a gating predicate (`bountyIsClaimable`) doesn't match the real state/preconditions used by the action it gates.

### Impact Explanation
`LendTx` is an unconditional, independently-signed proposer-funded value transfer (`kaiax/gasless/impl/getter.go:268-313`); its execution does not depend on the paired `SwapTx` succeeding. If `SwapTx` reverts due to insufficient balance/allowance/AMM rate/deadline expiry that `VerifyExecutable` failed to re-check, the sender still received real KAIA from the `LendTx` with no compensating repayment (`swapForGas`'s `SwappedForGas`/repay-to-proposer logic never executes). This is a concrete value-transfer/settlement-theft impact against the block proposer, directly reachable by any transaction sender crafting or timing an approve/swap pair.

### Likelihood Explanation
Any unprivileged account can submit a valid-looking approve+swap gasless pair, have it admitted to the pool (satisfying `checkBalanceForSwap` momentarily), then before inclusion alter their own balance/allowance (e.g., transfer away tokens, revoke approval) or let the swap deadline lapse. Because `ExtractTxBundles`/`IsExecutable` at inclusion time does not re-verify these conditions, the malicious/careless swap can still be selected for bundling with the accompanying `LendTx`, making this reliably triggerable by a single account controlling the timing of its own token state, without requiring any validator/proposer complicity.

### Recommendation
Re-run the same on-chain preconditions enforced by `checkBalanceForSwap`/`checkBalanceForApprove` (balance, allowance, AMM rate, deadline) inside `VerifyExecutable`, or explicitly call `checkBalanceForSwap`/`checkBalanceForApprove` against the state that will be used for block building (not just pool-admission state) before `ExtractTxBundles` decides to prepend `LendTx`, so the gating predicate used to authorize the unconditional `LendTx` transfer matches the real settlement conditions at the moment of inclusion.

### Proof of Concept
1. Attacker deploys/holds an allowed ERC-20 token and sends `ApproveTx` (max allowance to `GaslessSwapRouter`) followed by `SwapTx` (`swapForGas`) with a valid `amountIn`/`minAmountOut`/`amountRepay`, satisfying `checkBalanceForApprove`/`checkBalanceForSwap` at admission (`kaiax/gasless/impl/tx_pool.go:74-182`), so both are accepted into the pool.
2. Before the proposer builds the block containing this pair, attacker sends (from the same or another account they control) a transaction transferring away enough of the token to drop below `amountIn`, or simply lets `swapArgs.Deadline` pass — none of this is re-checked by `VerifyExecutable` (`kaiax/gasless/impl/getter.go:214-266`), which only checks structural/nonce/amount-arithmetic conditions.
3. `ExtractTxBundles` (`kaiax/gasless/impl/builder.go:28-72`) still treats the pair as executable and builds bundle `[LendTx, ApproveTx, SwapTx]`, with `LendTx` unconditionally transferring `lendAmount(approveTx, swapTx)` KAIA from the proposer to the attacker (`kaiax/gasless/impl/getter.go:268-313`, `346-359`).
4. The block executes: `LendTx` succeeds (attacker receives KAIA), but `SwapTx` reverts on-chain due to insufficient balance/allowance/deadline (as demonstrated directly by `tests/gasless_test.go:245-258` for the same failure modes), so `swapForGas`'s repayment to the proposer never happens — the proposer is out the lent KAIA with no compensating repayment.

### Citations

**File:** kaiax/gasless/impl/getter.go (L203-266)
```go
func (g *GaslessModule) IsExecutable(approveTxOrNil, swapTx *types.Transaction) bool {
	err := g.VerifyExecutable(approveTxOrNil, swapTx)
	if err != nil {
		return false
	}
	return true
}

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

**File:** kaiax/gasless/impl/tx_pool.go (L251-290)
```go
// isApproveTxReady assumes that the caller checked `g.IsApproveTx(approveTx)`
func (g *GaslessModule) isApproveTxReady(approveTx, nextTx *types.Transaction) bool {
	addr, err := types.Sender(g.signer, approveTx)
	if err != nil {
		return false
	}
	nonce := g.getCurrentStateNonce(addr)

	if approveTx.Nonce() != nonce {
		return false
	}
	if nextTx == nil || !g.IsSwapTx(nextTx) {
		return false
	}

	return g.IsExecutable(approveTx, nextTx)
}

// isSwapTxReady assumes that the caller checked `g.IsSwapTx(swapTx)`
func (g *GaslessModule) isSwapTxReady(swapTx, prevTx *types.Transaction) bool {
	addr, err := types.Sender(g.signer, swapTx)
	if err != nil {
		return false
	}
	nonce := g.getCurrentStateNonce(addr)

	var approveTx *types.Transaction
	if swapTx.Nonce() == nonce {
		approveTx = nil
	} else if swapTx.Nonce() == nonce+1 {
		if prevTx == nil || !g.IsApproveTx(prevTx) {
			return false
		}
		approveTx = prevTx
	} else {
		return false
	}

	return g.IsExecutable(approveTx, swapTx)
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

**File:** contracts/bindings/kip247/GaslessSwapRouter.go (L1127-1135)
```go
// GaslessSwapRouterSwappedForGas represents a SwappedForGas event raised by the GaslessSwapRouter contract.
type GaslessSwapRouterSwappedForGas struct {
	Proposer        common.Address
	AmountRepaid    *big.Int
	User            common.Address
	FinalUserAmount *big.Int
	Commission      *big.Int
	Raw             types.Log // Blockchain specific contextual infos
}
```

**File:** tests/gasless_test.go (L245-258)
```go
	//// Reject obviously reverting SwapTx.

	// reject swapTx when minAmountOut < amountRepay
	_, err = sendSwapTx(t, gsrContract, accounts[0], testTokenAddr, swapAmmount, common.Big0, amountRepaySwap, deadline)
	assert.ErrorContains(t, err, "insufficient minAmountOut")

	// reject swapTx when amountIn < router.GetAmountIn(minAmountOut)
	_, err = sendSwapTx(t, gsrContract, accounts[0], testTokenAddr, common.Big0, minAmountOut, amountRepaySwap, deadline)
	assert.ErrorContains(t, err, "insufficient amountIn")

	// reject swapTx when balance < amountIn
	// the test acc first received `transferToken` but used up some. So it has less than `transferToken`.
	_, err = sendSwapTx(t, gsrContract, accounts[0], testTokenAddr, transferToken, minAmountOut, amountRepaySwap, deadline)
	assert.ErrorContains(t, err, "insufficient balance")
```
