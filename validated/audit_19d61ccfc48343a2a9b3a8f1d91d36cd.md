### Title
Auction Bid Amount Can Be Set to 1 Wei to Win Auction Slot While Evading Meaningful MEV/Priority Fee Payment - (File: kaiax/auction/impl/bid_pool.go)

### Summary
The Kaia auction module (KIP-249) enforces only that a submitted bid's amount be strictly greater than zero, not that it meet any meaningful minimum. A searcher/bidder can submit a bid of `1 wei` via the `auction_submitBid` RPC and, absent a higher competing bid on the same target transaction, win the exclusive right to have their `BidTx` executed immediately after the target transaction in the next block — the entire economic purpose of the auction — while paying a negligible amount.

### Finding Description
`BidPool.validateBid` only checks:
```go
// 3. The `bid.Bid` must be greater than 0.
if bid.Bid.Sign() <= 0 {
    return auction.ErrZeroBid
}
``` [1](#0-0) 

This is the sole amount-related admission rule in the bid pool; the only other amount comparison, `ErrLowBid`, is relative to a previous bid for the same target/sender and not to any absolute floor: [2](#0-1) 

The generated `BidTx` itself carries `Value = 0`; the bid amount is only encoded into the calldata sent to the `AuctionEntryPoint` contract's `call()` function: [3](#0-2) [4](#0-3) 

The only on-chain sanity check visible in the (mock) `AuctionEntryPoint` implementation is likewise `bid > 0`, not a meaningful minimum: [5](#0-4) 

This mirrors the reported bug class exactly: a value field that is validated as "must not be zero" but is otherwise unconstrained, allowing the fee/payment to be set to the smallest possible non-zero unit (1 wei) to functionally evade the intended fee/payment while still satisfying the `>0` check.

### Impact Explanation
Since the bid pool's only floor is `>0`, and competition is only checked pairwise against existing bids for the same target tx/sender (`ErrLowBid`), a searcher facing no competing bidder for a given target transaction can capture the auction slot — and thus priority, guaranteed back-run execution rights, and any associated MEV — for `1 wei`. This redirects value that should flow to the auctioneer/proposer/protocol as compensation for the privileged execution slot, undermining the auction's fee/incentive design (KIP-249) with minimal-value bids.

### Likelihood Explanation
Any external searcher can call the public `auction_submitBid` RPC with an arbitrary `bid` value; there is no minimum-bid governance parameter or reserve price enforced client-side. Under normal, less-competitive market conditions (single searcher targeting a given transaction), this is trivially and repeatedly exploitable without any special privilege.

### Recommendation
Introduce a governance-configurable minimum bid amount (or minimum-bid-as-percentage-of-expected-MEV/gas) enforced in `BidPool.validateBid`, rather than relying solely on `bid.Bid.Sign() <= 0`. Alternatively, require the bid to exceed a reserve price tied to the target transaction's gas/value or a protocol-defined floor, ensuring the auction cannot be won for a negligible payment when there is no competing bid.

### Proof of Concept
1. A searcher observes a profitable target transaction `T` in the pending pool.
2. The searcher signs and submits a `BidInput` via `auction_submitBid` with `Bid = 1` (wei), a valid `SearcherSig`, and a valid `AuctioneerSig`.
3. `BidPool.validateBid` passes because `bid.Bid.Sign() <= 0` is false for `Bid = 1`: [1](#0-0) 
4. If no other searcher bids higher on the same `TargetTxHash`, this bid becomes and remains the winner (`bidWinnerMap`), and `ExtractTxBundles`/`GetBidTxGenerator` build a `BidTx` bundle that executes immediately after `T` in the next block: [6](#0-5) 
5. The searcher obtains the exclusive back-run execution slot for essentially no cost, having paid only 1 wei into the `AuctionEntryPoint.call()`.

### Citations

**File:** kaiax/auction/impl/bid_pool.go (L374-377)
```go
	// 3. The `bid.Bid` must be greater than 0.
	if bid.Bid.Sign() <= 0 {
		return auction.ErrZeroBid
	}
```

**File:** kaiax/auction/impl/bid_pool_test.go (L227-230)
```go
	// Test bid with lower amount for same target
	lowerBid := testBids[0].Copy()
	_, err = pool.AddBid(lowerBid)
	assert.Equal(t, auction.ErrLowBid, err)
```

**File:** kaiax/auction/impl/getter.go (L49-59)
```go
		tx, err := types.NewTransactionWithMap(types.TxTypeEthereumDynamicFee, map[types.TxValueKeyType]interface{}{
			types.TxValueKeyNonce:      nonce,
			types.TxValueKeyTo:         &auctionEntryPoint,
			types.TxValueKeyAmount:     common.Big0,
			types.TxValueKeyData:       data,
			types.TxValueKeyGasLimit:   bid.GetGasLimit(),
			types.TxValueKeyGasFeeCap:  tx.GasFeeCap(),
			types.TxValueKeyGasTipCap:  tx.GasTipCap(),
			types.TxValueKeyAccessList: types.AccessList{},
			types.TxValueKeyChainID:    chainId,
		})
```

**File:** blockchain/system/auction.go (L83-118)
```go
func EncodeAuctionCallData(bid *auction.Bid, version string) ([]byte, error) {
	if version == auction.AuctionVersionV3 {
		maxGasPrice := bid.MaxGasPrice
		if maxGasPrice == nil {
			maxGasPrice = new(big.Int)
		}
		input := contractsv3.IAuctionEntryPointAuctionTx{
			TargetTxHash:  bid.TargetTxHash,
			BlockNumber:   new(big.Int).SetUint64(bid.BlockNumber),
			Sender:        bid.Sender,
			To:            bid.To,
			Nonce:         new(big.Int).SetUint64(bid.Nonce),
			Bid:           bid.Bid,
			MaxGasPrice:   maxGasPrice,
			CallGasLimit:  new(big.Int).SetUint64(bid.CallGasLimit),
			Data:          bid.Data,
			SearcherSig:   bid.SearcherSig,
			AuctioneerSig: bid.AuctioneerSig,
		}
		return abiV3.Pack("call", input)
	}

	// auction.AuctionVersionV2 or unknown version: default to v2.1 ABI.
	input := contracts.IAuctionEntryPointAuctionTx{
		TargetTxHash:  bid.TargetTxHash,
		BlockNumber:   new(big.Int).SetUint64(bid.BlockNumber),
		Sender:        bid.Sender,
		To:            bid.To,
		Nonce:         new(big.Int).SetUint64(bid.Nonce),
		Bid:           bid.Bid,
		CallGasLimit:  new(big.Int).SetUint64(bid.CallGasLimit),
		Data:          bid.Data,
		SearcherSig:   bid.SearcherSig,
		AuctioneerSig: bid.AuctioneerSig,
	}
	return abi.Pack("call", input)
```

**File:** contracts/testing/system_contracts/AuctionEntryPointMock.sol (L83-86)
```text
        /// 2. Check if the bid is greater than 0
        if (auctionTx.bid <= 0) {
            return false;
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
