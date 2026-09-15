### Title
Gasless bundle execution check bypasses live token balance/allowance verification, enabling gas-lending theft from the block proposer - ([File: kaiax/gasless/impl/getter.go])

### Summary
The gasless module lends KAIA gas fees to a sender (via `LendTx`) expecting repayment through the swap proceeds of a bundled `ApproveTx`+`SwapTx`. The decision to actually build and include this lend/repay bundle is gated by `VerifyExecutable`, which — unlike the tx-pool admission check `checkBalanceForSwap`/`checkBalanceForApprove` — never verifies the sender's live ERC20 token balance or allowance. This mirrors the reported bug class: two related functions that are supposed to enforce the same protective condition (sufficient real balance) diverge, and the function that actually gates value movement (`VerifyExecutable`, used at bundling/execution time) is the one missing the protection, while the "informational"/admission-time function (`checkBalanceForSwap`) has it.

### Finding Description
`GetCheckBalance` (invoked from `TxPool.validateTx`) enforces, at tx-pool admission time, that `tx.token.balanceOf(sender) >= tx.amountIn` and `allowance(sender, router) >= amountIn`, but only when `GaslessConfig.ShouldCheckToken()` is enabled: [1](#0-0) 

However, the function that actually decides whether a bundle (`LendTx` + `ApproveTx` + `SwapTx`) is built and injected into the block — `ExtractTxBundles` → `IsExecutable` → `VerifyExecutable` — performs no balance or allowance check at all. It only validates nonce sequencing, that `approveArgs.Amount >= swapArgs.AmountIn`, and that the declared `AmountRepay` matches the deterministic `repayAmount()` computation: [2](#0-1) 

`ExtractTxBundles` calls `IsExecutable`/`VerifyExecutable` directly without re-checking live token state, then unconditionally generates the `LendTx` via `GetLendTxGenerator`, which transfers `lendAmount(approveTxOrNil, swapTx)` (the sender's expected gas fee) from the proposer to the sender: [3](#0-2) [4](#0-3) 

Because `checkBalanceForApprove`/`checkBalanceForSwap` token checks are config-gated (`ShouldCheckToken()`), and even when enabled they only run once at admission time (not re-verified at bundle/inclusion time), there is a window — analogous to the VotingEscrow inconsistency between `balanceOfNFT`, `balanceOfNFTAt`, and the unprotected `_balanceOfNFT` used by `getVotes` — where the value-affecting path (`VerifyExecutable`/`ExtractTxBundles`, which triggers the actual KAIA transfer via `LendTx`) lacks the same protection that the admission-time path has. If `ShouldCheckToken` is disabled by node configuration, or if the sender's token balance/allowance becomes insufficient between admission and block building (e.g. re-orgs, mempool propagation delay, or state changes not tied to the sender's own nonce), `VerifyExecutable` will still return "executable" purely from the tx's static fields, causing the proposer's `LendTx` to unconditionally send KAIA to the sender. The subsequent `SwapTx` (which is the sender's only means of repaying via `swapForGas`) will then revert on-chain due to insufficient balance/allowance, and the repayment never occurs, but the `LendTx` transfer is a distinct transaction that already executed successfully in the same block.

### Impact Explanation
This is fee-delegation/gasless settlement theft: the block proposer irreversibly lends KAIA (`lendAmount`) to a sender whose ability to repay (`repayAmount`) via the swap is never actually confirmed against live on-chain token state at the point that matters (bundle construction), only optionally and separately at pool admission. A sender able to exploit this window causes the proposer to lose the lent KAIA with no compensating repayment, a concrete unauthorized value movement fitting the accepted-impact categories (fee-delegation abuse / gasless settlement theft).

### Likelihood Explanation
Likelihood depends on operational conditions: if `ShouldCheckToken()` is enabled and admission-time and bundling occur essentially back-to-back with no intervening state change (which nonce sequencing largely enforces for the sender's own txs), the window is narrow. However, `ShouldCheckToken` is an explicit config toggle, and the code contains no defense-in-depth: `VerifyExecutable` itself, which is the single authoritative gate before an irreversible KAIA transfer is generated, has zero token-balance/allowance verification, making it entirely dependent on an optional, non-redundant check elsewhere. This structural inconsistency — the same class of bug the external report describes — is the root issue regardless of default configuration.

### Recommendation
`VerifyExecutable` (or `ExtractTxBundles` immediately before generating `LendTx`) should independently re-verify, against the state at which the bundle will actually execute, that `tx.token.balanceOf(sender) >= amountIn` and `allowance(sender, router) >= amountIn`, mirroring the checks in `checkBalanceForSwap`/`checkBalanceForApprove`, and this check should not be optional/config-gated for the bundling path since it directly guards an irreversible value transfer (`LendTx`).

### Proof of Concept
1. Node operator (or default config) sets `GaslessConfig.ShouldCheckToken()` to `false`, or an attacker relies on the narrow window between tx-pool admission and block building.
2. Attacker submits a valid `ApproveTx` (approving `MaxUint256` to the whitelisted swap router) followed by a `SwapTx` calling `swapForGas(token, amountIn, minAmountOut, amountRepay, deadline)` with a token balance that is (or becomes) less than `amountIn`.
3. `ExtractTxBundles` calls `IsExecutable(approveTx, swapTx)` → `VerifyExecutable`, which only checks nonce order, `approveArgs.Amount >= swapArgs.AmountIn`, and `repayAmount()` arithmetic consistency — none of which query the sender's actual token balance [5](#0-4) .
4. The bundle is accepted; `GetLendTxGenerator` creates and signs a `LendTx` transferring `lendAmount(approveTx, swapTx)` KAIA from the proposer's key to the attacker [6](#0-5) .
5. In-block execution: `LendTx` succeeds (attacker receives KAIA), but `SwapTx`'s `swapForGas` call reverts due to insufficient token balance/allowance, so no repayment is made to the proposer — net KAIA loss to the proposer.

**Note:** I was unable to fully trace how the produced `LendTx`/bundle interacts with final block-inclusion validation (e.g., whether a failed `SwapTx` causes the whole bundle, including `LendTx`, to be dropped from the block by some external bundle-atomicity mechanism not visible in the indexed files). If such atomicity is enforced elsewhere (e.g., in `work/builder`), it could mitigate or eliminate this finding, and I was unable to fully verify that mechanism from the available search results.

### Citations

**File:** kaiax/gasless/impl/tx_pool.go (L144-173)
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
