### Title
Auction bid pool accepts bids without verifying searcher's on-chain deposit balance - (File: kaiax/auction/impl/bid_pool.go)

### Summary
The `BidPool.validateBid` and `insertBid` functions in `kaiax/auction/impl/bid_pool.go` accept and record a `Bid` as the winning bid for a target transaction/block without ever checking that the `bid.Sender` (searcher) actually holds a sufficient deposit in the on-chain `AuctionEntryPoint` contract's deposit vault to cover `bid.Bid`. This mirrors the reported Notional pattern where a value-bearing operation is performed without checking the corresponding on-chain cap/balance, allowing the accounting invariant enforced elsewhere in the system to be silently bypassed.

### Finding Description
`validateBid` checks nonce/hash uniqueness, block-number range, positive bid amount, calldata size, gas limit, and signatures, but performs no comparison against the searcher's deposit: [1](#0-0) 

The contract-level API `getNoncesAndDeposits` exists specifically to expose each searcher's current deposit for this purpose: [2](#0-1) 

but it is never called anywhere in the `kaiax/auction` module (no references found in `kaiax/auction/**`). Once a bid passes `validateBid`, `insertBid` records it as the block's winner for that sender/target and blocks any other (different) bid from the same sender for that block via `senderHasDifferentWinner`: [3](#0-2) 

The winning bid is later turned into a bundle and injected into the block via `ExtractTxBundles`, unconditionally, whenever the bid's target transaction is present in the mined block's tx list: [4](#0-3) 

Because the deposit is never checked off-chain, a searcher (unprivileged, only needs a valid EIP-712 signature and an auctioneer co-signature obtained through the normal auction flow) can submit and have accepted a bid whose amount exceeds their actual on-chain deposit. This occupies the single "winner" slot for that `(blockNumber, sender)` and `(blockNumber, targetTxHash)`, preventing legitimate bids from the same searcher or bids targeting the same transaction from ever being displaced by a real, fundable, lower-but-payable bid (since replacement only occurs when a strictly higher bid arrives, per the FCFS/greater-bid logic in `insertBid`), and reserves block-building resources for a bid tx that is expected to fail/revert on-chain when the entry-point contract's own deposit check runs during the `call` execution.

### Impact Explanation
This allows an attacker to grief the auction/gasless settlement mechanism: by submitting unfunded, high-value bids that pass local pool validation, an attacker can occupy winning slots ahead of legitimate, fundable searchers for a given block and target transaction, causing the proposer to build a bundle around a transaction that will fail on-chain (wasting block gas/space reserved by `bidTxGasBuffer`/`CallGasLimit`) and denying the actual bid/fee revenue that the auction is meant to capture for that slot. This is a concrete disruption of auction settlement/fee capture reachable from a single externally submitted bid (`auction_submitBid`), fitting the "gasless or auction settlement theft" and "reward redirection" categories, since it can be used to redirect or deny legitimate bid revenue to the protocol/proposer.

### Likelihood Explanation
Likelihood is high: `auction_submitBid` is a public RPC available to any account able to obtain the required searcher EIP-712 signature and an auctioneer co-signature via the normal off-chain auction flow described in the module's README; no privileged role or node access is required, and the missing check is unconditional in the validation path.

### Recommendation
Add a deposit-balance check to `BidPool.validateBid` (or `insertBid`) that reads the searcher's current deposit via the on-chain `getNoncesAndDeposits` (or equivalent) call against the relevant `AuctionEntryPoint` at the mining block's expected state, and reject/deprioritize bids whose `bid.Bid` exceeds the searcher's available deposit, consistent with how `checkSupplyCap` is meant to gate value-bearing operations before they are committed to state or the block-building pipeline.

### Proof of Concept
1. An attacker obtains a validly signed searcher signature and a valid auctioneer co-signature for a `Bid{Sender: attacker, Bid: <very large amount>, TargetTxHash: <victim target tx>, BlockNumber: N}` (the auctioneer signs based on off-chain simulation and may not guarantee synchronized on-chain deposit state at bid-pool-insertion time, or the flow can be raced/mis-timed).
2. Attacker calls `auction_submitBid` (`kaiax/auction/impl/api.go` `SubmitBid`) with this bid.
3. `BidPool.AddBid` → `validateBid` → `insertBid` accept the bid purely based on signature/format checks (`kaiax/auction/impl/bid_pool.go` lines 345-395, 276-324), with no deposit check, and record it as the winner for `(N, attacker)` and `(N, targetTxHash)`.
4. `ExtractTxBundles` builds a bundle containing the bid tx for block N (`kaiax/auction/impl/builder.go` lines 31-70), consuming the winner slot and block-building resources for a bid that reverts on-chain due to insufficient deposit, while legitimate lower/fundable bids for the same sender or target are blocked from being considered as winners for that block.

### Citations

**File:** kaiax/auction/impl/bid_pool.go (L335-343)
```go
// senderHasDifferentWinner reports whether a different bid from the same sender
// already exists in the winner list for this block. Caller must hold bidMu.
func (bp *BidPool) senderHasDifferentWinner(bid *auction.Bid) bool {
	hash, ok := bp.bidWinnerMap[bid.BlockNumber][bid.Sender]
	if !ok {
		return false
	}
	return !bid.Equals(bp.bidMap[hash])
}
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

**File:** contracts/bindings/auctionv3/Kip249V3.go (L480-503)
```go
// GetNoncesAndDeposits is a free data retrieval call binding the contract method 0x0339ed37.
//
// Solidity: function getNoncesAndDeposits(address[] searchers) view returns(uint256[] nonces_, uint256[] deposits_)
func (_IAuctionEntryPoint *IAuctionEntryPointCaller) GetNoncesAndDeposits(opts *bind.CallOpts, searchers []common.Address) (struct {
	Nonces   []*big.Int
	Deposits []*big.Int
}, error) {
	var out []interface{}
	err := _IAuctionEntryPoint.contract.Call(opts, &out, "getNoncesAndDeposits", searchers)

	outstruct := new(struct {
		Nonces   []*big.Int
		Deposits []*big.Int
	})
	if err != nil {
		return *outstruct, err
	}

	outstruct.Nonces = *abi.ConvertType(out[0], new([]*big.Int)).(*[]*big.Int)
	outstruct.Deposits = *abi.ConvertType(out[1], new([]*big.Int)).(*[]*big.Int)

	return *outstruct, err

}
```

**File:** kaiax/auction/impl/builder.go (L31-70)
```go
func (a *AuctionModule) ExtractTxBundles(txs []*types.Transaction, prevBundles []*builder.Bundle) []*builder.Bundle {
	bundles := []*builder.Bundle{}
	curBlock := a.Chain.CurrentBlock()
	if curBlock == nil || atomic.LoadUint32(&a.bidPool.running) == 0 {
		return bundles
	}

	miningBlock := curBlock.NumberU64() + 1
	bidTargetMap := a.bidPool.GetTargetTxMap(miningBlock)
	if len(bidTargetMap) == 0 {
		return bundles
	}

	for _, tx := range txs {
		txHash := tx.Hash()
		bid, ok := bidTargetMap[txHash]
		if !ok {
			continue
		}
		b := builder.NewBundle(
			builder.NewTxOrGenList(a.GetBidTxGenerator(tx, bid)),
			txHash,
			true,
		)

		isConflict := false
		for _, prev := range append(prevBundles, bundles...) {
			if prev.IsConflict(b) {
				isConflict = true
				break
			}
		}
		if isConflict {
			continue
		}
		bundles = append(bundles, b)
	}

	return bundles
}
```
