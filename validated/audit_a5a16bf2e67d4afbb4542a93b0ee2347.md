### Title
Gasless-tx lending fee can be permanently lost if the paired SwapTx reverts after `LendTx` already transferred KAIA to the sender - (File: kaiax/gasless/impl/getter.go)

### Summary
The gasless (KIP-247) flow lends KAIA to a sender via a synthetic `LendTx` and expects the sender's own `SwapTx` call to `GaslessSwapRouter.swapForGas` to repay that exact amount. The mechanism assumes the swap call will not revert; if it does (e.g., a faulty/adversarial ERC20 token, DEX slippage, or an arithmetic revert inside the router — the same "should never revert" assumption flagged in the referenced report about `LocalAsset(xc20).mint`), the already-lent KAIA is not recovered, and repeated attempts do not fix the underlying condition.

### Finding Description
`GetLendTxGenerator` unconditionally builds and signs a `LendTx` that transfers `lendAmount(approveTxOrNil, swapTx)` KAIA from the proposer's node key to the sender **before** the swap executes, based purely on the values declared in the (unexecuted) `SwapTx`: [1](#0-0) 

The corresponding repay amount is computed statically from tx fields, with no dependency on whether the swap actually succeeds: [2](#0-1) 

The pre-admission checks (`VerifyExecutable`) only validate the *declared* relationship between `ApproveTx`/`SwapTx`, not that the swap will successfully execute and repay: [3](#0-2) 

`LendTx` and `SwapTx` are separate, independently-executing top-level transactions combined into a `Bundle`. The `Bundle`/`TxOrGen` machinery in `work/builder/bundle.go` only implements *conflict detection* between bundles (`IsConflict`) — it contains no logic proving that if one member transaction reverts, the entire bundle (including the already-broadcast `LendTx`) is rolled back or excluded atomically: [4](#0-3) 

Because `swapForGas` performs a real token swap (via `GaslessSwapRouter`/DEX router) with a `minAmountOut` check and a deadline, it can legitimately revert for reasons outside the sender's control (price movement between validation and inclusion, DEX router underflow/overflow, or a malicious/nonstandard ERC20 token behaving like the `XC20Wrapper.mint` case that reverts instead of returning `false`). In that scenario, exactly as in the referenced report, the code path assumes success and has no compensating/try-catch or refund mechanism for the KAIA already lent by the proposer via `LendTx`.

### Impact Explanation
If the `SwapTx` reverts after `LendTx` has already been included/executed in the block, the proposer's lent KAIA is transferred to the sender but never repaid — the sender keeps the gas funding without paying it back, and the block proposer/system that generates `LendTx` unrecoverably loses that value. Because the failure condition (adversarial or reverting ERC20/router behavior) can be deterministically reproduced, the sender or a colluding token deployer can repeat this to drain lent KAIA on every gasless attempt, which is a concrete unauthorized value transfer / fee-delegation abuse matching the report's bug class ("assumes no revert can happen, but revert is reachable").

### Likelihood Explanation
Reachable by any unprivileged gasless-swap user: an attacker only needs to get an ERC20 token accepted by `allowedTokens` (or use one already allowed) whose behavior causes `swapForGas` to revert deterministically (e.g., transfer-tax tokens, tokens with hookable callbacks, or tokens causing overflow/underflow in the router's accounting) while still passing the pool-admission `checkBalanceForSwap` checks, since those checks call `GetAmountIn`/`Allowance`/`BalanceOf` at admission time but the real swap executes later against router/DEX state that can differ (slippage, `minAmountOut`, deadline) or embed the same non-standard-revert flaw described in the referenced finding.

### Recommendation
Ensure the `LendTx`/`SwapTx` pair is executed with true atomicity at the block-building/bundle-execution layer (i.e., if `SwapTx` fails, `LendTx` must also be excluded from the block, not merely "not conflicting"), and/or have `GaslessSwapRouter.swapForGas` use a safe/try-catch pattern around external token calls (mirroring the `_safeMint`-style mitigation used for `XC20Wrapper`) so that failure is reported deterministically without side effects, combined with strict re-validation of swap outcome before finalizing `LendTx` inclusion.

### Proof of Concept
Could not be fully constructed/verified within this session: confirming exploitability requires inspecting the block-building/worker code that consumes `Bundle`s (outside `kaiax/gasless` and `work/builder/bundle.go`) to determine whether `LendTx` inclusion is actually conditioned on successful `SwapTx` execution in the same block. This code path was not located within the available tool budget, so the atomicity guarantee (or its absence) is unverified — this is the key uncertainty in this analog and should be checked directly in the worker/miner bundle-application logic before treating this as confirmed.

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

**File:** work/builder/bundle.go (L73-103)
```go
// IsConflict checks if newBundle conflicts with current bundle.
func (b *Bundle) IsConflict(newBundle *Bundle) bool {
	// 1. Check for same target tx hash and both are required
	// If both are required, it discards the new bundle.
	if b.TargetTxHash == newBundle.TargetTxHash && b.TargetRequired && newBundle.TargetRequired {
		return true
	}

	// 2-1. Empty bundleTxs does not conflict with other transactions
	if len(b.BundleTxs) == 0 {
		return false
	}

	// 2-2. Check for overlapping txs
	if slices.ContainsFunc(newBundle.BundleTxs, b.Has) {
		return true
	}

	// 2-3. Check for TargetTxHash breaking current bundle.
	// If newBundle.TargetTxHash is equal to the last tx of current bundle, it is NOT a conflict.
	// Check both direction to guarantee symmetry.
	// e.g.) b.txs = [0x1, 0x2] and newBundle's TargetTxHash is 0x2.
	if idx := b.FindIdx(newBundle.TargetTxHash); idx != -1 && idx != len(b.BundleTxs)-1 {
		return true
	}
	if idx := newBundle.FindIdx(b.TargetTxHash); idx != -1 && idx != len(newBundle.BundleTxs)-1 {
		return true
	}

	return false
}
```
