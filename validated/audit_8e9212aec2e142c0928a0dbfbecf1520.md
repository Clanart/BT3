### Title
Gasless lend/repay TOCTOU allows draining proposer KAIA without valid token repayment - ([File: kaiax/gasless/impl/tx_pool.go])

### Summary
The GMBL incident stemmed from a betting platform crediting users (referral bonuses) without verifying that real funds actually backed the bet. Kaia's `kaiax/gasless` module (KIP-247) has an analogous split between a one-time "can this account afford it" check and the unconditional advance of real value, creating a time-of-check/time-of-use (TOCTOU) gap that an unprivileged gasless user can exploit to receive real KAIA from the block proposer without ever completing a valid, fund-backed repayment.

### Finding Description
Gasless swaps work by having the proposer "lend" KAIA to the user via a synthetically generated `LendTx`, expecting the user's `GaslessSwapTx` to repay that amount by swapping ERC20 tokens through `GaslessSwapRouter`. The `LendTx` value transfer is unconditional and is *not* contingent on the paired `SwapTx` actually succeeding: [1](#0-0) 

The lend amount is computed purely from transaction fees, independent of whether the swap will actually complete: [2](#0-1) 

The only place that verifies the sender actually owns and has approved sufficient token balance to *back* the swap is `checkBalanceForSwap`, invoked through `GetCheckBalance()`: [3](#0-2) [4](#0-3) 

This is only called once, at transaction admission into the pool (`PreAddTx`/`GetCheckBalance`). Critically, the promotion logic (`IsReady`/`isReady`, used both to move a tx from queue to pending and to determine eligibility for block-building bundles via `ExtractTxBundles`) never re-invokes `checkBalanceForSwap`/`checkBalanceForApprove`. It only re-checks nonce sequencing and `IsExecutable` (which validates repay-amount arithmetic, not live balances): [5](#0-4) 

`ExtractTxBundles` then unconditionally prepends the `LendTxGenerator` ahead of the `ApproveTx`/`SwapTx` for any tx that passes this stale readiness check: [6](#0-5) 

Bundle members are independent transactions with no atomic rollback relationship — `Bundle` only tracks conflict/ordering metadata, not an all-or-nothing execution guarantee: [7](#0-6) 

As a result: an attacker can submit an `ApproveTx`+`SwapTx` pair while genuinely holding sufficient ERC20 balance/allowance (satisfying the one-time `checkBalanceForSwap`), get it admitted and marked ready in the pool, then drain the ERC20 token balance via a separate transfer before the block containing the bundle is built. The stale `SwapTx` remains "ready" since readiness only re-checks nonce/arithmetic, not live balance. When the block is assembled, the bundle `[LendTx, ApproveTx, SwapTx]` is included: `LendTx` unconditionally transfers real KAIA from the proposer to the attacker, while `SwapTx` reverts on-chain (the router's `transferFrom` fails due to insufficient balance) with no repayment and no clawback of the already-executed `LendTx` transfer.

### Impact Explanation
This allows an unprivileged gasless-swap user to extract real KAIA value from the block proposer's own funds without any binding repayment — directly analogous to GMBL's flaw of crediting/advancing value based on stale/unverified deposit state. Because the proposer's `NodeKey` account funds every `LendTx` (see `GetLendTxGenerator`'s value/key usage), a proposer running the gasless module can be drained repeatedly, one exploit per gasless tx (bounded by `MaxBundleTxsInPending`/`MaxBundleTxsInQueue`), which is a concrete unauthorized value movement / fee-delegation abuse.

### Likelihood Explanation
Exploitation requires precise timing: get the pair admitted while balance-check conditions are satisfied, then race a token-draining transfer into the pool before the pool's readiness re-check (which does not re-verify balance) is invalidated by a full pool reset, and before the block is built. This is a realistic but non-trivial race — feasible for a sophisticated attacker/searcher who can also submit the balance-draining transfer with correct nonce ordering and observe mempool/block-building timing, especially under `ShouldCheckToken()`/`ShouldCheckSwapAmount()` being satisfied once but not continuously enforced.

### Recommendation
Re-validate `checkBalanceForSwap`/`checkBalanceForApprove` (live token balance and allowance) immediately before bundling (`ExtractTxBundles`) or immediately before `LendTx` is generated/included, not just once at pool admission (`PreAddTx`). Alternatively, make the `LendTx` conditional on successful `SwapTx` execution (e.g., by having the router itself pull/verify funds atomically in the same call rather than relying on a pool-level pre-check that can go stale), or drop stale `SwapTx`s from "ready" status whenever a state-changing event affecting the relevant token balance/allowance is observed.

### Proof of Concept
1. Attacker holds `amountIn` of a whitelisted ERC20 token and approves `GaslessSwapRouter` for `MaxUint256` via a valid `GaslessApproveTx`.
2. Attacker submits `GaslessApproveTx` + `GaslessSwapTx(token, amountIn, minAmountOut, amountRepay, deadline)`. `GetCheckBalance()` passes because attacker genuinely has funds/allowance at admission time (`checkBalanceForSwap`, `kaiax/gasless/impl/tx_pool.go:107-182`).
3. Both txs are promoted to `pending` (`IsReady`/`isReady`, `kaiax/gasless/impl/tx_pool.go:184-290`), which never re-checks live balance.
4. Before the block containing these txs is built, attacker sends a normal `transfer()` moving the ERC20 tokens elsewhere, zeroing the balance backing the pending `SwapTx`.
5. Block builder calls `ExtractTxBundles`, which still considers the stale `SwapTx` executable and prepends `LendTx` unconditionally (`kaiax/gasless/impl/builder.go:28-72`, `getter.go:268-313`).
6. Block executes: `LendTx` unconditionally transfers real KAIA (proposer funds) to attacker; `SwapTx` reverts on-chain because `transferFrom` fails for insufficient token balance. Attacker keeps the lent KAIA with no repayment, and the proposer's balance is permanently reduced — mirroring GMBL's "place bets/receive credit without depositing real funds" bug class.

### Citations

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

**File:** kaiax/gasless/impl/tx_pool.go (L107-182)
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

	// tx.deadline >= currentTimestamp
	deadline := swapArgs.Deadline
	if deadline.Cmp(g.Chain.CurrentBlock().Time()) < 0 {
		return fmt.Errorf("insufficient deadline: deadline=%s, want=%s", deadline.String(), g.Chain.CurrentBlock().Time().String())
	}

	return nil
}
```

**File:** kaiax/gasless/impl/tx_pool.go (L184-290)
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

// Check promotion condition.
func (g *GaslessModule) isReady(txs map[uint64]*types.Transaction, i uint64, ready types.Transactions) bool {
	tx := txs[i]

	if g.IsApproveTx(tx) && i < uint64(math.MaxUint64) {
		return g.isApproveTxReady(tx, txs[i+1])
	}

	if g.IsSwapTx(tx) {
		var prevTx *types.Transaction
		if len(ready) > 0 {
			prevTx = ready[len(ready)-1]
		}
		return g.isSwapTxReady(tx, prevTx)
	}

	return false
}

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

**File:** work/builder/bundle.go (L25-103)
```go
type Bundle struct {
	// each element can be either *types.Transaction, or TxGenerator
	BundleTxs []*TxOrGen

	// BundleTxs is placed AFTER the target tx. If empty hash, it is placed at the very front.
	TargetTxHash common.Hash

	// TargetRequired is true if the bundle must be executed after the target tx
	// and only if the target tx is successfully executed.
	TargetRequired bool

	// lookup map for O(1) membership checks (lazy initialized)
	txLookup map[common.Hash]int
}

func NewBundle(txs []*TxOrGen, targetTxHash common.Hash, targetRequired bool) *Bundle {
	b := &Bundle{
		BundleTxs:      txs,
		TargetTxHash:   targetTxHash,
		TargetRequired: targetRequired,
	}
	b.buildLookup()
	return b
}

func (b *Bundle) buildLookup() {
	if b.txLookup == nil {
		b.txLookup = make(map[common.Hash]int, len(b.BundleTxs))
		for i, txOrGen := range b.BundleTxs {
			b.txLookup[txOrGen.Id] = i
		}
	}
}

// Has checks if the bundle contains a tx with the given hash.
func (b *Bundle) Has(txOrGen *TxOrGen) bool {
	_, exists := b.txLookup[txOrGen.Id]
	return exists
}

// FindIdx returns if the bundle contains a tx with the given hash and its index in bundle.
func (b *Bundle) FindIdx(id common.Hash) int {
	if idx, exists := b.txLookup[id]; exists {
		return idx
	}
	return -1
}

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
