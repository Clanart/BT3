## Analog Found

### Title
Auction bid pool admits bids from searchers with no deposit sufficiency check, allowing a zero/insufficient-deposit searcher to grief the winning slot and block legitimate bids - (File: kaiax/auction/impl/bid_pool.go)

### Summary
The Sherlock finding shows that `depositAuction()`/`netAtPrice()` commit to processing an amount without first verifying the corresponding funds actually exist on-chain, letting a participant front-run with a withdrawal to make the operation fail after other parties have already committed to it. Kaia's auction module (`kaiax/auction`) has the analogous gap: a bid is admitted into the pool and made the block's "winner" for a target transaction purely from off-chain signature/format checks, without ever verifying the searcher's actual deposit balance recorded in `AuctionEntryPoint`.

### Finding Description
`BidPool.validateBid` in `kaiax/auction/impl/bid_pool.go` only checks: duplicate winner conflicts, block-number window, `bid.Bid > 0`, calldata/gas-limit size bounds, and searcher/auctioneer signature validity. [1](#0-0) 

Nowhere in this admission path is the searcher's actual on-chain deposit (exposed via `getNoncesAndDeposits` on the `AuctionEntryPoint` contract) checked against `bid.Bid`. [2](#0-1) 

Once a bid passes `validateBid`, `insertBid` immediately grants it the winner slot for that `(blockNumber, targetTxHash)`, evicting any previously-accepted lower bid, and locks the sender into `bidWinnerMap` for that block: [3](#0-2) 

Because a searcher's actual `deposit` sufficiency is only enforced inside the `AuctionEntryPoint` contract's `_checkAndTakeBid` step at execution time (not modeled/enforced anywhere in the Go bid-pool logic, and stubbed out entirely in the test mock `AuctionEntryPointMock.sol`): [4](#0-3) 

a searcher can submit (and get an auctioneer signature for) a high `bid.Bid` value while holding zero or insufficient deposit, or can withdraw/drain their deposit after the auctioneer has signed but before the bid transaction executes on-chain. This mirrors the report's core issue exactly: the check for sufficiency of committed funds is deferred to execution time (`_checkAndTakeBid`) rather than being enforced (or the winner slot being reserved) atomically with admission, exactly like `netAtPrice()`/`depositAuction()` deferring the USDC-sufficiency check to the point of the router/transfer call.

The winner slot is exclusive: `senderHasDifferentWinner` and the strict "must-be-higher" replacement rule in `insertBid` mean once a fraudulent high bid occupies `bidTargetMap[blockNumber][targetTxHash]`, a legitimate, fully-funded, lower (but real) bid for the same target transaction is rejected with `ErrLowBid`, and the same searcher cannot be replaced by a different bid for a different target in the same block (`ErrBidSenderExists`). At block-building time, `ExtractTxBundles`/`GetBidTxGenerator` will build and sign a bid transaction for this fraudulent bid, which will simply fail/revert on-chain against `_checkAndTakeBid` since deposit is insufficient: [5](#0-4) [6](#0-5) 

### Impact Explanation
The real financial harm here is identical in class to the Sherlock report: a market participant (searcher) can, at no real cost, occupy the winning auction slot for a target transaction by outbidding all legitimately-funded competitors, and then cause the resulting execution to fail (deposit already withdrawn/insufficient). This denies the target transaction sender/proposer the auction revenue that a genuine, funded searcher would have paid, and denies the honest, out-bid searcher(s) the opportunity to win and execute their MEV strategy for that block — a griefing/market-timing attack at the expense of other auction participants, matching the report's stated impact ("manipulating netting and auction functions can be used for market timing... at the expense of other participants").

### Likelihood Explanation
Any unprivileged searcher who can obtain an auctioneer signature for a bid (a normal step of the KIP-249 flow) can exploit this without needing any special privilege — they simply do not need to maintain sufficient deposit at the time of bid submission, since `validateBid` never checks it. The attack requires no malicious node/validator/peer behavior — the vulnerable check sits entirely in the standard bid-admission code path reachable through the `auction_submitBid` RPC. [7](#0-6) 

### Recommendation
Add a deposit-sufficiency check to `BidPool.validateBid` (or `insertBid`) by querying `AuctionEntryPoint.getNoncesAndDeposits` for `bid.Sender` and rejecting/evicting bids whose current deposit is less than `bid.Bid`, and re-verify this deposit at block-building time (`ExtractTxBundles`/`GetBidTxGenerator`) immediately before constructing the bid transaction, so that a just-in-time withdrawal cannot occupy or hold the winner slot without being displaced by a genuinely funded competing bid.

### Proof of Concept
1. Searcher A obtains a signed bid (`bid.Bid = X`, high value) from the `Auctioneer` for target tx `T` while holding deposit `≥ X` in `AuctionEntryPoint` at signing time.
2. Searcher A submits the bid via `auction_submitBid`; `BidPool.validateBid`/`insertBid` accept it and it becomes `bidTargetMap[blockNumber][T.Hash()]`'s winner, evicting Searcher B's lower but fully-backed bid (`ErrLowBid` on B's later resubmission attempt).
3. Searcher A withdraws/drains their `AuctionEntryPoint` deposit before the mining block is built (no auction-module check blocks this, since deposit adequacy is never re-verified after admission).
4. At block-building time, `ExtractTxBundles`/`GetBidTxGenerator` builds and signs the bid transaction for A's bid and it is executed on-chain; `_checkAndTakeBid` fails/reverts due to insufficient deposit.
5. Result: the target tx's auction revenue for that slot is lost, and Searcher B's legitimate, fully-funded bid was displaced and never executed, at zero cost to Searcher A.

### Citations

**File:** kaiax/auction/impl/bid_pool.go (L276-324)
```go
func (bp *BidPool) insertBid(bid *auction.Bid) error {
	bp.bidMu.Lock()
	defer bp.bidMu.Unlock()

	var (
		blockNumber  = bid.BlockNumber
		targetTxHash = bid.TargetTxHash
		sender       = bid.Sender
	)

	// Re-check bidWinnerMap here — two concurrent bids can pass validateBid together.
	if _, ok := bp.bidMap[bid.Hash()]; ok {
		return auction.ErrBidAlreadyExists
	}
	if bp.senderHasDifferentWinner(bid) {
		return auction.ErrBidSenderExists
	}

	// If same block number, same target tx hash exists, replace it if it's better
	if existingBid, ok := bp.bidTargetMap[blockNumber][targetTxHash]; ok {
		// FCFS if the bid is the same.
		if existingBid.Bid.Cmp(bid.Bid) >= 0 {
			return auction.ErrLowBid
		}

		logger.Trace("Replace bid", "old", existingBid.Hash(), "new", bid.Hash())
		delete(bp.bidMap, existingBid.Hash())
		delete(bp.bidWinnerMap[blockNumber], existingBid.Sender)
	} else {
		if int64(len(bp.bidMap)) >= bp.maxBidPoolSize {
			logger.Info("Bid pool is full", "maxBidPoolSize", bp.maxBidPoolSize, "bid", bid.Hash())
			return auction.ErrBidPoolFull
		}
	}

	hash := bid.Hash()

	bp.initializeBidMap(blockNumber)

	bp.bidMap[hash] = bid
	bp.bidTargetMap[blockNumber][targetTxHash] = bid
	bp.bidWinnerMap[blockNumber][sender] = hash

	numBidsGauge.Update(int64(len(bp.bidMap)))

	logger.Trace("Add bid", "bid", hash)

	return nil
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

**File:** contracts/bindings/auctionv3/Kip249V3.go (L480-502)
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

```

**File:** contracts/testing/system_contracts/AuctionEntryPointMock.sol (L54-73)
```text
    function call(AuctionTx calldata auctionTx) external onlyProposer {
        // 1. Verify input integrity
        if (!_verifyInputIntegrity(auctionTx)) revert();

        // // 2. Take bid first
        // if (!_checkAndTakeBid(searcher, auctionTx.bid, callGasLimit)) revert();

        // // 3. Execute call and refund execution gas
        // uint256 nonce = _useNonce(searcher);
        // (bool success, ) = auctionTx.to.call{gas: callGasLimit}(auctionTx.data);
        // if (success) {
        //     emit Call(searcher, nonce);
        // } else {
        //     emit CallFailed(searcher, nonce);
        // }

        // // 4. Refund gas to the proposer
        // if (!_payGas(searcher, initialGas)) revert();
        count++;
    }
```

**File:** kaiax/auction/impl/builder.go (L31-67)
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
```

**File:** kaiax/auction/impl/getter.go (L27-69)
```go
func (a *AuctionModule) GetBidTxGenerator(tx *types.Transaction, bid *auction.Bid) *builder.TxOrGen {
	gen := func(nonce uint64) (*types.Transaction, error) {
		var (
			chainId           = a.InitOpts.ChainConfig.ChainID
			signer            = types.LatestSignerForChainID(chainId)
			auctionEntryPoint = a.bidPool.GetAuctionEntryPoint()
			key               = a.InitOpts.NodeKey
		)

		data, err := system.EncodeAuctionCallData(bid, a.bidPool.GetAuctionEntryPointVersion())
		if err != nil {
			return nil, err
		}

		if bid.GetGasLimit() == 0 {
			gasLimit, err := a.bidPool.getBidTxGasLimit(bid)
			if err != nil {
				return nil, err
			}
			bid.SetGasLimit(gasLimit)
		}

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
		if err != nil {
			return nil, err
		}

		err = tx.Sign(signer, key)

		return tx, err
	}

	return builder.NewTxOrGenFromGen(gen, bid.Hash())
```

**File:** kaiax/auction/impl/api.go (L118-142)
```go
func (api *AuctionAPI) SubmitBid(ctx context.Context, bidInput BidInput) RPCOutput {
	numBidRequestCounter.Inc(1)
	if api.a.IsDisabled() {
		return makeRPCOutput(EMPTY_HASH, auction.ErrAuctionDisabled)
	}

	//  1. directly send target transaction
	targetTx, errTxDecode := toTx(bidInput.TargetTxRaw)
	if errTxDecode != nil {
		return makeRPCOutput(EMPTY_HASH, errTxDecode)
	}
	if targetTx.Hash() != bidInput.TargetTxHash {
		return makeRPCOutput(EMPTY_HASH, auction.ErrInvalidTargetTxHash)
	}
	errTargetTxSend := api.a.Backend.SendTx(ctx, targetTx)
	// ignore known transaction related errors against target tx validation
	if errTargetTxSend != nil && !(strings.HasPrefix(errTargetTxSend.Error(), "known transaction:") || errors.Is(errTargetTxSend, gasless_impl.ErrUnableToAddKnownBundleTx)) {
		return makeRPCOutput(EMPTY_HASH, errTargetTxSend)
	}

	// 2. add bid
	bid := ToBid(bidInput)
	bidHash, errValidateBid := api.a.bidPool.AddBid(bid)
	return makeRPCOutput(bidHash, errValidateBid)
}
```
