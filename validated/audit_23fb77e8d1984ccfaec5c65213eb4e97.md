### Title
Target transaction is broadcast to the public mempool before the accompanying bid is validated, defeating auction privacy and enabling sandwich attacks - ([File: kaiax/auction/impl/api.go])

### Summary
`AuctionAPI.SubmitBid` unconditionally decodes and submits the caller-supplied `targetTxRaw` to the node's transaction pool *before* the accompanying bid (signatures, bid amount, block-number window, size/gas limits) is validated. Any bidder can therefore force a target transaction into the public mempool by attaching a garbage/invalid bid, exposing the trade to the network without ever completing a valid auction round.

### Finding Description
`SubmitBid` performs two steps in this order:

1. Decode `TargetTxRaw`, verify only that its hash matches `TargetTxHash`, then immediately call `api.a.Backend.SendTx(ctx, targetTx)`, which inserts the transaction into the node's tx pool and causes it to propagate over the p2p network like any ordinary pending transaction.
2. Only afterwards is the bid itself validated via `bidPool.AddBid` → `validateBid`, which checks the block-number window, bid > 0, data/gas-limit caps, and — critically — the searcher and auctioneer signatures (`ValidateSearcherSig`, `ValidateAuctioneerSig`). [1](#0-0) 

Because step 1 happens unconditionally regardless of the outcome of step 2, an attacker can call `auction_submitBid` with:
- a syntactically valid `targetTxRaw` (the transaction they want protected/auctioned), and
- a deliberately invalid bid (bad `SearcherSig`/`AuctioneerSig`, zero bid, wrong block number, or oversized data),

and the target transaction is still pushed into the public mempool and gossiped to peers, while `AddBid` subsequently rejects the bid with an error such as `ErrInvalidSignature`/`ErrZeroBid`/`ErrInvalidBlockNumber`. [2](#0-1) 

The entire point of KIP-249's auction/bid design, as documented in the module README, is that a target transaction is only supposed to be executed together with the winning searcher bid, bundled atomically at block-building time, so it is never exposed as an ordinary, front-runnable mempool transaction: [3](#0-2) [4](#0-3) 

By reordering "send tx" ahead of "validate bid," this protection is bypassed: the moment `SubmitBid` is called, the target transaction is public regardless of whether the bid pool ever accepts a winning bid for it. This is directly analogous to the reported incident, where a relay disclosed block/payload content to a proposer before it was actually validated/finalized, letting a malicious party read and act on soon-to-be-committed transaction data ahead of legitimate execution — enabling a sandwich attack. Here, the "relay" is the auction RPC endpoint and the "invalid but still leaked payload" is the target transaction sent to the mempool despite an invalid, doomed-to-fail bid.

### Impact Explanation
Any party who can reach the `auction_submitBid` RPC (an auction bidder, by design an unprivileged caller of this API per the module's intended use) can force disclosure of a target transaction into the public mempool without needing to win, or even submit, a valid auction bid. Once in the public mempool, the transaction is visible to every peer/validator, who can trivially front-run or sandwich it using ordinary mempool-observation techniques — the exact MEV-extraction scenario the auction module exists to prevent. This results in unauthorized value extraction from the user who believed their trade was protected by the private bid/auction mechanism, which matches the "concrete unauthorized value movement" impact class.

### Likelihood Explanation
The issue requires only a single RPC call with attacker-controlled parameters (a normal, otherwise-valid target transaction plus any deliberately malformed bid field) — no special privileges, timing races, or validator/relay collusion are needed. It is deterministically reachable on every call to `SubmitBid`, since the ordering of "send tx" then "validate bid" is unconditional in the code path.

### Recommendation
Validate the bid (signatures, bid amount, block-number window, size/gas-limit caps) in `SubmitBid` *before* calling `Backend.SendTx` on the decoded target transaction. Only submit/broadcast the target transaction once the accompanying bid has passed all `validateBid` checks (or at minimum, once signatures are verified), so that an attacker cannot force premature public disclosure of a transaction by pairing it with an intentionally invalid bid.

### Proof of Concept
1. Craft a normal, valid, signed transaction `T` (e.g., a swap) intended to be protected by the auction.
2. Call `auction_submitBid` with `targetTxRaw = RLP(T)`, `targetTxHash = T.Hash()`, and any bid fields but with an invalid `searcherSig`/`auctioneerSig` (e.g., all-zero bytes) or `bid = 0`.
3. Observe that `api.a.Backend.SendTx(ctx, targetTx)` succeeds and `T` is inserted into the local tx pool and propagated to peers (per [5](#0-4) ), while the subsequent `bidPool.AddBid` call returns an error (e.g., `ErrInvalidSignature`) as shown in `validateBid` ( [6](#0-5) ).
4. `T` is now a normal pending transaction visible to all mempool observers before any winning bid exists, and can be sandwiched using standard front-running.

### Citations

**File:** kaiax/auction/impl/api.go (L118-141)
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

**File:** kaiax/auction/README.md (L5-11)
```markdown
## Concepts

The bid is a data that contains the information to generate a transaction to be executed right after the target transaction is executed. All the winning bids is sent by `Auctioneer`, which is an independent service that is responsible for processing auction and submit winner's bid to the Kaia client (CN). The bid pool checks the `Auctioneer`'s signature to ensure the bid is coming from the `Auctioneer`.

![auction_topology](./auction_topology.png)

As shown in the topology, the `Auctioneer` won't connect to the PN or EN, and the auction module itself is disabled on PN and EN.
```

**File:** kaiax/auction/README.md (L26-33)
```markdown
## Block building rules

Upon detection of target transaction from the tx pool, the following logics are executed:

- The corresponding bid is retrieved from the bid pool.
- If the bid is found, a new bundle is generated which contain `[BidTx]`.
- If the target transaction is not found in the bid pool, the bid will be ignored.

```
