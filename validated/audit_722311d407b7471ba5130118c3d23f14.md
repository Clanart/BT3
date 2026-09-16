### Title
Auction `SubmitBid` relays the target transaction to the tx pool before the searcher/auctioneer bid signatures are validated - ([File: kaiax/auction/impl/api.go])

### Summary
`AuctionAPI.SubmitBid` performs the state-changing action (submitting the target transaction to the local tx pool, which then propagates it to the network / makes it eligible for inclusion) *before* it validates the accompanying bid's `SearcherSig`/`AuctioneerSig`. This mirrors the CVE-2018-18891 bug class of "authorization check occurs too late": the side effect happens unconditionally, and only afterwards is the authorization (here, the Auctioneer's signature, which is the module's actual trust boundary) checked and possibly rejected.

### Finding Description
`SubmitBid` executes in two ordered steps:

1. Decode `targetTxRaw` and unconditionally call `api.a.Backend.SendTx(ctx, targetTx)`, which inserts/broadcasts the transaction via the ordinary tx pool path.
2. Only afterwards does it build the `Bid` and call `api.a.bidPool.AddBid(bid)`, which runs `validateBid` → `validateBidSigs`, the function that actually checks `bid.SearcherSig` and, critically, `bid.AuctioneerSig` against the configured `Auctioneer` address. [1](#0-0) 

Per the module design, RPC access to `auction_submitBid` is *not* the trust boundary — the `AuctioneerSig` is: "The bid pool checks the `Auctioneer`'s signature to ensure the bid is coming from the `Auctioneer`." [2](#0-1)  The signature check itself lives in `validateBidSigs`, which is only reached in step 2, after the target transaction has already been sent: [3](#0-2) 

Because `SendTx` is called before `AddBid`/`validateBidSigs`, any caller with access to the `auction` namespace (registered as `Public: false`, i.e., reachable on IPC or any endpoint where the "auction" module is explicitly whitelisted) [4](#0-3)  can get an arbitrary transaction submitted to the tx pool while supplying a bogus/invalid `AuctioneerSig` (or `SearcherSig`), i.e., without ever presenting a bid that the auction module considers authentic. The function only aborts *before* `SendTx` for a decode error or tx-hash mismatch; once the tx itself is well-formed it is relayed regardless of whether the bid is later rejected.

### Impact Explanation
The auction relay path is designed so that only bids carrying a valid `Auctioneer` signature are supposed to trigger transaction relaying through this privileged path (which also feeds `ExtractTxBundles`/bundle building for the block proposer). Because the authorization check is performed after the side effect, the intended gatekeeping ("only the real Auctioneer's approved bids reach this relay/bundling flow") is bypassed: an unauthenticated caller of the `auction` RPC can force target-transaction relaying/propagation and consume the auction subsystem's tx-pool insertion path with garbage bid data, defeating the auctioneer-authorization model described for this module. This is a state-mutation-before-authorization ordering bug consistent with the CVE-2018-18891 class ("authentication check occurs too late").

### Likelihood Explanation
Reachable via a single RPC call (`auction_submitBid`) by any caller with access to that namespace, requiring only a syntactically valid target transaction and arbitrary (invalid) bid signature fields — no privileged role or node compromise needed.

### Recommendation
Reorder `SubmitBid` so that `bidPool.AddBid` (including `validateBidSigs`, i.e., `SearcherSig`/`AuctioneerSig` verification) is performed and succeeds *before* `api.a.Backend.SendTx` is invoked, so the target transaction is only relayed once the accompanying bid has been authenticated as coming from the legitimate `Auctioneer`.

### Proof of Concept
1. Craft any well-formed, validly-signed `targetTxRaw` (its own tx signature is unrelated to the auction bid).
2. Call `auction_submitBid` with that `targetTxRaw` and a `SearcherSig`/`AuctioneerSig` that do not correspond to the real Auctioneer (e.g., random bytes of correct length).
3. Observe: `SendTx` succeeds and the target transaction is accepted into the tx pool [5](#0-4) , while the subsequent `AddBid` call fails signature validation in `validateBidSigs` [3](#0-2)  — demonstrating the transaction relay occurred despite failed authorization.

### Citations

**File:** kaiax/auction/impl/api.go (L48-57)
```go
func (a *AuctionModule) APIs() []rpc.API {
	return []rpc.API{
		{
			Namespace: "auction",
			Version:   "1.0",
			Service:   newAuctionAPI(a),
			Public:    false,
		},
	}
}
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

**File:** kaiax/auction/README.md (L1-24)
```markdown
# kaiax/auction

This module implements the Kaia client (CN) for processing the auction specified by [KIP-249](https://kips.kaia.io/KIPs/kip-249).

## Concepts

The bid is a data that contains the information to generate a transaction to be executed right after the target transaction is executed. All the winning bids is sent by `Auctioneer`, which is an independent service that is responsible for processing auction and submit winner's bid to the Kaia client (CN). The bid pool checks the `Auctioneer`'s signature to ensure the bid is coming from the `Auctioneer`.

![auction_topology](./auction_topology.png)

As shown in the topology, the `Auctioneer` won't connect to the PN or EN, and the auction module itself is disabled on PN and EN.

## Bid pool validation rules

A bid pool is responsible for managing the valid bids from the `Auctioneer`. The bid must satisfy the following rules:

1. The `bid.Sender` must not be in the winner list of the same block number if the new bid doesn't have the same target block and hash as the previous bid.
2. The `bid.BlockNumber` must be in range of `[currentBlockNumber + 1, currentBlockNumber + allowFutureBlock]`.
3. The `bid.Bid` must be greater than 0.
4. The `bid.Data` size must be less than or equal to `BidTxMaxDataSize`.
5. The `bid.CallGasLimit` must be less or equal to `BidTxMaxCallGasLimit`.
6. The `bid.SearcherSig` and `bid.AuctioneerSig` must be valid.

Please note that `Auctioneer` also validates the searcher's bid according to the KIP-249.
```

**File:** kaiax/auction/impl/bid_pool.go (L397-419)
```go
func (bp *BidPool) validateBidSigs(bid *auction.Bid) error {
	bp.auctionInfoMu.RLock()
	defer bp.auctionInfoMu.RUnlock()

	if bid.SearcherSig == nil || len(bid.SearcherSig) != crypto.SignatureLength {
		return auction.ErrInvalidSearcherSig
	}
	if bid.AuctioneerSig == nil || len(bid.AuctioneerSig) != crypto.SignatureLength {
		return auction.ErrInvalidAuctioneerSig
	}

	// Verify the EIP712 signature.
	if err := bid.ValidateSearcherSig(bp.ChainConfig.ChainID, bp.auctionEntryPoint, bp.auctionEntryPointVersion); err != nil {
		return err
	}

	// Verify the auctioneer signature.
	if err := bid.ValidateAuctioneerSig(bp.auctioneer); err != nil {
		return err
	}

	return nil
}
```
