## Analog Found

### Title
Gasless `SwapForGas` transactions accept a `minAmountOut` that is validated only against `amountRepay`, exposing users to sandwich/front-running value extraction - ([File: kaiax/gasless/impl/tx_pool.go])

### Summary
The Sherlock report describes DODO's `sellShares()` accepting a caller-supplied `baseMinAmount`/`quoteMinAmount` with no floor above zero, letting a front-runner sandwich the withdrawal and extract nearly all value while still passing the `>=` check. Kaia's KIP-247 Gasless module has the structurally identical weakness in its own admission/validation logic for `GaslessSwapTx` (`swapForGas`): the only floor enforced on the user-supplied `minAmountOut` slippage-protection parameter is that it be `>= amountRepay`, not that it reflect a fair/expected market price for the swap.

### Finding Description
`GaslessSwapTx` calls `swapForGas(token, amountIn, minAmountOut, amountRepay, deadline)` on the whitelisted `GaslessSwapRouter`. This transaction is decoded and validated on the Kaia client (both at tx-pool admission and at execution/promotion time) in `checkBalanceForSwap`: [1](#0-0) 

The comment above the function documents the complete set of invariants enforced by the node: `tx.minAmountOut >= tx.amountRepay`, `tx.amountIn >= gsr.getAmountIn(minAmountOut)`, approval/balance checks, and `deadline`. There is no check that `minAmountOut` reflects a realistic/fair AMM output for `amountIn` at broadcast time (e.g., relative to `GetAmountsOut` for the current reserves) beyond the DEX's own slippage revert. `minAmountOut` is fully attacker/user-controlled input decoded straight from calldata: [2](#0-1) 

Because the module only enforces `minAmountOut >= amountRepay` (the minimum needed for the proposer to be repaid), a user (or a proposer/searcher constructing a sandwich around the swap) can set `minAmountOut` exactly at `amountRepay` — the lowest value the node will accept — and the transaction is treated as fully valid and promoted for execution: [3](#0-2) 

At block-building time the module wraps the tx into a bundle `[LendTxGenerator, GaslessApproveTx, GaslessSwapTx]`, which is executed atomically but still ordered within the block by the proposer, alongside any auction-bid transactions from `kaiax/auction`. The `AuctioneerSig`/`SearcherSig` bidding mechanism in `kaiax/auction/impl/bid_pool.go` explicitly allows a `Bid` transaction to be inserted immediately after (and can be arranged around) any target transaction, including a `GaslessSwapTx`: [4](#0-3) 

Because the router only guarantees the user receives at least `minAmountOut` and the client-side check does not prevent `minAmountOut` from being pinned at the bare repay floor, a searcher/proposer with the ability to manipulate the pool state before the swap executes (either through ordinary front-running or the auction bidding path) can push the realized swap output down to just `amountRepay`, capturing the entire surplus that should have gone to the "final user amount" (as emitted in `SwappedForGas.FinalUserAmount`): [5](#0-4) 

This mirrors the reported pattern precisely: the protocol advertises a slippage-protection field (`minAmountOut` / `baseMinAmount`+`quoteMinAmount`) but the enforced floor is degenerate (equal to the repayment obligation / zero) rather than tied to fair market value, so the field provides no real protection against sandwiching.

### Impact Explanation
A gasless-swap user can have essentially all of their swap proceeds beyond the gas-repayment amount extracted by a sandwiching party (front-runner or the block proposer itself, who has privileged transaction-ordering power over the bundle), resulting in concrete unauthorized value extraction from an unprivileged, single-transaction-submitting user. This is a High-severity value-loss issue analogous to the original finding, reachable purely by a normal user submitting a `GaslessSwapTx` (or having one crafted for them by tooling) with `minAmountOut` at the minimum accepted value.

### Likelihood Explanation
Likelihood is elevated because: (1) `GaslessSwapTx` calldata (including `minAmountOut` and `amountIn`) is fully visible in the tx pool before inclusion; (2) block proposers have direct control over transaction ordering within the block/bundle; (3) the auction module (`kaiax/auction`) provides a sanctioned, low-friction mechanism (bid transactions inserted right after a target tx) that a searcher could leverage to execute a sandwich around the swap; and (4) nothing in the node's validation flags or rejects a `minAmountOut` that is set at the bare minimum accepted value, so no additional exploit precondition beyond normal usage is required.

### Recommendation
Do not rely solely on `minAmountOut >= amountRepay` as the acceptance bar for `GaslessSwapTx`. Additionally validate that `minAmountOut` is within a reasonable bound of the current AMM quote (e.g., require `minAmountOut` to be within some maximum slippage tolerance of `GetAmountsOut` at admission time), and/or restrict sandwiching opportunities around gasless-swap bundles at the block-building level (e.g., disallow other-sender bundles/bids from being interleaved immediately before a `GaslessSwapTx` in the same block).

### Proof of Concept
1. User Alice signs `GaslessApproveTx` + `GaslessSwapTx` with `amountIn = X`, `amountRepay = R` (computed to cover proposer's gas), and sets `minAmountOut = R` (the minimum the node will accept per `checkBalanceForSwap`).
2. These transactions enter the public tx pool and are visible to searchers/the block proposer.
3. A searcher or the proposer inserts a large swap on the same pool (via a normal front-run tx, or via `kaiax/auction`'s bid mechanism, which allows arbitrary calldata to run adjacent to a target tx) immediately before Alice's bundle is executed, moving the pool price against Alice.
4. Alice's `swapForGas` executes, and because `minAmountOut = R` is honored exactly, Alice's swap produces just enough output to repay the proposer (`amountRepay`), with `FinalUserAmount` reduced to near zero — all surplus is captured by the sandwicher.
5. The searcher reverses their position after Alice's tx executes, realizing the difference as profit, exactly as described in the original report's PoC steps but replayed against `GaslessSwapRouter.swapForGas` instead of `GSPFunding.sellShares`.

### Citations

**File:** kaiax/gasless/impl/tx_pool.go (L102-120)
```go
// tx.minAmountOut >= tx.amountRepay
// tx.amountIn >= gsr.getAmountIn(minAmountOut)
// tx.token.approval(sender, router) >= tx.amountIn
// tx.token.balanceOf(sender) >= tx.amountIn
// tx.deadline >= currentTimestamp
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
```

**File:** kaiax/gasless/impl/getter.go (L142-180)
```go
func decodeSwapTx(tx *types.Transaction, signer types.Signer) (args *SwapArgs, ok bool) {
	to, inputs, ok := decodeFunctionCall(tx, routerSwapFunc)
	if !ok {
		return nil, false
	}
	token, ok := inputs["token"].(common.Address)
	if !ok {
		return nil, false
	}
	amountIn, ok := inputs["amountIn"].(*big.Int)
	if !ok {
		return nil, false
	}
	minAmountOut, ok := inputs["minAmountOut"].(*big.Int)
	if !ok {
		return nil, false
	}
	amountRepay, ok := inputs["amountRepay"].(*big.Int)
	if !ok {
		return nil, false
	}
	deadline, ok := inputs["deadline"].(*big.Int)
	if !ok {
		return nil, false
	}
	from, err := types.Sender(signer, tx)
	if err != nil {
		return nil, false
	}
	return &SwapArgs{
		Sender:       from,
		Router:       to,
		Token:        token,
		AmountIn:     amountIn,
		MinAmountOut: minAmountOut,
		AmountRepay:  amountRepay,
		Deadline:     deadline,
	}, true
}
```

**File:** tests/gasless_test.go (L245-249)
```go
	//// Reject obviously reverting SwapTx.

	// reject swapTx when minAmountOut < amountRepay
	_, err = sendSwapTx(t, gsrContract, accounts[0], testTokenAddr, swapAmmount, common.Big0, amountRepaySwap, deadline)
	assert.ErrorContains(t, err, "insufficient minAmountOut")
```

**File:** kaiax/auction/impl/bid_pool.go (L345-395)
```go
func (bp *BidPool) validateBid(bid *auction.Bid) error {
	blockNumber := bid.BlockNumber

	bp.bidMu.RLock()

	// Check if the auction tx is already in the pool.
	if _, ok := bp.bidMap[bid.Hash()]; ok {
		bp.bidMu.RUnlock()
		return auction.ErrBidAlreadyExists
	}

	// 1. The `bid.Sender` must not be in the winner list of the same block number if the new bid isn't equal to the previous bid.
	if bp.senderHasDifferentWinner(bid) {
		bp.bidMu.RUnlock()
		return auction.ErrBidSenderExists
	}
	bp.bidMu.RUnlock()

	curBlock := bp.Chain.CurrentBlock()
	if curBlock == nil {
		return auction.ErrBlockNotFound
	}

	// 2. The `bid.BlockNumber` must be in range of `[currentBlockNumber + 1, currentBlockNumber + allowFutureBlock]`.
	curNum := curBlock.NumberU64()
	if blockNumber <= curNum || blockNumber > curNum+allowFutureBlock {
		return auction.ErrInvalidBlockNumber
	}

	// 3. The `bid.Bid` must be greater than 0.
	if bid.Bid.Sign() <= 0 {
		return auction.ErrZeroBid
	}

	// 4. The data size must be less than the maximum limit.
	if uint64(len(bid.Data)) > BidTxMaxDataSize {
		return auction.ErrExceedMaxDataSize
	}

	// 5. The gas limit must be less than the maximum limit.
	if bid.CallGasLimit > BidTxMaxCallGasLimit {
		return auction.ErrExceedMaxCallGasLimit
	}

	// 6. The `bid.SearcherSig` and `bid.AuctioneerSig` must be valid.
	if err := bp.validateBidSigs(bid); err != nil {
		return err
	}

	return nil
}
```

**File:** contracts/bindings/kip247/GaslessSwapRouter.go (L1127-1135)
```go
// GaslessSwapRouterSwappedForGas represents a SwappedForGas event raised by the GaslessSwapRouter contract.
type GaslessSwapRouterSwappedForGas struct {
	Proposer        common.Address
	AmountRepaid    *big.Int
	User            common.Address
	FinalUserAmount *big.Int
	Commission      *big.Int
	Raw             types.Log // Blockchain specific contextual infos
}
```
