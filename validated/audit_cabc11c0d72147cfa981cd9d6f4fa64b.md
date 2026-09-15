### Title
Gasless swap admission check (`checkBalanceForSwap`) enforces a stricter exchange-rate/output invariant than the readiness/repay check (`VerifyExecutable`), letting a user bypass the amountIn/minAmountOut invariant depending on which code path a node relies on - (File: kaiax/gasless/impl/tx_pool.go, kaiax/gasless/impl/getter.go)

### Summary
The `ERC4626Cloned.deposit`/`mint` bug class is: two entry points are meant to enforce equivalent economic invariants, but each checks a different (non-equivalent, rate-dependent) quantity, so a user can pick the less restrictive path. The Kaia `kaiax/gasless` module has an analogous structural pattern: `checkBalanceForSwap` (mempool admission, `GetCheckBalance()`) enforces `AmountIn >= GetAmountIn(MinAmountOut)` (an exchange-rate-based invariant) as an *optional* check, while `VerifyExecutable`/`IsExecutable` (the function that actually governs whether the sender's GaslessSwapTx is treated as *ready* and bundled into a block with a `LendTx`) never checks this exchange-rate invariant at all — it only checks nonce sequencing, token/sender consistency, and that `AmountRepay` matches the fee-derived `repayAmount()`.

### Finding Description
`GetCheckBalance()` in [1](#0-0)  is called from the tx-pool balance-check step to validate gasless swap transactions in place of the ordinary sender-balance check, and it delegates to `checkBalanceForSwap`, which enforces `tx.amountIn >= gsr.getAmountIn(minAmountOut)` only if `g.GaslessConfig.ShouldCheckSwapAmount()` is true [2](#0-1) .

However, the function that actually decides whether a GaslessSwapTx is *ready* for block inclusion — `IsExecutable`/`VerifyExecutable`, invoked from `isSwapTxReady`/`isApproveTxReady` in the same file [3](#0-2)  — never performs this rate-consistency check. Its full validation logic in [4](#0-3)  only checks: sender consistency (AP1), token consistency (SP1), sufficient approval (SP2), nonce sequencing (SP3), and that `swapArgs.AmountRepay` equals the fee-derived `repayAmount(approveTxOrNil, swapTx)` (SP4). There is no check that `AmountIn` is consistent with `MinAmountOut` via the router's exchange rate.

This mirrors the ERC4626 pattern precisely: `checkBalanceForSwap` is the "restrictive" check (analogous to `deposit`'s `shares > minDepositAmount()`), and `VerifyExecutable`/`IsExecutable` is the "permissive" path (analogous to `mint`'s independent `assets > minDepositAmount()`) that omits the equivalent rate-based constraint entirely. Since `IsExecutable` is the sole gate used by `isReady`/`IsReady` to decide promotion and bundling [5](#0-4) , and the actual repayment amount lent by the proposer is computed purely from gas fees (`repayAmount`) rather than from the swap's economic output [6](#0-5) , a sender can submit a `GaslessSwapTx` whose declared `AmountIn` is *less* than what the router's current exchange rate would require for the declared `MinAmountOut`, and this tx can still be deemed executable/bundled as long as it satisfies SP1–SP4, provided the admission-time balance check (`ShouldCheckSwapAmount`) is disabled or the state has changed since admission (since `checkBalanceForSwap` is only invoked once, at tx-pool-add time, using state at that time, not re-verified at bundling time).

### Impact Explanation
If the swap-amount consistency check is skipped or stale relative to block-building time, the actual on-chain `swapForGas` call in the router can revert (since the router itself is expected to enforce the true minAmountOut/amountIn/exchange-rate relationship), wasting the proposer-funded `LendTx` gas or causing bundle inclusion of a transaction that fails execution. Because the proposer (block builder) pre-funds the user's gas via `GetLendTxGenerator`/`LendTx` before the swap executes [7](#0-6) , a systematic gap between the admission-time (optional) rate check and the bundling-time `VerifyExecutable` check can be leveraged to get gas fronted for a swap tx that predictably fails on-chain, at worst resulting in wasted lent value/gas that the module's repay accounting does not recapture (since a failed swap does not repay the lender), and at minimum a state-divergence-prone gap between the promotion decision and the actual settlement outcome.

### Likelihood Explanation
The `ShouldCheckSwapAmount()` config flag suggests this check is deployment/config-dependent, and is documented in `kaiax/gasless/README.md` as being part of "balance check," not part of "ready condition" (`IsExecutable`) [8](#0-7) . Any single unprivileged user can craft a `GaslessApproveTx`+`GaslessSwapTx` pair (or standalone swap) whose `AmountIn`/`MinAmountOut` do not correspond to the router's live exchange rate; whether this succeeds depends entirely on whether the node's `GaslessConfig.ShouldCheckSwapAmount()` is enabled and whether pool state at admission still matches state at bundling time, making exploitability configuration/timing dependent rather than universally guaranteed.

### Recommendation
Move the `AmountIn`-vs-`GetAmountIn(MinAmountOut)` invariant check (and make it mandatory, not gated by `ShouldCheckSwapAmount()`) into `VerifyExecutable` in `kaiax/gasless/impl/getter.go`, so both the mempool admission path (`checkBalanceForSwap`) and the actual readiness/bundling gate (`IsExecutable`) enforce the identical exchange-rate invariant using state as of the moment of bundling, eliminating the divergence between the two enforcement points, analogous to unifying `ERC4626Cloned.deposit`/`mint`'s minimum-deposit checks on a single canonical quantity.

### Proof of Concept
Conceptual reproduction (would require a running Kaia node/testnet):
1. Deploy/configure a `GaslessSwapRouter` (KIP-247) with a non-trivial exchange rate for a whitelisted token, and set node config with `ShouldCheckSwapAmount=false` (or exploit the time-of-check/time-of-use gap between admission and bundling under a fluctuating rate).
2. Craft a `GaslessApproveTx` (or omit it if nonce allows) approving the router for `MaxUint256`.
3. Craft a `GaslessSwapTx` calling `swapForGas(token, amountIn, minAmountOut, amountRepay, deadline)` where `amountIn` is deliberately less than `router.GetAmountIn(token, minAmountOut)` at current rates, but where `amountRepay == repayAmount(approveTxOrNil, swapTx)` (computed purely from gas fees per `kaiax/gasless/impl/getter.go:346-367`) so SP4 passes.
4. Submit via `auction_submitBid`-adjacent or plain `eth_sendRawTransaction`; observe that `GaslessModule.IsExecutable`/`VerifyExecutable` returns `true` (since it never checks amountIn-vs-rate), causing the tx to be promoted and bundled with a `LendTx` funded by the block proposer, even though the swap will revert or fail to produce the promised `minAmountOut` on execution — demonstrating the same class of "different functions, different invariant on the same value pair" inconsistency as the ERC4626 `deposit`/`mint` finding.

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

**File:** kaiax/gasless/impl/tx_pool.go (L185-230)
```go
func (g *GaslessModule) IsReady(txs map[uint64]*types.Transaction, next uint64, ready types.Transactions) bool {
	g.knownTxsMu.Lock()
	defer g.knownTxsMu.Unlock()

	tx, ok := txs[next]
	if !ok {
		return false
	}

	if !g.isReady(txs, next, ready) {
		return false
	}

	if g.IsBundleTx(tx) {
		// If prev tx is bundle tx, there's no need to check the knownTxs limit because it has been checked in the previous `IsReady()` execution.
		isPrevTxBundleTx := len(ready) != 0 && g.IsBundleTx(ready[len(ready)-1])
		if isPrevTxBundleTx {
			g.knownTxs.add(tx, TxStatusPending)
			return true
		}

		maxBundleTxsInPending := g.GetMaxBundleTxsInPending()
		if maxBundleTxsInPending != math.MaxUint64 {
			numExecutable := uint(g.knownTxs.numExecutable())

			numSeqTxs := uint(1)
			for i := next + 1; i < next+uint64(len(txs)); i++ {
				if tx, ok := txs[i]; ok && g.IsBundleTx(tx) {
					numSeqTxs++
				} else {
					break
				}
			}

			// false if there is possibility of exceeding max bundle tx num
			if numExecutable+numSeqTxs > maxBundleTxsInPending {
				logger.Trace("Not promoting a tx because of exceeding max bundle tx num", "tx", tx.Hash().String(), "numExecutable", numExecutable, "maxBundleTxsInPending", maxBundleTxsInPending)
				return false
			}
		}

		g.knownTxs.add(tx, TxStatusPending)
	}

	return true
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

**File:** kaiax/gasless/README.md (L15-27)
```markdown
#### Ready

This module is responsible for promoting gasless transactions.
Sender's nonce of GaslessSwapTx is checked to distinguish if GaslessApproveTx is expected. If `tx.nonce == GetNonce(sender) + 1`, GaslessApproveTx is expected. If `tx.nonce == GetNonce(sender)`, GaslessApproveTx is not expected.

If GaslessApproveTx is expected, GaslessApproveTx and GaslessSwapTx can be promoted when they are both ready for execution.
Otherwise, GaslessSwapTx can be promoted when it is ready for execution.

See ready condition [KIP-247](https://kips.kaia.io/KIPs/kip-247) and the implementation `IsExecutable(approveTxOrNil, swapTx *types.Transaction) bool`.

#### Balance check

Sender balance check is omitted for gasless transactions (see `GetCheckBalance()`).
```
