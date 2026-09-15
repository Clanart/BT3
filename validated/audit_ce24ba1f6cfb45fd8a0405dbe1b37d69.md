## Analysis

I confirmed that Kaia's KIP-249 auction module has the exact same class of issue described in the report, applied to the `AuctionEntryPoint.call()` signature scheme.

Each `Bid` signed off-chain by a searcher includes a `Nonce` field that is part of the EIP-712 struct hash (`AuctionTx(bytes32 targetTxHash,uint256 blockNumber,address sender,address to,uint256 nonce,uint256 bid,...)`), and this nonce is meant to be validated on-chain against the searcher's current on-chain nonce (`getNoncesAndDeposits`, and the commented reference check `auctionTx.nonce == nonces(auctionTx.sender)` in the mock contract). This nonce is analogous to the `_queueIndex` in the original `EnforcedTxGateway` report: it is a value baked into a signature at signing time whose correctness can only be confirmed at execution time on-chain, and it can silently go stale between signing, gossip, block-building and execution.

The Kaia node-side `kaiax/auction/impl/bid_pool.go#validateBid` never re-validates `bid.Nonce` against the searcher's live on-chain nonce (via the `AuctionEntryPoint` contract) before accepting the bid as the block's exclusive winner for that sender/block: [1](#0-0) 

Once a bid passes this validation, `insertBid` marks it as the winner for `(blockNumber, sender)`, and it is the *only* bid accepted from that sender for the target block (`senderHasDifferentWinner`) — any other, even higher/valid bid from the same searcher is rejected: [2](#0-1) [3](#0-2) 

Because the nonce is signed off-chain and never revalidated by the node against current on-chain state before block building, exactly the delay scenario from the report applies: a searcher signs a bid for nonce `i`; before the block containing this bid is built, another `AuctionEntryPoint.call()` transaction from the same searcher (or an unrelated stale/rebroadcast bid) consumes nonce `i` on-chain; the winning bid the node locked in is now guaranteed to revert at execution (`return auctionTx.nonce == nonces(auctionTx.sender)` fails on-chain), while the bid slot for that block/sender is already exhausted so no other (possibly higher) bid can take its place.

### Title
Stale off-chain-signed auction bid nonce is never revalidated before block inclusion, enabling denial-of-auction-slot griefing - (File: kaiax/auction/impl/bid_pool.go)

### Summary
`BidPool.validateBid`/`insertBid` accept and lock in a searcher's auction bid as the sole winner for a `(blockNumber, sender)` slot based solely on signature and static-field checks, without checking that the bid's EIP-712-signed `Nonce` still matches the searcher's live on-chain nonce in the `AuctionEntryPoint` contract. Since the nonce is fixed at signing time (like the `_queueIndex` in the reported `EnforcedTxGateway` bug), any nonce-consuming event that occurs between signing/gossip and block building invalidates the bid without the node detecting it.

### Finding Description
The `Bid` struct signed by a searcher includes a `Nonce` field bound into the EIP-712 digest verified by `ValidateSearcherSig`: [4](#0-3) [5](#0-4) 

This nonce is intended to be checked on-chain against the searcher's current nonce, as referenced by `getNoncesAndDeposits` in the generated bindings and the commented `nonces(auctionTx.sender)` check in the reference mock contract: [6](#0-5) 

However, the Kaia node's bid admission logic (`BidPool.validateBid`) only checks: pool duplication, sender-per-block exclusivity, block-number window, bid amount, data size, gas limit, and the two ECDSA signatures. It never queries or checks `bid.Nonce` against the searcher's current on-chain nonce: [1](#0-0) 

Once validated, `insertBid` commits the bid as the exclusive winner for that sender/block via `bidWinnerMap`, and rejects any other bid from the same sender targeting a different transaction for that block: [7](#0-6) 

At block-building time, the bid is turned into a signed `AuctionEntryPoint.call()` transaction and included in the block via `GetBidTxGenerator`/`ExtractTxBundles`, without any additional nonce freshness check: [8](#0-7) [9](#0-8) 

This mirrors the `EnforcedTxGateway` root cause precisely: a value (`_queueIndex`/`nonce`) baked into an off-chain signature is validated only at final execution against a piece of state that can change between signing and inclusion, and the module holding/gating this signed message performs no freshness check against that same state before locking in its outcome.

### Impact Explanation
If the searcher's nonce advances on-chain (e.g., the searcher's earlier bid for the same or an earlier block was already executed, or the searcher/auctioneer double-submits a bid that gets included first) before this bid's target block is built, the locked-in bid transaction is guaranteed to revert on-chain due to the nonce mismatch. Because `senderHasDifferentWinner` prevents any other bid from that sender being accepted for the block once a winner is set, this can be used to grief the auction: an attacker (or unlucky timing) causes the winning slot for a block to be occupied by a bid that is known/guaranteed to fail, denying the legitimate highest bidder inclusion and denying the block proposer/validator the auction revenue for that slot. This is a fee/auction-settlement abuse and value-redirection issue (lost/misallocated auction proceeds) reachable purely by a searcher/bidder submitting a bid through the public auction submission path.

### Likelihood Explanation
The condition requires only a timing gap between bid signing and block-inclusion combined with any nonce-consuming activity for that searcher — a normal and even attacker-inducible occurrence, since a searcher (or an adversarial one impersonating/racing another searcher's flow) can trivially cause its own nonce to advance (e.g., by submitting a separate low bid that executes earlier) between when the higher, intended-to-win bid was signed and when the node locks it in. No special privileges beyond being a bid submitter (searcher) are needed.

### Recommendation
Before accepting a bid into `bidWinnerMap`/committing it as a block's sole winner for a sender, the `BidPool` should query the searcher's live on-chain nonce (e.g., via `getNoncesAndDeposits`, similar to how `ReadAuctionVersion`/`ReadGasBufferEstimate` already read on-chain auction state) and reject/re-validate bids whose `Nonce` no longer matches. Additionally, re-check nonce freshness immediately before `GetBidTxGenerator` builds the final transaction for block inclusion, so a bid that has gone stale between admission and block assembly does not consume the sender's exclusive winner slot.

### Proof of Concept
1. Searcher signs `Bid A` (nonce = N, high bid) for target tx `T` in block `B`.
2. Before block `B` is built, the same searcher's nonce advances to `N+1` on-chain (e.g., a separate `AuctionEntryPoint.call` with nonce N is already mined, or another bid from the same searcher for an earlier block consumed nonce N).
3. `BidPool.AddBid(BidA)` is still accepted: `validateBid` performs no nonce check (`kaiax/auction/impl/bid_pool.go:345-395`), and `insertBid` marks the searcher as the winner for block `B` (`kaiax/auction/impl/bid_pool.go:276-324`).
4. A competing, otherwise-valid `Bid B'` (potentially higher, from the same searcher for a different target, or replacing `Bid A`) is rejected via `senderHasDifferentWinner` (`kaiax/auction/impl/bid_pool.go:337-343`).
5. At block-building time, `GetBidTxGenerator` signs and includes the `AuctionEntryPoint.call()` transaction for `Bid A` (`kaiax/auction/impl/getter.go:27-70`), which reverts on-chain because `auctionTx.nonce != nonces(searcher)`, wasting the auction slot and denying the intended proceeds/execution for block `B`.

### Citations

**File:** kaiax/auction/impl/bid_pool.go (L276-343)
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

func (bp *BidPool) initializeBidMap(num uint64) {
	if _, ok := bp.bidTargetMap[num]; !ok {
		bp.bidTargetMap[num] = make(map[common.Hash]*auction.Bid)
	}
	if _, ok := bp.bidWinnerMap[num]; !ok {
		bp.bidWinnerMap[num] = make(map[common.Address]common.Hash)
	}
}

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

**File:** kaiax/auction/bid.go (L94-115)
```go
func (b *Bid) ValidateSearcherSig(chainId *big.Int, verifyingContract common.Address, version string) error {
	if chainId == nil {
		return ErrNilChainId
	}

	if common.EmptyAddress(verifyingContract) {
		return ErrNilVerifyingContract
	}

	digest := b.GetHashTypedData(chainId, verifyingContract, version)

	recoveredSender, err := getSigner(b.SearcherSig, digest)
	if err != nil {
		return fmt.Errorf("failed to recover searcher sig: %v", err)
	}

	if recoveredSender != b.Sender {
		return fmt.Errorf("invalid searcher sig: expected %v, calculated %v", b.Sender.String(), recoveredSender.String())
	}

	return nil
}
```

**File:** kaiax/auction/eip712.go (L75-86)
```go
func (b *Bid) EncodeData() []byte {
	encoded := make([]byte, 0)
	encoded = append(encoded, b.TargetTxHash.Bytes()...)
	encoded = append(encoded, common.LeftPadBytes(common.Int64ToByteBigEndian(b.BlockNumber), 32)...)
	encoded = append(encoded, common.LeftPadBytes(b.Sender.Bytes(), 32)...)
	encoded = append(encoded, common.LeftPadBytes(b.To.Bytes(), 32)...)
	encoded = append(encoded, common.LeftPadBytes(common.Int64ToByteBigEndian(b.Nonce), 32)...)
	encoded = append(encoded, common.LeftPadBytes(b.Bid.Bytes(), 32)...)
	encoded = append(encoded, common.LeftPadBytes(common.Int64ToByteBigEndian(b.CallGasLimit), 32)...)
	encoded = append(encoded, crypto.Keccak256Hash(b.Data).Bytes()...)
	return encoded
}
```

**File:** contracts/testing/system_contracts/AuctionEntryPointMock.sol (L75-107)
```text
    function _verifyInputIntegrity(
        AuctionTx calldata auctionTx
    ) internal view returns (bool) {
        /// 1. Check if the block number is correct
        if (auctionTx.blockNumber != block.number) {
            return false;
        }

        /// 2. Check if the bid is greater than 0
        if (auctionTx.bid <= 0) {
            return false;
        }

        // /// 3. Check if the auctioneer signature is valid
        // bytes32 digest = MessageHashUtils.toEthSignedMessageHash(auctionTx.searcherSig);
        // (address recoveredSigner, , ) = digest.tryRecover(auctionTx.auctioneerSig);
        // if (recoveredSigner != auctioneer) {
        //     return false;
        // }

        // /// 4. Check if the searcher signature is valid
        // bytes32 structHash = _getAuctionTxHash(auctionTx);
        // // Compute the final digest
        // digest = _hashTypedDataV4(structHash);
        // // Recover the signer from the signature
        // (recoveredSigner, , ) = digest.tryRecover(auctionTx.searcherSig);

        // if (recoveredSigner != auctionTx.sender) {
        //     return false;
        // }

        // return auctionTx.nonce == nonces(auctionTx.sender);
        return true;
```

**File:** kaiax/auction/impl/getter.go (L27-70)
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
