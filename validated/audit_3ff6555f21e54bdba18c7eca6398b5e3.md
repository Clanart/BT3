### Title
Owner of `GaslessSwapRouter` can add/remove `allowedTokens` with no timelock, causing kaiax `gasless` module to build unfundable/failing lend bundles - (File: kaiax/gasless/impl/getter.go)

### Summary
The kaiax `gasless` module caches an allowlist of tokens (`g.allowedTokens`) and a `swapRouter` address that are read directly from the on-chain `GaslessSwapRouter` (KIP-247) contract every block via `updateAddresses`, which is driven by the contract owner's `addToken`/`removeToken` calls with no timelock or delay, exactly analogous to the reported `allowedAsset`/`allowedCollection` issue in the referenced finding.

### Finding Description
`GaslessModule.updateAddresses` refreshes `g.swapRouter` and `g.allowedTokens` from the `GaslessSwapRouter` contract state at the *parent* block header on every block insertion: [1](#0-0) [2](#0-1) 

The contract itself exposes unrestricted, immediate `AddToken`/`RemoveToken` mutator functions to its `owner` (bound in `kip247.GaslessSwapRouter`), with no timelock, delay, or pending/approval step: [3](#0-2) 

These cached `g.allowedTokens`/`g.swapRouter` values gate whether a submitted `GaslessApproveTx`/`GaslessSwapTx` pair is considered valid gasless traffic: `isApproveTx`/`isSwapTx` check `g.allowedTokens[token]` and `g.swapRouter`, and `VerifyExecutable`/`IsExecutable` (used by `ExtractTxBundles` at block-building time) depend on these checks: [4](#0-3) [5](#0-4) [6](#0-5) 

The critical timing gap: the module's cached `allowedTokens`/`swapRouter` used during pool admission/readiness checks and `ExtractTxBundles` reflect the state as of the *previous* block (updated in `PostInsertBlock` after that block is finalized), while the actual `GaslessSwapRouter.swapForGas` execution happens in the *current*, in-progress block. If the owner submits `removeToken(token)` (or changes the registered GSR address via the Registry) in the same block being built, or the block immediately preceding it, a `GaslessApproveTx`/`GaslessSwapTx` pair that was accepted by the node as valid (based on stale allowlist state) and had its `LendTxGenerator` prepended — sending the proposer's real KAIA to the user via `MakeLendTx` (L1-L4) — can then have its on-chain `swapForGas` call revert on-chain because the token is no longer allowed by the contract at execution time, since token support is also enforced inside the contract itself.

Because the `LendTx` unconditionally transfers KAIA value to the user (`LendTx.value = LendAmount(...)`, using `types.TxValueKeyAmount: lendAmount(...)`) before/independently of whether the subsequent swap succeeds: [7](#0-6) 

...an owner (or anyone who can influence the owner key, or the owner acting maliciously/compromised) can weaponize the lack of a timelock on token allowlisting to desynchronize the module's stale allowlist from the contract's live allowlist, causing the proposer-funded `LendTx` to be spent while the compensating `SwapTx` reverts on-chain (no on-chain repayment), i.e., a fee-delegation-style value loss for the block proposer/protocol.

### Impact Explanation
This is a Medium-severity centralization issue matching the reported bug class: an owner-controlled allowlist can be flipped instantly and reach a public, permissionless transaction-processing pipeline (gasless swap admission and bundling), causing either (a) denial-of-service of previously valid `GaslessApproveTx`/`GaslessSwapTx` pairs already queued by ordinary users, and (b) in the race-condition scenario above, uncompensated value transfer from the proposer via the generated `LendTx` when the on-chain state diverges from the module's one-block-stale cache. There is no delay mechanism (timelock) that would let node operators/users react to or anticipate an allowlist change, unlike best practice for asset/collection allowlists.

### Likelihood Explanation
Reachable purely by a public transaction: the `GaslessSwapRouter` owner (or anyone with access to that key) can call `removeToken`/`addToken` in any block, and normal gasless users independently and permissionlessly submit `GaslessApproveTx`/`GaslessSwapTx` pairs. No consensus, validator, or p2p compromise is required — only an ordinary transaction from the router owner combined with ordinary pending gasless transactions from unrelated users. The one-block staleness of `g.allowedTokens` (updated in `PostInsertBlock` using the parent header) versus the router's live, immediately-effective state makes the race condition deterministic for at least a one-block window on every allowlist change.

### Recommendation
Add a timelock (e.g., a `pendingToken`/effective-after-N-blocks queue) to `GaslessSwapRouter.addToken`/`removeToken`, similar to the recommended pattern in the referenced finding (`pendingAsset`/`setAllowedAssetTimestamp` + `approveAllowedAsset`), so that allowlist changes only become contract-enforced after a delay that is at least as long as the kaiax `gasless` module's one-block cache lag. Additionally, consider having the kaiax `gasless` module re-validate `isTokenSupported`/`swapRouter` against the *current* (not parent-block) state immediately before prepending `LendTxGenerator`, or making `LendTx` conditional/bundled such that it cannot be finalized independently of a successful `SwapTx`.

### Proof of Concept
1. GSR owner calls `RemoveToken(T)` in block N (`GaslessSwapRouter.RemoveToken`, `contracts/bindings/kip247/GaslessSwapRouter.go:512-524`).
2. Prior to block N's processing, a user's `GaslessApproveTx`(T)/`GaslessSwapTx`(T) pair was already validated and pending, based on the node's `g.allowedTokens` cache populated from block N-1's state (`getter.go:315-341`, `execution.go:26-33`), where T was still allowed.
3. During block N (or N+1, depending on exact timing of `PostInsertBlock` vs. block building), `ExtractTxBundles` (`builder.go:28-72`) still sees T as allowed in `g.allowedTokens` (stale by one block) and successfully calls `g.IsExecutable`/`VerifyExecutable`, prepending `GetLendTxGenerator(approveTx, swapTx)` which creates and signs a real `LendTx` sending KAIA from the proposer to the user (`getter.go:268-313`).
4. The bundle `[LendTx, ApproveTx, SwapTx]` is included in block N; the `LendTx` succeeds (a plain value transfer, unconditional on later txs). The subsequent `SwapTx` calls `GaslessSwapRouter.swapForGas`, which now reverts on-chain because token T was already removed by the owner's `RemoveToken(T)` transaction earlier in the same block (or the previous block), leaving no repayment to the proposer for the value already lent.

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

**File:** kaiax/gasless/impl/getter.go (L315-341)
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
```

**File:** contracts/bindings/kip247/GaslessSwapRouter.go (L470-531)
```go
// AddToken is a paid mutator transaction binding the contract method 0xc6e85b3b.
//
// Solidity: function addToken(address token, address factory, address router) returns()
func (_GaslessSwapRouter *GaslessSwapRouterTransactor) AddToken(opts *bind.TransactOpts, token common.Address, factory common.Address, router common.Address) (*types.Transaction, error) {
	return _GaslessSwapRouter.contract.Transact(opts, "addToken", token, factory, router)
}

// AddToken is a paid mutator transaction binding the contract method 0xc6e85b3b.
//
// Solidity: function addToken(address token, address factory, address router) returns()
func (_GaslessSwapRouter *GaslessSwapRouterSession) AddToken(token common.Address, factory common.Address, router common.Address) (*types.Transaction, error) {
	return _GaslessSwapRouter.Contract.AddToken(&_GaslessSwapRouter.TransactOpts, token, factory, router)
}

// AddToken is a paid mutator transaction binding the contract method 0xc6e85b3b.
//
// Solidity: function addToken(address token, address factory, address router) returns()
func (_GaslessSwapRouter *GaslessSwapRouterTransactorSession) AddToken(token common.Address, factory common.Address, router common.Address) (*types.Transaction, error) {
	return _GaslessSwapRouter.Contract.AddToken(&_GaslessSwapRouter.TransactOpts, token, factory, router)
}

// ClaimCommission is a paid mutator transaction binding the contract method 0xfad99f98.
//
// Solidity: function claimCommission() returns()
func (_GaslessSwapRouter *GaslessSwapRouterTransactor) ClaimCommission(opts *bind.TransactOpts) (*types.Transaction, error) {
	return _GaslessSwapRouter.contract.Transact(opts, "claimCommission")
}

// ClaimCommission is a paid mutator transaction binding the contract method 0xfad99f98.
//
// Solidity: function claimCommission() returns()
func (_GaslessSwapRouter *GaslessSwapRouterSession) ClaimCommission() (*types.Transaction, error) {
	return _GaslessSwapRouter.Contract.ClaimCommission(&_GaslessSwapRouter.TransactOpts)
}

// ClaimCommission is a paid mutator transaction binding the contract method 0xfad99f98.
//
// Solidity: function claimCommission() returns()
func (_GaslessSwapRouter *GaslessSwapRouterTransactorSession) ClaimCommission() (*types.Transaction, error) {
	return _GaslessSwapRouter.Contract.ClaimCommission(&_GaslessSwapRouter.TransactOpts)
}

// RemoveToken is a paid mutator transaction binding the contract method 0x5fa7b584.
//
// Solidity: function removeToken(address token) returns()
func (_GaslessSwapRouter *GaslessSwapRouterTransactor) RemoveToken(opts *bind.TransactOpts, token common.Address) (*types.Transaction, error) {
	return _GaslessSwapRouter.contract.Transact(opts, "removeToken", token)
}

// RemoveToken is a paid mutator transaction binding the contract method 0x5fa7b584.
//
// Solidity: function removeToken(address token) returns()
func (_GaslessSwapRouter *GaslessSwapRouterSession) RemoveToken(token common.Address) (*types.Transaction, error) {
	return _GaslessSwapRouter.Contract.RemoveToken(&_GaslessSwapRouter.TransactOpts, token)
}

// RemoveToken is a paid mutator transaction binding the contract method 0x5fa7b584.
//
// Solidity: function removeToken(address token) returns()
func (_GaslessSwapRouter *GaslessSwapRouterTransactorSession) RemoveToken(token common.Address) (*types.Transaction, error) {
	return _GaslessSwapRouter.Contract.RemoveToken(&_GaslessSwapRouter.TransactOpts, token)
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
