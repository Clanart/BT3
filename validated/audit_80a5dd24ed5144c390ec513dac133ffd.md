### Title
Stale allow-list cache in the gasless module lets a token/router removal cause proposer fund loss on gasless swaps - (File: `kaiax/gasless/impl/getter.go`, `kaiax/gasless/impl/execution.go`)

### Summary
The Kaia gasless (KIP-247) module caches the `GaslessSwapRouter`'s allowed-token set and router address in memory, and this cache is refreshed only once per inserted block via `PostInsertBlock`. All gasless-tx pool admission, readiness, and bundle-building decisions are made against this stale, block-lagged cache rather than the authoritative on-chain state at execution time — analogous to the EigenLayer bug where an off-chain assumption about a whitelist's state was invalidated by an on-chain removal, causing a value-transfer to be attempted against no-longer-valid conditions.

### Finding Description
`updateAddresses` populates `g.swapRouter` / `g.allowedTokens` from the `GaslessSwapRouter` contract state and is invoked only from `PostInsertBlock`, i.e. after a block has already been fully processed and inserted: [1](#0-0) 

All admission checks (`isApproveTx`, `isSwapTx`) that decide whether a `GaslessApproveTx`/`GaslessSwapTx` is promoted in the pool and bundled for block building read this same cached map: [2](#0-1) 

The block-building bundling logic (`ExtractTxBundles`) uses these same cached checks to decide whether to prepend a `LendTxGenerator` (a real-value transfer from the block proposer to the user) ahead of the approve/swap pair: [3](#0-2) 

Because the cache is only refreshed after a block is inserted, it necessarily lags one block behind the state being assembled for the *current* block. If the `GaslessSwapRouter` owner/governance removes a token from its allow-list (or updates the router address) in a transaction that lands earlier in the same block currently being built, the gasless module's in-memory `allowedTokens`/`swapRouter` still reflect the pre-removal state for the remainder of that block's construction. The pool will still treat the pending `GaslessApproveTx`/`GaslessSwapTx` pair for that token as valid and the builder will still emit `[LendTxGenerator, GaslessApproveTx, GaslessSwapTx]`.

`GetLendTxGenerator`/`lendAmount` compute and sign a standalone `TxTypeEthereumDynamicFee` transaction that unconditionally transfers `lendAmount()` KAIA from the proposer's node key to the swap sender: [4](#0-3) [5](#0-4) 

This is a separate top-level transaction from the `GaslessSwapTx`. If the actual on-chain `swapForGas` call subsequently reverts because the router's authoritative (non-cached) state no longer permits the token — which is exactly the case that just occurred on-chain in this same block — the `LendTx` has already unconditionally transferred real KAIA out of the proposer's control, while the `SwapTx` that was supposed to repay that amount (`repayAmount`) reverts and provides no repayment.

### Impact Explanation
This results in direct, unauthorized value loss to the block proposer: KAIA is transferred via `LendTx` based on a stale validity check, but the compensating `SwapTx` that should repay the lent gas fee fails because the authoritative on-chain allow-list no longer matches the module's cached view. This is analogous to the reported EigenLayer class of bug — an assumption based on a whitelist snapshot that can be invalidated by governance/owner action before the corresponding operation executes, leading to loss of value for a party who acted on the stale assumption.

### Likelihood Explanation
This requires the `GaslessSwapRouter` owner/governance to remove a token (or change the router) in a transaction ordered before the affected gasless bundle within the same block, and requires a pending `GaslessApproveTx`/`GaslessSwapTx` for that token to already be admitted in the pool for that block. This is a narrow, block-timing-dependent window rather than a trivially-reachable single-tx exploit, and I could not fully confirm within available tool budget whether the underlying `builder.Bundle` execution model in `work/builder/bundle.go` enforces atomic all-or-nothing reversion across the `LendTxGenerator`/`ApproveTx`/`SwapTx` triplet (which would negate the fund-loss impact) — this needs verification against `work/builder/bundle.go` and the block-execution pipeline that consumes `ExtractTxBundles` output.

### Recommendation
- Refresh the router/allow-list cache using the state being built for the *current* block (or re-validate against live state immediately before including the `LendTxGenerator`) rather than relying solely on the last inserted block's snapshot.
- Ensure bundle execution is enforced atomically at the EVM/block-assembly level so that if `SwapTx` reverts, the corresponding `LendTx` is also excluded/reverted from the block, preventing proposer fund loss from stale-whitelist races.

### Proof of Concept
Due to index limitations I could not trace the exact block-assembly code that consumes `ExtractTxBundles`'s output to confirm bundle atomicity; a full PoC would require a background Devin session with repository access to construct: (1) a pending `GaslessApproveTx`/`GaslessSwapTx` pair for token T, (2) a governance/owner transaction in the same block that removes T from `GaslessSwapRouter`'s allow-list ordered before the gasless bundle, and (3) verification that the resulting block still contains a successful `LendTx` alongside a reverted `SwapTx`.

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

**File:** kaiax/gasless/impl/builder.go (L28-71)
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
```
