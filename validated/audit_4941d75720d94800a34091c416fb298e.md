### Title
Attacker can DOS the gasless bundle-tx queue by flooding it with worthless-value swap bundles - (File: kaiax/gasless/impl/tx_pool.go)

### Summary
The `gasless` module admits any transaction into its bounded local bundle-tx queue as long as it passes cheap, value-agnostic checks (whitelisted token + nonzero token balance), with no cost in native gas since the sender never needs to hold KAIA. An attacker holding a trivial (even "worthless") amount of an allowed token can repeatedly submit approve/swap bundles that consume the fixed-size `MaxBundleTxsInQueue`/`MaxBundleTxsInPending` capacity, blocking legitimate gasless users' bundle transactions from ever being admitted or promoted — the same "fill the limited slot with worthless value to DOS legitimate users" pattern as the referenced OpenQ bounty-token-limit bug.

### Finding Description
`PreAddTx` rejects a new bundle tx once the local queue reaches the configured cap: [1](#0-0) 

The cap comes from `GaslessConfig.MaxBundleTxsInQueue`/`MaxBundleTxsInPending`, which default to 200/100 respectively (not unlimited) unless the operator explicitly disables the limit: [2](#0-1) [3](#0-2) 

Whether a transaction qualifies as a bundle tx (`IsApproveTx`/`IsSwapTx`) only requires the token to be in the on-chain allowed-token set and the counterparty (spender/router) to match the whitelisted `SwapRouter` — there is no minimum trade size or value requirement: [4](#0-3) 

The balance checks performed before admission (`checkBalanceForApprove`/`checkBalanceForSwap`) only require the token balance to be strictly greater than zero (or, at higher check levels, `>= amountIn`, which the attacker can set arbitrarily small): [5](#0-4) 

Critically, none of these checks require the sender to hold any native KAIA balance — the entire point of the gasless module is that the proposer sponsors gas via a `LendTx` that is repaid from the swap output: [6](#0-5) 

Once the queue count reaches `MaxBundleTxsInQueue`, every subsequent legitimate bundle tx is rejected outright with `ErrBundleTxQueueFull`, and `IsReady` additionally throttles promotion into `pending` based on `MaxBundleTxsInPending`: [7](#0-6) 

An attacker can therefore mint/acquire a negligible (e.g., wei-level) balance of any single whitelisted token, and generate large numbers of near-zero-value approve+swap bundle pairs from many throwaway accounts (each only needing balance > 0, no KAIA needed), broadcasting them to fill the queue on every node in the network to its configured cap.

### Impact Explanation
Once the queue/pending slots are saturated with worthless spam bundles, genuine gasless users cannot get their approve/swap bundles admitted (`ErrBundleTxQueueFull`) or promoted (`IsReady` denial via `maxBundleTxsInPending`), effectively denying the gasless (fee-delegated) service to legitimate users network-wide for as long as the attacker refreshes the spam faster than the 10-second `QueueTimeout`/`PendingTimeout` eviction: [8](#0-7) 
This is a denial-of-service on the gasless sponsorship pathway reachable by any unprivileged, gasless-eligible transaction sender, matching the "Medium" DOS-by-flooding-a-bounded-registry-with-worthless-entries class from the referenced report.

### Likelihood Explanation
Likelihood is moderate-to-high: the attack requires no privileged role, no consensus/network-message manipulation, and no meaningful capital — only owning any nonzero amount of a single whitelisted token and the ability to sign many transactions from disposable accounts, since gas for the spam bundles is effectively subsidized by the gasless/lend mechanism itself. The default caps (100 pending / 200 queue) are small enough to be filled cheaply; operators can raise or disable the cap (`math.MaxUint64`), but the vulnerable default configuration is shipped in `DefaultGaslessConfig`.

### Recommendation
- Require a minimum economically meaningful `amountIn`/`amountOut` (or a minimum USD-equivalent value threshold) for a tx to be treated as a bundle tx eligible for gasless queue admission, rather than only checking `balance > 0`.
- Apply a per-sender or per-token cap on concurrent queued/pending bundle slots so a single actor (or a set of related worthless-token holders) cannot exhaust the entire shared queue.
- Consider requiring the swap `amountIn` to exceed a configurable dust threshold before `IsApproveTx`/`IsSwapTx` treats the transaction as a gasless-eligible bundle.

### Proof of Concept
1. Attacker acquires a negligible balance (e.g., `1` unit) of any token present in `g.allowedTokens` (`kaiax/gasless/impl/getter.go:315-341`).
2. Attacker generates `N` disposable accounts, each with the tiny token balance, and for each crafts an `approve(spender=swapRouter, amount=MaxUint256)` tx followed by a `swapForGas(token, amountIn=1, minAmountOut=0, amountRepay=0, deadline=far-future)` tx — both pass `isApproveTx`/`isSwapTx` and `checkBalanceForApprove`/`checkBalanceForSwap` (`kaiax/gasless/impl/tx_pool.go:74-182`) without requiring any native KAIA balance.
3. Attacker broadcasts these bundles to the network faster than the 10s `QueueTimeout`, driving `g.knownTxs.numQueue()` up to `MaxBundleTxsInQueue` (default 200) (`kaiax/gasless/impl/tx_pool.go:38-53`, `kaiax/gasless/config.go:48-54`).
4. Any legitimate user's genuine gasless bundle tx submitted after this point is rejected with `ErrBundleTxQueueFull`, or fails to be promoted to `pending` due to the `MaxBundleTxsInPending` check in `IsReady` (`kaiax/gasless/impl/tx_pool.go:184-230`), denying gasless service network-wide while the attacker sustains the spam.

### Citations

**File:** kaiax/gasless/impl/tx_pool.go (L38-53)
```go
func (g *GaslessModule) PreAddTx(tx *types.Transaction, local bool) error {
	g.knownTxsMu.RLock()
	defer g.knownTxsMu.RUnlock()

	if knownTx, ok := g.knownTxs.get(tx.Hash()); ok && knownTx.elapsedPromotedOrAddedTime() < KnownTxTimeout {
		return ErrUnableToAddKnownBundleTx
	}

	if g.IsBundleTx(tx) {
		if uint(g.knownTxs.numQueue()) >= g.GetMaxBundleTxsInQueue() {
			return ErrBundleTxQueueFull
		}
		g.knownTxs.add(tx, TxStatusQueue)
	}
	return nil
}
```

**File:** kaiax/gasless/impl/tx_pool.go (L74-100)
```go
func (g *GaslessModule) checkBalanceForApprove(approveArgs *ApproveArgs) error {
	token := approveArgs.Token
	bc := backends.NewBlockchainContractBackend(g.Chain, nil, nil)

	if g.GaslessConfig.ShouldCheckSenderCode() {
		if g.getCurrentHasCode(approveArgs.Sender) {
			return errors.New("sender with code is not allowed")
		}
	}

	if g.GaslessConfig.ShouldCheckToken() {
		tokenContract, err := sc_erc20.NewERC20(token, bc)
		if err != nil {
			return err
		}

		// tx.token.balanceOf(sender) > 0
		tokenBalance, err := tokenContract.BalanceOf(nil, approveArgs.Sender)
		if err != nil {
			return err
		}
		if tokenBalance.Sign() <= 0 {
			return fmt.Errorf("insufficient sender token balance: token=%s, have=%s, want=nonzero", token.Hex(), tokenBalance.String())
		}
	}
	return nil
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

**File:** kaiax/gasless/impl/tx_pool.go (L292-315)
```go
// PreReset removes timed out tx from the tx pool and knownTxs.
func (g *GaslessModule) PreReset(oldHead, newHead *types.Header) []common.Hash {
	g.knownTxsMu.Lock()
	defer g.knownTxsMu.Unlock()

	drops := make([]common.Hash, 0)

	for hash, knownTx := range *g.knownTxs {
		// remove pending timed out tx from tx pool
		if knownTx.status == TxStatusPending && knownTx.elapsedPromotedTime() >= PendingTimeout {
			drops = append(drops, hash)
		}
		// remove queue timed out tx from tx pool
		if knownTx.status == TxStatusQueue && knownTx.elapsedAddedTime() >= QueueTimeout {
			drops = append(drops, hash)
		}
		// remove known timed out tx from knownTxs
		if knownTx.elapsedPromotedOrAddedTime() >= KnownTxTimeout {
			g.knownTxs.delete(hash)
		}
	}

	return drops
}
```

**File:** kaiax/gasless/config.go (L41-54)
```go
	MaxBundleTxsInPendingFlag = &cli.IntFlag{
		Name:     "gasless.max-bundle-txs-in-pending",
		Usage:    "max number of gasless bundle txs in pending queue. Default value is 100. No limit if negative value",
		Value:    100,
		Aliases:  []string{"kaiax.module.gasless.max-bundle-txs-in-pending"},
		Category: "KAIAX",
	}
	MaxBundleTxsInQueueFlag = &cli.IntFlag{
		Name:     "gasless.max-bundle-txs-in-queue",
		Usage:    "max number of gasless bundle txs in queue. Default value is 200. No limit if negative value",
		Value:    200,
		Aliases:  []string{"kaiax.module.gasless.max-bundle-txs-in-queue"},
		Category: "KAIAX",
	}
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

**File:** kaiax/gasless/impl/getter.go (L69-103)
```go
// IsApproveTx checks following conditions:
// A1. tx.to is a whitelisted ERC20 token.
// A2. tx.data is `approve(spender, amount)`.
// A3. spender is a whitelisted SwapRouter contract.
// A4. amount is MaxUint.
func (g *GaslessModule) IsApproveTx(tx *types.Transaction) bool {
	args, ok := decodeApproveTx(tx, g.signer)
	return ok && g.isApproveTx(args)
}

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
