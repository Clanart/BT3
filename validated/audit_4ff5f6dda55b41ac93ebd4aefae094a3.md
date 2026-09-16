### Title
Stale per-block cache of `swapRouter`/`allowedTokens` in the gasless module allows a same-block sandwich of `GaslessSwapRouter.addToken`/`removeToken` (or a Registry `GaslessSwapRouter` address swap) to make the proposer lend gas that cannot be repaid - (File: kaiax/gasless/impl/execution.go, kaiax/gasless/impl/getter.go)

### Summary
The gasless module caches the `GaslessSwapRouter` address and its `allowedTokens` set once per block, at `PostInsertBlock` time, and uses this **stale, previous-block** snapshot to decide (a) whether a `GaslessApproveTx`/`GaslessSwapTx` pair is a valid gasless pattern and (b) whether the proposer should front the user's gas via `LendTxGenerator`. Because the cache is refreshed only after a block is inserted, any transaction inside the *same* block that changes the router's allowed-token set or the router address itself (e.g. `addToken`/`removeToken`, or a `Registry` update pointing `GaslessSwapRouter` to a new address) is invisible to the module while it is building/validating gasless bundles for that block. This mirrors the "Dangerous Edition Update" pattern: an admin-controlled parameter (`ethereum`/token-of-record) that can be changed after value has effectively been committed (minted / lent), letting a sandwiching party benefit from the stale-vs-fresh mismatch and leaving the honest party (the proposer, who has already lent gas) unbacked.

### Finding Description
`updateAddresses` is only invoked from `PostInsertBlock`, i.e., once the block has already been committed: [1](#0-0) 

It recomputes `g.swapRouter` and `g.allowedTokens` from on-chain state as of the block just inserted: [2](#0-1) 

These fields are read (under `gaslessInfoMu`) by `isApproveTx`/`isSwapTx` when the tx-pool decides which transactions are gasless, and by `checkBalanceForSwap`/`VerifyExecutable` when deciding execution readiness and computing `AmountRepay`: [3](#0-2) [4](#0-3) 

The block-building bundling logic (`ExtractTxBundles`) prepends a `LendTxGenerator` transaction — signed by the node's own key, transferring `lendAmount(approveTxOrNil, swapTx)` KAIA out of the proposer's own account to the user — in front of the `ApproveTx`/`SwapTx` pair: [5](#0-4) [6](#0-5) 

The lend amount is computed purely from the transactions' declared fees, not validated against the *current in-block* GSR/allowed-token state: [7](#0-6) 

Because `g.swapRouter`/`g.allowedTokens` are only refreshed after the block is committed, if a governance-style transaction inside the same block being built calls `GaslessSwapRouter.removeToken` (or the `Registry` swaps in a new `GaslessSwapRouter` address) *before* the bundled `[LendTx, ApproveTx, SwapTx]` executes, the module still treats the pending `SwapTx` as a legitimate gasless swap (using the stale snapshot) and instructs the proposer to lend KAIA to the sender. When the actual `GaslessSwapRouter` contract executes on-chain (enforcing its own live `allowedTokens`/router state), the swap can revert or behave differently than the module assumed, breaking the implicit invariant that `SwapTx` will repay the lender. This is directly analogous to the reported issue: a mutable "type of asset" parameter (there, `ethereum`; here, `swapRouter`/`allowedTokens`) can be changed while value has already been advanced against the old parameter value, and the change is not gated on quiescence of in-flight lend/swap pairs, nor is it validated against the same block in which the advance happens.

### Impact Explanation
If the `SwapTx` reverts or fails to repay after the `LendTx` has already unconditionally transferred KAIA from the block proposer to the user (the `LendTx` is an independent, always-successful KAIA transfer, not contingent on `SwapTx` success), the proposer permanently loses the lent KAIA. This is a genuine unauthorized value movement out of the fee-delegation/gasless-lending counterparty (the block proposer/CN), reachable purely through ordinary, unprivileged transaction submission (an `ApproveTx`+`SwapTx` pair) combined with a same-block admin transaction on `GaslessSwapRouter`/`Registry`. Because block proposers are rotated per round and stake real KAIA to lend gas (subject to `GaslessLenderMinBal`), repeated exploitation could drain proposer funds over many blocks — a Medium/High severity issue depending on how frequently token-list or router-address changes occur relative to pending gasless transactions.

### Likelihood Explanation
Exploitation requires: (1) a transaction changing `GaslessSwapRouter`'s allowed tokens or the registered router address to land in the same block as a pending gasless `ApproveTx`/`SwapTx` pair, and (2) that pair to be for the token/router being changed. `addToken`/`removeToken` calls are likely infrequent and access-controlled at the contract level, and the bundling/conflict-detection logic in `ExtractTxBundles` may incidentally reduce some overlap, but nothing in the reviewed module code explicitly re-validates the swap-router/allowed-token state against the *current* block being assembled, nor does it abort a bundle if such a change is detected within the same block. Likelihood is therefore assessed as **Medium**: it requires specific timing (an admin/governance action co-occurring with pending gasless traffic for the same token/router), which is plausible but not trivially reproducible on demand by an unprivileged actor acting alone.

### Recommendation
- Refresh `g.swapRouter`/`g.allowedTokens` (or otherwise re-validate `IsApproveTx`/`IsSwapTx`/`VerifyExecutable`) against the state *as of the point the bundle is being assembled within the current block*, not only the state from the previously inserted block.
- Make `LendTx` execution contingent on `SwapTx` success (e.g., verify atomically that the bundle will not be split, or refuse to lend if any in-block transaction is detected that mutates the `GaslessSwapRouter`'s registered tokens/address before the swap).
- Consider requiring `GaslessSwapRouter` allow-list/address changes to only take effect after a cool-down/next-epoch boundary, analogous to the recommended fix of only updating the sensitive field while related activity is paused, so no gasless bundle can straddle a stale-vs-fresh state boundary.

### Proof of Concept
Conceptual sequence within a single block being built by a proposer running the gasless module:
1. Block N-1 is committed with `g.allowedTokens = {tokenX: true}`, `g.swapRouter = GSR`.
2. A user submits `ApproveTx` (approve GSR for tokenX, max amount) and `SwapTx` (`swapForGas(tokenX, ...)`), both validated as a gasless pair using the stale block N-1 snapshot (`isApproveTx`/`isSwapTx` in `kaiax/gasless/impl/getter.go`).
3. Before these are executed, another transaction in block N calls `GaslessSwapRouter.removeToken(tokenX)` (contract method visible in `contracts/bindings/kip247/GaslessSwapRouter.go`), executing earlier in transaction order within the same block.
4. The gasless module's `ExtractTxBundles` (still relying on the pre-block snapshot) builds `[LendTxGenerator, ApproveTx, SwapTx]` and the `LendTx` unconditionally transfers `lendAmount` KAIA from the proposer to the user (`kaiax/gasless/impl/getter.go:268-312`).
5. `SwapTx` executes on-chain against the now-updated `GaslessSwapRouter` state (tokenX removed) and reverts or fails to repay the expected `AmountRepay`.
6. The proposer's lent KAIA is not recovered — a direct loss of proposer funds triggered by an ordinary user's gasless swap combined with a same-block parameter change, with no code path re-checking the router/allowed-token state at in-block execution time.

Note: I was not able to fully trace whether `builder.Bundle`/`work` guarantees strict all-or-nothing execution or ordering exclusivity for bundles containing generated transactions (`LendTxGenerator`), which would materially affect whether this scenario is fully preventable by existing conflict-detection logic; this remains unverified due to iteration limits and should be confirmed against `work/builder/builder.go` and `work/builder/bundle.go` before treating this as conclusively exploitable.

### Citations

**File:** kaiax/gasless/impl/execution.go (L26-33)
```go
func (g *GaslessModule) PostInsertBlock(block *types.Block) error {
	currentState, err := g.Chain.StateAt(block.Header().Root)
	if err != nil {
		return err
	}
	g.setCurrentState(currentState)
	return g.updateAddresses(block.Header())
}
```

**File:** kaiax/gasless/impl/getter.go (L79-103)
```go
func (g *GaslessModule) isApproveTx(args *ApproveArgs) bool {
	g.gaslessInfoMu.RLock()
	defer g.gaslessInfoMu.RUnlock()

	return g.allowedTokens[args.Token] && // A1
		g.swapRouter == args.Spender && // A3
		args.Amount.Cmp(abi.MaxUint256) == 0 // A4
}

// IsSwapTx checks following conditions:
// S1. tx.to is a whitelisted SwapRouter contract.
// S2. tx.data is `swapForGas(token, amountIn, minAmountOut, amountRepay)`.
// S3. token is a whitelisted ERC20 token.
func (g *GaslessModule) IsSwapTx(tx *types.Transaction) bool {
	args, ok := decodeSwapTx(tx, g.signer)
	return ok && g.isSwapTx(args)
}

func (g *GaslessModule) isSwapTx(args *SwapArgs) bool {
	g.gaslessInfoMu.RLock()
	defer g.gaslessInfoMu.RUnlock()

	return g.swapRouter == args.Router && // S1
		g.allowedTokens[args.Token] // S3
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

**File:** kaiax/gasless/impl/getter.go (L268-312)
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
```

**File:** kaiax/gasless/impl/getter.go (L315-344)
```go
func (g *GaslessModule) updateAddresses(header *types.Header) error {
	g.gaslessInfoMu.Lock()
	defer g.gaslessInfoMu.Unlock()

	swapRouter, tokens, err := getGaslessInfo(g.Chain, header)
	// proceed even if there is something wrong with multicall contract
	if err != nil {
		g.swapRouter = common.Address{}
		g.allowedTokens = map[common.Address]bool{}
		logger.Warn("there is something wrong with multicall contract", "err", err.Error())
		return nil
	}

	g.swapRouter = swapRouter

	g.allowedTokens = map[common.Address]bool{}
	for _, addr := range tokens {
		// all tokens are allowed if nil
		if g.GaslessConfig.AllowedTokens == nil {
			g.allowedTokens[addr] = true
		}
		for _, allowed := range g.GaslessConfig.AllowedTokens {
			if addr == allowed {
				g.allowedTokens[addr] = true
			}
		}
	}

	return nil
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
