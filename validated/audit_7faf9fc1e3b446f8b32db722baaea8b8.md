### Title
Bid pool replaces a valid higher-deposit bid with an unfunded higher-bid before on-chain deposit is verified, permanently losing auction revenue - ([File: kaiax/auction/impl/bid_pool.go])

### Summary
`BidPool.insertBid` selects the winning bid for a given `(blockNumber, targetTxHash)` purely by comparing the numeric `bid.Bid` amount, without any check that the new bidder actually has a sufficient on-chain deposit in `AuctionEntryPoint` to back that bid. This mirrors the reported CrabNetting issue, where an unverified counterparty (a trader lacking token approval) can be selected/processed and cause the whole settlement step to fail, destroying value that the other, valid participants could have realized.

### Finding Description
`BidPool.insertBid` replaces an existing winning bid whenever a new bid for the same target tx has a strictly higher `bid.Bid` value: [1](#0-0) 

`validateBid`, which gates entry into `insertBid`, only checks pool duplication, sender-winner conflicts, block-number range, `bid.Bid > 0`, data/gas-limit size caps, and the searcher/auctioneer EIP-712 signatures — it never queries the searcher's actual deposit balance in the `AuctionEntryPoint` contract: [2](#0-1) 

The actual value transfer (taking the bid from the searcher's deposit) only happens later, on-chain, inside `AuctionEntryPoint.call()` at block-building/inclusion time — which is exactly the step analogous to CrabNetting's `transferFrom` in the audit report. The mock contract shows the intended flow (`_checkAndTakeBid` before executing the searcher's call), confirming the deposit check is a separate, later step: [3](#0-2) 

The Go binding confirms deposits are tracked per searcher on-chain via `getNoncesAndDeposits`, i.e., a searcher can have a stale/insufficient deposit relative to a bid they sign: [4](#0-3) 

When the bid transaction is built into the block, `commitBundleTransaction` treats any non-successful receipt as a fatal bundle failure: it rolls back all state changes for that bundle and marks the bid tx (and any dependents) unexecutable, dropping it entirely: [5](#0-4) 

Because `insertBid` already deleted the previous (lower but adequately-funded) bid from `bidMap`/`bidTargetMap`/`bidWinnerMap` at the moment the higher bid was accepted, there is no fallback to the displaced, valid bid once the higher bid fails on-chain. The auction for that target transaction proceeds with no bid tx executed, and the auction revenue that the displaced bid would have generated is permanently lost for that block.

### Impact Explanation
Any external party able to reach `auction_submitBid` (an unprivileged, gasless-signature-only RPC call requiring no balance check by the bid pool itself) can submit a validly signed bid with an artificially high `bid.Bid` amount while holding an insufficient/zero on-chain deposit in `AuctionEntryPoint`. This bid will pass `validateBid` (which never touches deposit balance), displace a legitimate, properly-funded lower bid in `insertBid`, and then revert on-chain at inclusion time. The end result is that the previously-valid bid is gone and the target transaction is committed without any bid execution — auction/MEV revenue that should have gone to the proposer/searcher ecosystem is destroyed, an outcome the CrabNetting report also flags as "impairing" a batch settlement due to an unverified counterparty being allowed to participate. This is a concrete value-loss/auction-settlement-integrity issue reachable purely via a submitted bid (equivalent to an unprivileged transaction/bid submitter).

### Likelihood Explanation
Likelihood is Medium-to-High: exploiting this requires only (1) a valid EIP-712 searcher signature (trivial for the attacker to produce for their own address) and (2) a plausible-but-unfunded auctioneer-countersigned bid (the auctioneer countersignature is a rubber-stamp on searcher-submitted data per the KIP-249 flow described in the README, not a deposit check), and (3) submitting it before the block is built. No special privilege, staking, or validator role is required — it is reachable from a single `auction_submitBid` RPC call, matching the "auction bidder" persona explicitly listed as reachable in scope.

### Recommendation
Before allowing a new bid to replace an existing winning bid in `insertBid` (and ideally in `validateBid`), read the searcher's current on-chain deposit from `AuctionEntryPoint` (e.g., via `getNoncesAndDeposits`) and require `deposit >= bid.Bid`. If insufficient, reject the new bid instead of evicting the currently valid bid, so that a legitimately funded lower bid is not discarded in favor of one that will fail at inclusion time.

### Proof of Concept
1. Searcher A submits a valid, adequately-deposited bid `bidA` (`Bid = 10`) for `targetTxHash = T`, block `N+1`. `insertBid` stores it as the winner in `bidTargetMap[N+1][T]` and `bidWinnerMap[N+1][A]`.
2. Attacker (Searcher B), with zero or near-zero deposit in `AuctionEntryPoint`, crafts a validly EIP-712-signed bid `bidB` (`Bid = 1000`) for the same `targetTxHash = T` and gets it counter-signed by the auctioneer (per protocol, the auctioneer signs based on off-chain auction results, not a live deposit check).
3. `validateBid`/`insertBid` see `bidB.Bid (1000) > bidA.Bid (10)` and evict `bidA`, storing `bidB` as the sole winner — see the replace logic at [1](#0-0) .
4. At block-building time, `AuctionEntryPoint.call()` for `bidB` reverts because Searcher B's deposit is insufficient to cover `bid.Bid = 1000`.
5. `commitBundleTransaction` rolls back the bid bundle and marks it unexecutable — see [6](#0-5) .
6. The target transaction `T` is included in block `N+1` with no bid executed at all — `bidA`'s legitimate 10-unit auction revenue is permanently lost, since it was already deleted in step 3.

### Citations

**File:** kaiax/auction/impl/bid_pool.go (L294-309)
```go
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

**File:** work/worker.go (L906-936)
```go
	for _, txOrGen := range bundle.BundleTxs {
		tx, err := txOrGen.GetTx(env.state.GetNonce(nodeAddr))
		if err != nil {
			logger.Error("TxGenerator error", "error", err)
			markAllTxUnexecutable()
			restoreEnv()
			return kerrors.ErrTxGeneration, nil, nil
		}

		env.state.SetTxContext(tx.Hash(), common.Hash{}, env.tcount)
		receipt, _, err := bc.ApplyTransaction(env.config, &nodeAddr, env.state, env.header, tx, &env.header.GasUsed, vmConfig)
		// Bundled tx will be rejected with any receipt.Status other than success.
		// There may be cases where a revert occurs within the EVM, which could result in an attack on a tx sender in an already executed bundle.
		if err != nil || receipt.Status != types.ReceiptStatusSuccessful {
			if err != vm.ErrInsufficientBalance && err != vm.ErrTotalTimeLimitReached {
				markAllTxUnexecutable()
			}
			receiptStatus := ""
			if receipt != nil {
				receiptStatus = strconv.FormatUint(uint64(receipt.Status), 10)
			}
			logger.Warn("ApplyTransaction error, restoring env",
				"blockNum", env.header.Number.String(), "txHash", tx.Hash().String(),
				"error", err, "receiptStatus", receiptStatus,
			)
			restoreEnv()
			if err == nil {
				err = kerrors.ErrRevertedBundleByVmErr
			}
			return err, tx, nil
		}
```
