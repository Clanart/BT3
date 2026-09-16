### Title
Gasless swap price check uses stale AMM quote for value verification, allowing sender to receive lent gas without guaranteed repayment - (File: kaiax/gasless/impl/tx_pool.go)

### Summary
The Hubble report shows a class of bug where a value that is *provisional/unrealized at check time* (unrealized PnL) is treated as settled collateral, letting the account extract real value before the provisional value can actually be realized, leaving a fee/loss to be socialized (insurance). The Kaia `gasless` module (KIP-247) has an analogous pattern: transaction-pool admission for a `GaslessSwapTx` estimates the required `amountIn` for a given `minAmountOut` via a *live* AMM quote (`routerContract.GetAmountIn`), but the actual value transferred to the user (`LendTx`, funded by the block proposer) happens as a *separate, already-executed* transaction, sequenced before the swap's on-chain settlement actually finalizes repayment.

### Finding Description
For a `GaslessSwapTx`, `checkBalanceForSwap` in [1](#0-0)  validates `amountIn` against a live call to `GetAmountIn(token, minAmountOut)` on the `GaslessSwapRouter` contract — a spot-price-dependent quote that can change between the time it is checked (tx-pool admission / block building) and the time the transaction is actually executed. This is the same "provisional value counted as if final" pattern as the Hubble unrealizedPnL bug: a price/AMM-state-dependent quantity is used to gate an irreversible payout.

The actual fee is fronted to the user *before* the swap settles: per [2](#0-1) , the block-building flow prepends a `LendTxGenerator`-produced transaction (sending real KAIA from the block proposer to the user) ahead of the `ApproveTx`/`SwapTx` bundle: `[LendTxGenerator, GaslessApproveTx, GaslessSwapTx]`. The `LendTx` itself is a plain value-transfer built and signed independently in `GetLendTxGenerator`/`MakeLendTx` at [3](#0-2) , computing `lendAmount` purely from `Fee()` of the approve/swap transactions ( [4](#0-3) ) — it is not conditioned on the swap actually succeeding or on-chain repayment actually landing in the router/proposer.

`repayAmount` — the amount the contract-level `swapForGas` is expected to enforce as repayment — is likewise computed off of tx-level fee math ( [5](#0-4) ) and validated only at bundle-formation time via `VerifyExecutable`/`IsExecutable` ( [6](#0-5) ), which checks `SP4` (`SwapTx.amountRepay == repayAmount(...)`) but does not re-verify against a fresh AMM state guarantee at the moment of on-chain execution beyond the contract's own `minAmountOut` slippage check. Sender balance checking for gasless transactions is explicitly skipped in the tx pool (`shouldSkipBalanceCheck`) as documented: "Sender balance check is omitted for gasless transactions (see `GetCheckBalance()`)" [7](#0-6) , and the tx-pool's `checkBalanceForSwap` only optionally re-validates against a live AMM quote depending on `BalanceCheckLevel` ( [8](#0-7) ), which can be configured down to `BalanceCheckLevelStatic` (no token/AMM checks at all), as exercised directly in the module's own tests: `gaslessConfig.BalanceCheckLevel = gasless.BalanceCheckLevelStatic` used in `TestPromoteGaslessTxsWithMultiSenders` [9](#0-8) .

Because `LendTx` is a separate, unconditional native-value transfer executed before/independently of the swap's actual settlement within the block, if the `GaslessSwapTx` reverts or fails to fully repay (e.g., AMM price moved between the tx-pool's estimate and the block's actual execution, or the router's on-chain `minAmountOut` guard triggers a partial/failed state after the `LendTx` value has already left the proposer), the proposer has already paid out real KAIA to the sender with no atomic guarantee of clawback. This mirrors the Hubble pattern exactly: a value based on a mutable/predictive state (AMM spot price / unrealized PnL) is treated as sufficient collateral for a payout that is settled separately and can diverge from the check-time estimate, and the loss falls on a third party that has no recourse (proposer here, insurance fund in Hubble).

### Impact Explanation
If the swap fails to fully repay the lent amount due to AMM price divergence between the tx-pool check and actual on-chain settlement, or if the swap transaction reverts after the `LendTx` has already unconditionally transferred KAIA, the block proposer suffers an uncompensated loss of the lent gas fee. This is a direct value-movement / fee-abuse impact: an unprivileged gasless-swap sender can effectively obtain gas-fee-equivalent value from the block proposer without guaranteed repayment, analogous to a liquidated Hubble user extracting margin based on transient unrealized PnL before the fee obligation materializes.

### Likelihood Explanation
Any user submitting a `GaslessSwapTx` can trigger this by targeting a low-liquidity/volatile token pool so that the AMM price used in `checkBalanceForSwap`'s `GetAmountIn` estimate (at tx-pool admission time) diverges materially from the price at actual on-chain execution time in a later block, or by timing submissions around large trades on the pool. The severity is amplified by operator-tunable `BalanceCheckLevel`, which can disable the AMM-amount check (`BalanceCheckLevelStatic`/`BalanceCheckLevelTokenBalanceAndAllowance`) entirely, as demonstrated in the module's own test configuration, widening the exploitable window without requiring any special privilege — this is reachable purely via public transaction submission (gasless swap flow).

### Recommendation
Do not treat the tx-pool/block-building-time AMM quote (`GetAmountIn`) as final settlement collateral for constructing an unconditional `LendTx`. Either (a) make the `LendTx` payout conditional/atomic with successful, on-chain-verified repayment within the same bundle (e.g., via a wrapping contract that reverts the whole bundle unless repayment is confirmed), or (b) require the `GaslessSwapRouter` contract itself to hold/escrow the lent amount and only release it to the sender after confirming actual repayment in the same transaction, removing reliance on the pool's advance quote. Additionally, tighten the minimum `BalanceCheckLevel` allowed in production configurations so the AMM-amount check cannot be disabled.

### Proof of Concept
1. Proposer/CN configures `gasless.balance-check-level` at or below `BalanceCheckLevelTokenBalanceAndAllowance` (skipping the `GetAmountIn` re-check), or simply relies on the standard flow where the check is done against the pool state at admission time — see `checkBalanceForSwap` [10](#0-9) .
2. Attacker submits a `GaslessApproveTx` + `GaslessSwapTx` pair for a thinly-liquid token pool with `minAmountOut` computed against the current AMM reserves, satisfying `checkBalanceForSwap` at submission time.
3. Before the transaction is picked up for block inclusion, attacker (or a third party) executes a large trade against the same AMM pool, shifting the exchange rate unfavorably.
4. The block proposer's worker still forms the bundle `[LendTxGenerator, GaslessApproveTx, GaslessSwapTx]` per `GetLendTxGenerator`/`MakeLendTx` [3](#0-2) , sending the `LendTx` (real KAIA) to the sender unconditionally.
5. The `GaslessSwapTx` executes on-chain against the now-worse AMM price; the contract's `minAmountOut` guard may cause the swap to revert or produce insufficient output to fully cover `amountRepay`, while the `LendTx` has already unconditionally transferred value to the sender, leaving the proposer with an unrecovered loss — functionally identical to the Hubble scenario where a liquidated account's fee-paying collateral (based on stale unrealized PnL) is gone by the time the fee actually needs to be collected.

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

**File:** kaiax/gasless/README.md (L25-27)
```markdown
#### Balance check

Sender balance check is omitted for gasless transactions (see `GetCheckBalance()`).
```

**File:** kaiax/gasless/README.md (L29-36)
```markdown
### Block building rules

Upon detection of GaslessTxs, the following logics are executed:

- Per sender, if exists, GaslessApproveTx is relocated before GaslessSwapTx.
- LendTxGenerator is prepended before GaslessApproveTx.
- A new bundle is generated which contain either `[LendTxGenerator, GaslessApproveTx, GaslessSwapTx]` or `[LendTxGenerator, GaslessSwapTx]`
- If the bundle has conflict with any previous bundles, it is excluded from the returned bundle list.
```

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

**File:** kaiax/gasless/impl/getter.go (L361-367)
```go
func repayAmount(approveTxOrNil, swapTx *types.Transaction) *big.Int {
	// R1 = LendTx.Fee() = SwapTx.GasPrice() * TxGas
	r1 := new(big.Int).Mul(swapTx.GasPrice(), new(big.Int).SetUint64(params.TxGas))

	// RepayAmount = R1 + R2 + R3
	return new(big.Int).Add(r1, lendAmount(approveTxOrNil, swapTx))
}
```

**File:** kaiax/gasless/config.go (L64-69)
```go
const (
	BalanceCheckLevelStatic                   = iota // relation between amounts and deadline
	BalanceCheckLevelTokenBalanceAndAllowance        // all above + token balance and allowance
	BalanceCheckLevelSwapAmount                      // all above +	amountIn calculated by dex
	BalanceCheckLevelAll                             // all above +	sender code check
)
```

**File:** kaiax/gasless/impl/tx_pool_test.go (L443-452)
```go
func TestPromoteGaslessTxsWithMultiSenders(t *testing.T) {
	t.Parallel()

	testTxPoolConfig := blockchain.DefaultTxPoolConfig
	testTxPoolConfig.Journal = ""

	// Skip balance check for this test
	gaslessConfig := *gasless.DefaultGaslessConfig()
	gaslessConfig.BalanceCheckLevel = gasless.BalanceCheckLevelStatic

```
