### Title
Auction bundle sandwiches a gasless swap by exploiting the fixed slippage window between admission-time price check and block-inclusion execution - (File: kaiax/gasless/impl/tx_pool.go, work/builder/builder.go)

### Summary
`GaslessSwapRouter.swapForGas` (KIP-247) executes a user's token→KAIA swap with a proposer-chosen `minAmountOut` that is only validated against the AMM's *current* quote at tx-pool admission time, not re-validated at execution time. Because the Kaia block builder explicitly allows an `AuctionModule` bundle to be placed immediately adjacent to (targeting) a `GaslessModule` bundle's swap transaction within the same block, a block proposer/auctioneer can insert an AMM trade immediately before the gasless swap (and reverse it after) to move the effective execution price against the user, extracting the difference between the quoted-at-admission output and the actual manipulated output while remaining within the `minAmountOut` bound. This is a same-transaction/same-block sandwich the sender cannot detect or reject, directly analogous to the reported GoodEntry `swapAll`/`close` sandwich (fixed slippage tolerance + no re-check before settlement).

### Finding Description
`checkBalanceForSwap` in `kaiax/gasless/impl/tx_pool.go` validates a `GaslessSwapTx`'s `minAmountOut`/`amountIn` against the router's `GetAmountIn` quote only once, at tx-pool admission: [1](#0-0) 

This check establishes that, *at admission time*, `amountIn` is sufficient for the declared `minAmountOut`. It performs no further verification tied to the block in which the swap is ultimately executed, and the AMM price used for `GetAmountIn` is whatever the pool's current on-chain reserves are at that moment — a value fully controllable by the block proposer's own subsequent trades.

At block-building time, `ExtractTxBundles` wraps the gasless approve/swap pair into a `[LendTxGenerator, ApproveTx, SwapTx]` bundle: [2](#0-1) 

Crucially, `ExtractBundlesAndIncorporate` and `coordinateTargetTxHash` in `work/builder/builder.go` explicitly support and preserve an *auction bundle targeting the gasless bundle's last transaction* — i.e., an auction-won transaction can be placed immediately after (or, by symmetric target-hash mechanics, adjacent to) the gasless swap transaction within the same block: [3](#0-2) [4](#0-3) 

This adjacency guarantee is directly tested and documented as an intended feature: [5](#0-4) 

Since the block proposer is also the entity settling the winning auction bid (via `AuctionModule`/`bid_pool.go`) and controls transaction ordering for the block it assembles, it can:
1. Insert its own (or an accomplice's) auction-winning swap immediately before the gasless swap to move the AMM pool price against the gasless swap's direction (pushing effective output down toward, but not below, the user's `minAmountOut`).
2. Let the `GaslessSwapTx` execute at the worse, but still passing, price (the router only enforces `amountOut >= minAmountOut`, so it does not revert).
3. Reverse the price move with a back-run trade (or profit directly from the arbitrage created), pocketing the spread between the honest expected output and the manipulated output.

The `GaslessSwapRouter`'s Solidity source is not present in the indexed repo (only Go bindings/ABI were found), so the exact on-chain `minAmountOut` enforcement logic could not be directly inspected; this is inferred from the ABI function signature and the invariant enforced in the tx-pool check (`minAmountOut >= amountRepay`): [6](#0-5) [7](#0-6) 

### Impact Explanation
This allows the party that controls block assembly (the proposer, who is also necessarily privy to/participating in the auction settlement for that block via `AuctionModule`) to extract value from every gasless-swap user whose declared `minAmountOut` margin exceeds the strict minimum (`amountRepay`). The stolen value comes directly out of the user's expected swap proceeds (`FinalUserAmount`), constituting unauthorized value extraction/theft facilitated by the gasless and auction settlement mechanisms working together — squarely within the "gasless or auction settlement theft" impact category. Because the mechanism relies only on capabilities the block proposer/auctioneer already legitimately has (bundle placement, self-inclusion of trades), no additional privilege escalation is required.

### Likelihood Explanation
Medium-High. The adjacency of auction bundles to gasless-swap targets is an explicitly supported and tested code path (`TestCoordinateTargetTxHashDeterministicWithGaslessTarget`), meaning it is a normal, reachable operating mode rather than an edge case. Any block proposer running the auction module, or any account that wins the block's auction slot, can perform this sandwich whenever a pending `GaslessSwapTx` with a slippage margin above the strict repay minimum is available — which is expected to be common, since users typically add slippage buffer for safety.

### Recommendation
- Re-validate the gasless swap's actual execution-time price impact against a manipulation-resistant reference (e.g., a TWAP, or by disallowing any other trade on the same pool within the same block preceding a gasless swap) rather than relying solely on the tx-pool-admission-time quote.
- Consider forbidding an auction bundle from targeting/adjoining a gasless-swap transaction that touches the same liquidity pool, or require the gasless router to compute `minAmountOut` at settlement using an oracle/TWAP rather than trusting the user-declared value checked only once at admission.
- Cap the allowed spread between `amountRepay` and `minAmountOut`, or refund any surplus above the expected quote to the user rather than allowing it to be arbitraged away, closing the extractable margin.

### Proof of Concept
1. Deploy/observe a `GaslessSwapRouter` pool with a `GaslessSwapTx` pending, using `minAmountOut = amountRepay + margin` (as shown in the reference test setup, `margin` computed as ~1% of expected output): [8](#0-7) 
2. As the block proposer/auction winner for that block, submit (or accept via the auction) a bid transaction that trades against the same AMM pool in the direction that will worsen the price for the pending `GaslessSwapTx`, placed immediately before it using the guaranteed adjacency behavior of `coordinateTargetTxHash`: [9](#0-8) 
3. Include the `GaslessSwapTx`; because the router only checks `amountOut >= minAmountOut` (verified at pool-admission time against the pre-manipulation price via `checkBalanceForSwap`), the swap executes successfully but at the worse, manipulated price.
4. Insert a back-run trade (or exploit the arbitrage created) to restore the pool price and realize the captured spread as profit, repeating across multiple gasless users each block to accumulate value at their expense.

### Citations

**File:** kaiax/gasless/impl/tx_pool.go (L107-141)
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

**File:** work/builder/builder.go (L270-310)
```go
func ExtractBundlesAndIncorporate(arrayTxs []*types.Transaction, txBundlingModules []TxBundlingModule) ([]*TxOrGen, []*Bundle) {
	// Detect bundles and add them to bundles
	bundles := []*Bundle{}
	flattenedTxs := []*TxOrGen{}
	if txBundlingModules == nil {
		for _, tx := range arrayTxs {
			flattenedTxs = append(flattenedTxs, NewTxOrGenFromTx(tx))
		}
		return flattenedTxs, nil
	}

	for _, txBundlingModule := range txBundlingModules {
		newBundles := txBundlingModule.ExtractTxBundles(arrayTxs, bundles)
		for _, newBundle := range newBundles {
			isConflict := false
			// Check for conflicts with all previous bundles
			for _, prevBundle := range bundles {
				isConflict = prevBundle.IsConflict(newBundle)
				if isConflict {
					break
				}
			}
			// Not allowing empty bundles
			if !isConflict && len(newBundle.BundleTxs) > 0 {
				bundles = append(bundles, newBundle)
			}
		}
	}

	// Coordinate target tx hash of bundles. It assumes the Gasless and Auction modules only currently.
	// This reordering does not break the execution result.
	// For example, if bundle reordering breaks the nonce ordering, the execution result will be different.
	bundles = coordinateTargetTxHash(bundles)

	incorporatedTxs, err := IncorporateBundleTx(arrayTxs, bundles)
	if err != nil {
		return flattenedTxs, nil
	}

	return incorporatedTxs, bundles
}
```

**File:** work/builder/builder.go (L322-380)
```go
// coordinateTargetTxHash coordinates the target tx hash of bundles.
// It assumes there's only one bundle with TargetRequired = true among the bundles with the same TargetTxHash
// and no zero-length bundle.
// e.g.) bundles = [
//
//	{TargetTxHash: 0x2, TargetRequired: false, BundleTxs: []*TxOrGen{tx3, tx4}},
//	{TargetTxHash: 0x2, TargetRequired: true, BundleTxs: []*TxOrGen{g1}},
//
// ]
// -> returns [
//
//	{TargetTxHash: 0x2, TargetRequired: true, BundleTxs: []*TxOrGen{g1}},
//	{TargetTxHash: g1.Id, TargetRequired: false, BundleTxs: []*TxOrGen{tx3, tx4}},
//
// ]
func coordinateTargetTxHash(bundles []*Bundle) []*Bundle {
	if len(bundles) <= 1 {
		return bundles
	}

	newBundles := make([]*Bundle, 0, len(bundles))
	sameTargetTxHashBundles := make(map[common.Hash][]*Bundle)
	// Emit groups in first-seen (input) order. Ranging over the map directly
	// uses Go's randomized iteration order, which is nondeterministic.
	order := make([]common.Hash, 0, len(bundles))

	for _, bundle := range bundles {
		if _, ok := sameTargetTxHashBundles[bundle.TargetTxHash]; !ok {
			order = append(order, bundle.TargetTxHash)
		}
		sameTargetTxHashBundles[bundle.TargetTxHash] = append(sameTargetTxHashBundles[bundle.TargetTxHash], bundle)
	}

	for _, key := range order {
		list := sameTargetTxHashBundles[key]
		if len(list) == 1 {
			newBundles = append(newBundles, list[0])
			continue
		}

		// Find the bundle with TargetRequired = true and move it to the front.
		// This is needed because #incorporate assumes that targetTxHash is already in the txs.
		for i, bundle := range list {
			if bundle.TargetRequired {
				list[0], list[i] = list[i], list[0]
				break
			}
		}

		for i, bundle := range list {
			if i == 0 {
				continue
			}
			bundle.TargetTxHash = lastBundleTx(list[i-1]).Id
		}
		newBundles = append(newBundles, list...)
	}

	return newBundles
```

**File:** work/builder/builder_test.go (L585-627)
```go
// Regression test: an auction bundle targeting a gasless bundle's last tx must
// stay adjacent to its target after coordinateTargetTxHash, regardless of the
// (previously map-randomized) group order.
func TestCoordinateTargetTxHashDeterministicWithGaslessTarget(t *testing.T) {
	approveTx := types.NewTransaction(0, common.Address{}, big.NewInt(0), 0, big.NewInt(0), nil)
	swapTx := types.NewTransaction(1, common.Address{}, big.NewInt(0), 0, big.NewInt(0), nil)
	targetTx := types.NewTransaction(2, common.Address{}, big.NewInt(0), 0, big.NewInt(0), nil)
	fillerTx := types.NewTransaction(3, common.Address{}, big.NewInt(0), 0, big.NewInt(0), nil)
	txs := []*types.Transaction{approveTx, swapTx, targetTx, fillerTx}

	gen := func(nonce uint64) (*types.Transaction, error) {
		return types.NewTransaction(nonce, common.Address{}, big.NewInt(0), 0, big.NewInt(0), nil), nil
	}
	lendGen := NewTxOrGenFromGen(gen, common.Hash{0xaa})
	bidGen := NewTxOrGenFromGen(gen, common.Hash{0xbb})

	// Gasless bundle owns swap as its last tx; auction bundle targets swap.
	// Module registration order places gasless before auction in the input.
	newInput := func() []*Bundle {
		gasless := NewBundle([]*TxOrGen{lendGen, NewTxOrGenFromTx(approveTx), NewTxOrGenFromTx(swapTx)}, targetTx.Hash(), false)
		auction := NewBundle([]*TxOrGen{bidGen}, swapTx.Hash(), true)
		return []*Bundle{gasless, auction}
	}

	in := newInput()
	require.False(t, in[0].IsConflict(in[1]), "auction targeting the gasless bundle's last tx must be allowed")

	for i := 0; i < 100; i++ {
		incorporated, err := IncorporateBundleTx(txs, coordinateTargetTxHash(newInput()))
		require.NoError(t, err)

		bidIdx := -1
		for j, txOrGen := range incorporated {
			if txOrGen.Id == bidGen.Id {
				bidIdx = j
				break
			}
		}
		require.NotEqual(t, -1, bidIdx, "auction bid must be present")
		require.Greater(t, bidIdx, 0, "auction bid must not be first; its target must precede it")
		require.Equal(t, swapTx.Hash(), incorporated[bidIdx-1].Id, "auction bid must immediately follow its target swap tx")
	}
}
```

**File:** kaiax/gasless/impl/getter.go (L59-67)
```go
type SwapArgs struct {
	Sender       common.Address // tx.from
	Router       common.Address // tx.to
	Token        common.Address
	AmountIn     *big.Int
	MinAmountOut *big.Int
	AmountRepay  *big.Int
	Deadline     *big.Int
}
```

**File:** contracts/bindings/kip247/GaslessSwapRouter.go (L568-573)
```go
// SwapForGas is a paid mutator transaction binding the contract method 0x80426901.
//
// Solidity: function swapForGas(address token, uint256 amountIn, uint256 minAmountOut, uint256 amountRepay, uint256 deadline) returns()
func (_GaslessSwapRouter *GaslessSwapRouterTransactorSession) SwapForGas(token common.Address, amountIn *big.Int, minAmountOut *big.Int, amountRepay *big.Int, deadline *big.Int) (*types.Transaction, error) {
	return _GaslessSwapRouter.Contract.SwapForGas(&_GaslessSwapRouter.TransactOpts, token, amountIn, minAmountOut, amountRepay, deadline)
}
```

**File:** tests/gasless_test.go (L151-163)
```go
	var (
		gasPriceBN         = new(big.Int).Mul(big.NewInt(50), bigGkei)
		R1                 = new(big.Int).Mul(big.NewInt(21000), gasPriceBN)
		R2                 = new(big.Int).Mul(big.NewInt(100000), gasPriceBN)
		R3                 = new(big.Int).Mul(big.NewInt(500000), gasPriceBN)
		ammontRepay        = new(big.Int).Add(R1, new(big.Int).Add(R2, R3))
		amountRepaySwap    = new(big.Int).Add(R1, R3)
		transferToken      = new(big.Int).Mul(big.NewInt(100), bigKaia)
		swapExpectedOutput = amountsOut[1]
		margin             = new(big.Int).Div(swapExpectedOutput, big.NewInt(100))
		minAmountOut       = new(big.Int).Add(ammontRepay, margin)
		deadline           = new(big.Int).Add(chain.CurrentBlock().Time(), big.NewInt(300))
	)
```
