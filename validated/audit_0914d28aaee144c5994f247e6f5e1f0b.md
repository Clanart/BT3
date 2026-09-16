### Title
Gasless module's `disable` safety flag is not enforced in the block-building path - ([File: kaiax/gasless/impl/builder.go])

### Summary
The Kaia gasless module implements a `Disable`/`IsDisabled()` gate that is meant to halt gasless-lending activity (e.g., when the consensus node's balance drops below `GaslessLenderMinBal`), but this gate is only checked in the debug RPC surface, not in the actual transaction-bundling logic that spends the node's own funds. This mirrors the reported "inconsistent pause/unpause modifier coverage" bug class: one privileged safety switch, but only some of the reachable entry points respect it.

### Finding Description
`GaslessModule.Init` sets `GaslessConfig.Disable = true` when the consensus node's balance is below `GaslessLenderMinBal`, intending to stop the node from acting as a lender when it can't safely absorb the risk: [1](#0-0) 

The only place `IsDisabled()` is actually consulted is the informational/validation RPC API surface (`GaslessInfo`, and indirectly `IsGaslessTx`'s validation path): [2](#0-1) 

However, the function that actually builds the bundle and creates the lending transaction paid from the node's own key — `ExtractTxBundles`, which is invoked by the block builder every block to detect and bundle approve/swap transactions with a generated `LendTx` — never calls `g.IsDisabled()`: [3](#0-2) 

The `LendTxGenerator` created here signs and sends value from the node's own `NodeKey`: [4](#0-3) 

A search across the codebase confirms `IsDisabled` is referenced only in `node/cn/backend.go`, `kaiax/gasless/impl/api.go`, `kaiax/gasless/impl/init.go` (and their tests) and the equivalent auction files — it is never checked inside `work/builder/builder.go` or `kaiax/gasless/impl/builder.go`, i.e., the actual per-block bundle-extraction/tx-inclusion path that performs the value movement.

By contrast, the auction module's equivalent safety flag (`bidPool.running`, gated by `AuctionModule.PostInsertBlock`) is properly checked in the bundling path itself: [5](#0-4) 

This shows the gasless module's design intent (checking disablement before performing value-moving actions) is followed for the auction module but is missing for gasless's `ExtractTxBundles`.

### Impact Explanation
An unprivileged gasless user submitting a normal approve+swap transaction pair can still trigger the node to generate and broadcast a `LendTx` funded from the consensus node's own balance, even after the module has flagged itself `Disable=true` due to insufficient balance. This defeats the purpose of the safety check and can lead to further balance depletion of the CN, undermining fee-delegation/gasless settlement integrity precisely in the low-balance scenario the flag was designed to prevent.

### Likelihood Explanation
Reachable from a single pending transaction bundle submitted by any ordinary account (no special privilege needed) — the attacker only needs to submit a valid approve+swap sequence while the node is in a low-balance/disabled state. The condition (low CN balance) that triggers `Disable=true` is exactly the situation where further unrestrained lending is most damaging, making exploitation plausible whenever a node nears its `GaslessLenderMinBal` threshold.

### Recommendation
Add an explicit `IsDisabled()` check at the start of `GaslessModule.ExtractTxBundles` (and consider re-checking balance dynamically, not only at `Init`), mirroring the pattern used by `AuctionModule.ExtractTxBundles`, so that no `LendTx` bundle is generated once the module is (or becomes) disabled.

### Proof of Concept
1. Start a consensus node whose balance is at/near `GaslessLenderMinBal`, causing `GaslessModule.Init` to set `GaslessConfig.Disable = true` (see `kaiax/gasless/impl/init.go:87-95`).
2. As any ordinary account, submit a valid `approve` + `swapForGas` transaction pair matching `IsApproveTx`/`IsSwapTx`/`IsExecutable` in `kaiax/gasless/impl/getter.go`.
3. During block building, `ExtractTxBundles` (`kaiax/gasless/impl/builder.go:28-72`) is invoked without any `IsDisabled()` guard, so it still calls `GetLendTxGenerator`, producing and signing a `LendTx` from the node's `NodeKey` that transfers value to the swap sender — despite the module being marked disabled.

### Citations

**File:** kaiax/gasless/impl/init.go (L87-95)
```go
	// Disable module if CN (lender) does not have sufficient balance
	if g.NodeType == common.CONSENSUSNODE {
		nodeAddr := crypto.PubkeyToAddress(opts.NodeKey.PublicKey)
		balance := g.getCurrentStateBalance(nodeAddr)
		if balance.Cmp(GaslessLenderMinBal) < 0 {
			g.GaslessConfig.Disable = true
			logger.Warn("disabling gasless module due to insufficient balance", "node", nodeAddr.Hex(), "balance", balance.String())
		}
	}
```

**File:** kaiax/gasless/impl/api.go (L128-148)
```go
type GaslessInfoResult struct {
	IsDisabled    bool             `json:"isDisabled"`
	SwapRouter    common.Address   `json:"swapRouter"`
	AllowedTokens []common.Address `json:"allowedTokens"`
	MaxBundleTxs  uint             `json:"maxBundleTxs"`
}

func (s *GaslessAPI) GaslessInfo() *GaslessInfoResult {
	s.b.gaslessInfoMu.RLock()
	defer s.b.gaslessInfoMu.RUnlock()

	at := []common.Address{}
	for addr := range s.b.allowedTokens {
		at = append(at, addr)
	}
	return &GaslessInfoResult{
		IsDisabled:    s.b.IsDisabled(),
		SwapRouter:    s.b.swapRouter,
		AllowedTokens: at,
		MaxBundleTxs:  s.b.GetMaxBundleTxsInPending(),
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

**File:** kaiax/auction/impl/builder.go (L31-36)
```go
func (a *AuctionModule) ExtractTxBundles(txs []*types.Transaction, prevBundles []*builder.Bundle) []*builder.Bundle {
	bundles := []*builder.Bundle{}
	curBlock := a.Chain.CurrentBlock()
	if curBlock == nil || atomic.LoadUint32(&a.bidPool.running) == 0 {
		return bundles
	}
```
