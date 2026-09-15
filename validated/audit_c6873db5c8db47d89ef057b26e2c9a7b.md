Based on my investigation, I found a concrete analog in the `kaiax/gasless` module where two different validation layers check the swap deadline inconsistently, mirroring the `maxAge`/staleness mismatch pattern in the source report.

### Title
Gasless bundle building (`VerifyExecutable`) never re-validates `SwapArgs.Deadline`, allowing a proposer's pre-funded `LendTx` to be stranded when a stale swap is bundled - ([File: kaiax/gasless/impl/getter.go])

### Summary
The gasless flow lends KAIA gas fee to a sender via a `LendTx`, expecting repayment inside the subsequent `ApproveTx`/`SwapTx` bundle. The deadline staleness check (`tx.deadline >= currentTimestamp`) is enforced only in `checkBalanceForSwap`, which runs at tx-pool admission time, but is completely absent from `VerifyExecutable`, which is the function actually used at block-building time to decide whether to bundle (and thus fund) a swap.

### Finding Description
`checkBalanceForSwap` in [1](#0-0)  enforces the deadline check `tx.deadline >= currentTimestamp` only when this function is invoked (via `GetCheckBalance`, used for tx-pool admission/checks).

However, at block-building time, `ExtractTxBundles` in [2](#0-1)  decides whether to bundle `[LendTxGenerator, ApproveTx?, SwapTx]` purely based on `g.IsExecutable(approveTxs[addr], tx)`, which calls `VerifyExecutable` in [3](#0-2) . `VerifyExecutable` checks sender consistency (AP1), token consistency (SP1), approve amount (SP2), nonce ordering (SP3), and repay amount (SP4) — but it never checks `swapArgs.Deadline` against the current block time. This is the same class of bug as the reported issue: one layer computes/enforces a staleness bound (`checkBalanceForSwap`'s deadline check, analogous to `OracleModule`'s independent staleness check), while a second, later-executed layer (`VerifyExecutable`/`IsExecutable`, analogous to `StableModule`'s `_getMaxAge`-derived value) omits or fails to re-apply that same bound before committing to an irreversible action.

Because a transaction can sit in the pool (subject to `PendingTimeout`/`QueueTimeout` of 10 seconds each, see [4](#0-3) ) and pool promotion (`IsReady`) also does not re-check the deadline, by the time `ExtractTxBundles`/`VerifyExecutable` runs during block assembly, the swap's deadline may have already elapsed relative to the block being built. `VerifyExecutable` will still return success, so the module unconditionally generates and signs a `LendTx` from the node's own key (`GetLendTxGenerator`, [5](#0-4) ) that pays out `lendAmount(approveTxOrNil, swapTx)` in KAIA to the sender.

### Impact Explanation
The `LendTx`, `ApproveTx`, and `SwapTx` are non-atomic separate transactions bundled together (per [6](#0-5) ). If the on-chain `GaslessSwapRouter.swapForGas(...)` call independently enforces the `deadline` parameter (as its signature strongly implies, see the ABI in [7](#0-6) ) and reverts due to an expired deadline, the `SwapTx` fails on-chain while the preceding `LendTx` has already unconditionally transferred KAIA value to the sender. Since repayment (`repayAmount`) is only enforced as a value that must be declared correctly inside the `SwapTx` call (SP4 check in `VerifyExecutable`), and that call itself reverts, the block proposer/lender never recovers the lent KAIA. This is a concrete unauthorized value movement / fee-delegation abuse: an attacker can craft a swap with a deadline that is on the edge of expiry (or race block timing) to receive the lent KAIA fee without completing (or repaying) the swap.

### Likelihood Explanation
This is reachable by any unprivileged transaction sender who can submit a normal `ApproveTx`/`SwapTx` pair with a `Deadline` chosen to expire between pool admission and block building. Given `PendingTimeout`/`QueueTimeout` of 10 seconds and normal network/mempool latency, timing a deadline to lapse during that window is straightforward and repeatable, making likelihood non-trivial. It requires no special privileges — only that the node running the affected client has the `gasless` module enabled (default is enabled per `DefaultGaslessConfig`, [8](#0-7) ).

### Recommendation
Add an explicit deadline check inside `VerifyExecutable` (or `IsExecutable`) in `kaiax/gasless/impl/getter.go`, comparing `swapArgs.Deadline` against the current block's timestamp (the block being built, not the pool-admission-time block), mirroring the check already present in `checkBalanceForSwap`. This ensures the block-building/bundling decision path enforces the same staleness bound as the pool-admission path, so an expired-deadline swap is never bundled with (and never triggers) a `LendTx` payout.

### Proof of Concept
1. Attacker submits `ApproveTx` (nonce N) and `SwapTx` (nonce N+1) with `Deadline = now + 11s` (just above `checkBalanceForSwap`'s momentary pool-admission check, but designed to expire before block assembly).
2. Both pass `checkBalanceForSwap`'s deadline check at admission time (deadline still valid) and are added/promoted per `IsReady` in [9](#0-8) , which does not re-check the deadline.
3. Roughly ~10+ seconds later (within `PendingTimeout`), the block proposer calls `ExtractTxBundles`, which calls `IsExecutable`/`VerifyExecutable` — deadline is now expired but is never checked, so the bundle `[LendTxGenerator, ApproveTx, SwapTx]` is still generated and included.
4. `LendTx` executes, transferring `lendAmount` KAIA to the attacker's address.
5. `SwapTx` calls `GaslessSwapRouter.swapForGas(...)` on-chain, which reverts due to the expired deadline (assuming router-side deadline enforcement).
6. Attacker retains the lent KAIA with no repayment, and no on-chain penalty exists for the sender since the `SwapTx` revert does not roll back the already-mined `LendTx`.

**Caveat / uncertainty**: I was unable to fully inspect the on-chain `GaslessSwapRouter` contract's `swapForGas` implementation (not present or not indexed in this codebase snapshot) to confirm it independently reverts on expired deadlines, and could not verify whether `work/builder`'s bundle execution model might roll back the whole bundle (including `LendTx`) if a later tx in the bundle reverts. If bundle execution is atomic/all-or-nothing at the block-building layer, this would mitigate the value-loss impact, though the missing deadline check in `VerifyExecutable` (an inconsistency with `checkBalanceForSwap`) is confirmed by direct code inspection regardless.

### Citations

**File:** kaiax/gasless/impl/tx_pool.go (L32-36)
```go
var (
	QueueTimeout   = 10 * time.Second // bundle tx is removed from txpool.queue after QueueTimeout
	PendingTimeout = 10 * time.Second // bundle tx is removed from txpool.pending after PendingTimeout
	KnownTxTimeout = 30 * time.Second // bundle tx is removed from knownTxs after KnownTxTimeout
)
```

**File:** kaiax/gasless/impl/tx_pool.go (L175-179)
```go
	// tx.deadline >= currentTimestamp
	deadline := swapArgs.Deadline
	if deadline.Cmp(g.Chain.CurrentBlock().Time()) < 0 {
		return fmt.Errorf("insufficient deadline: deadline=%s, want=%s", deadline.String(), g.Chain.CurrentBlock().Time().String())
	}
```

**File:** kaiax/gasless/impl/tx_pool.go (L184-230)
```go
// Check promotion condition and enforce pending pool flow control.
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

**File:** kaiax/gasless/impl/getter.go (L40-41)
```go
	// function swapForGas(address token, uint256 amountIn, uint256 minAmountOut, uint256 amountRepay, uint256 deadline) external
	routerAbiJson = `[{"inputs":[{"internalType":"address","name":"token","type":"address"},{"internalType":"uint256","name":"amountIn","type":"uint256"},{"internalType":"uint256","name":"minAmountOut","type":"uint256"},{"internalType":"uint256","name":"amountRepay","type":"uint256"},{"internalType":"uint256","name":"deadline","type":"uint256"}],"name":"swapForGas","outputs":[],"stateMutability":"nonpayable","type":"function"}]`
```

**File:** kaiax/gasless/impl/getter.go (L214-266)
```go
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

**File:** kaiax/gasless/impl/getter.go (L273-313)
```go
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

**File:** kaiax/gasless/README.md (L29-36)
```markdown
### Block building rules

Upon detection of GaslessTxs, the following logics are executed:

- Per sender, if exists, GaslessApproveTx is relocated before GaslessSwapTx.
- LendTxGenerator is prepended before GaslessApproveTx.
- A new bundle is generated which contain either `[LendTxGenerator, GaslessApproveTx, GaslessSwapTx]` or `[LendTxGenerator, GaslessSwapTx]`
- If the bundle has conflict with any previous bundles, it is excluded from the returned bundle list.
```

**File:** kaiax/gasless/config.go (L80-88)
```go
func DefaultGaslessConfig() *GaslessConfig {
	return &GaslessConfig{
		AllowedTokens:         nil,
		Disable:               false,
		MaxBundleTxsInPending: 100,
		MaxBundleTxsInQueue:   200,
		BalanceCheckLevel:     BalanceCheckLevelAll,
	}
}
```
