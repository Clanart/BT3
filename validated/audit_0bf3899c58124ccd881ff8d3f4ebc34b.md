### Title
Auction bid's `to` (call destination) is never bound to the target transaction, allowing a searcher-controlled bid to execute against any address unrelated to the auctioned target tx - (File: kaiax/auction/impl/bid_pool.go)

### Summary
`ODSafeManager.transferCollateral()`'s root cause was that a destination address was accepted and acted upon without validating it against the invariant that it must correspond to a manager-tracked entity. In Kaia's auction module, `BidData.To` (the contract the searcher's bid will call on-chain) is signed by the searcher and validated only for signature integrity, never checked against the actual auctioned target transaction or any other invariant, before it is broadcast to the network and turned into an executable `call()` against `AuctionEntryPoint`.

### Finding Description
A bid is defined in [1](#0-0)  with fields `TargetTxHash`, `Sender`, `To`, `Nonce`, `Bid`, `Data`, etc. `BidPool.validateBid()` only checks: bid not already known, sender not already a different winner for the block, block-number range, `bid.Bid > 0`, `Data`/`CallGasLimit` size limits, and searcher/auctioneer signature validity [2](#0-1) . At no point is `bid.To` compared against the target transaction (`bid.TargetTxHash`) recipient, or validated to be a legitimate/expected contract. The signature check (`ValidateSearcherSig`) only proves the searcher authorized this specific `(TargetTxHash, To, Data, ...)` tuple - it does not constrain `To` to any protocol invariant, since `To` is part of the freely-chosen signed payload [3](#0-2) .

The accepted bid is later turned into an on-chain transaction calling `AuctionEntryPoint.call(auctionTx)` where `to` is passed through verbatim [4](#0-3) , and this bundle is unconditionally placed immediately after the target transaction hash in block construction via `ExtractTxBundles`/`GetTargetTxMap` [5](#0-4) , with the block builder only enforcing that the target tx precedes and succeeded (`shouldDiscardBundle`), never that `bid.To` relates to the target tx's `to` field [6](#0-5) .

Because there is no on-chain-facing linkage requirement between the auctioned target transaction and the bid's execution target, a searcher can win/attach an auction slot for a target transaction while directing the paid-for privileged execution slot (guaranteed-next-tx-after-target) at a completely unrelated address of their choosing. This mirrors the OpenDollar defect: a field that is expected/implied (by the KIP-249 concept of "bid to execute right after the target transaction") to correspond to a specific managed relationship (bid execution ⇔ target transaction) is accepted and acted upon without that linkage being enforced anywhere in the client.

### Impact Explanation
This breaks the implicit invariant of the auction design ("the bid is a data that contains the information to generate a transaction to be executed right after the target transaction," per the module's own README [7](#0-6) ) that bid execution should be tied to the outcome/context of the specific target transaction it bid against. Since `bid.To`/`bid.Data` are fully attacker (searcher) chosen and unconstrained beyond signature/size checks, the guaranteed execution slot immediately after a target transaction can be redirected to call arbitrary contracts, independent of what was actually being "auctioned." Combined with the auctioneer-signature model (which only proves auctioneer approval of the *tuple as submitted*, not that `To` is sane), this allows fee/priority abuse of the KIP-249 mechanism: a party can win an auction slot (paying `bid.Bid`) for one target transaction context while using the slot to execute against an entirely different destination, undermining the fairness/ordering guarantees the auction is meant to provide to searchers and the chain's MEV-extraction model.

### Likelihood Explanation
Reachable directly by an unprivileged auction bidder/searcher via the public `auction_submitBid` RPC or peer-to-peer `BidMsg` (subject to only signature and size checks), requiring no special privileges beyond holding a valid searcher key and auctioneer co-signature for the bid tuple - which the searcher fully controls the content of (including `To`) before requesting the auctioneer's signature. No additional consensus-level or admin verification exists between bid acceptance and block inclusion, comparable to the "M" severity assigned to the original OpenDollar finding since it does not directly cause fund loss but violates a documented protocol invariant.

### Recommendation
Add a validation step in `BidPool.validateBid()` (or in `ExtractTxBundles`/block-building) requiring that `bid.To` correspond to an expected value tied to the target transaction (e.g., matching `targetTx.To()`, or otherwise proven to be an intended, protocol-recognized recipient), so that the auction's guaranteed execution slot cannot be redirected to an address unrelated to the transaction that was actually being bid on.

### Proof of Concept
1. A searcher observes a pending `targetTx` in the mempool and computes `TargetTxHash`.
2. The searcher crafts a `BidData` with `To` set to an address of their choosing (e.g., a contract they control), `Data` for an arbitrary call, and gets it EIP-712 signed by themselves and countersigned by the `Auctioneer` (who only checks bid economics per KIP-249, not that `To` relates to `targetTx`).
3. Submit via `auction_submitBid`; `SubmitBid` → `AddBid` → `validateBid` passes since none of its checks reference `bid.To` versus `targetTx.To()` [8](#0-7) [9](#0-8) .
4. During block building, `ExtractTxBundles` finds the bid keyed by `targetTx.Hash()` and creates a bundle that places the generated bid transaction (calling `AuctionEntryPoint.call()` with the attacker-chosen `To`/`Data`) immediately after `targetTx` [10](#0-9) .
5. The bundle executes on-chain, calling the attacker's chosen `To` with attacker `Data`, entirely decoupled from the actual `targetTx`'s recipient/purpose, while consuming the guaranteed "next after target" execution slot that the auction was designed to allocate for MEV tied to that specific target transaction.

### Citations

**File:** kaiax/auction/bid.go (L32-44)
```go
type BidData struct {
	TargetTxHash  common.Hash    `json:"targetTxHash"`
	BlockNumber   uint64         `json:"blockNumber"`
	Sender        common.Address `json:"sender"`
	To            common.Address `json:"to"`
	Nonce         uint64         `json:"nonce"`
	Bid           *big.Int       `json:"bid"`
	MaxGasPrice   *big.Int       `json:"maxGasPrice,omitempty"`
	CallGasLimit  uint64         `json:"callGasLimit"`
	Data          []byte         `json:"data"`
	SearcherSig   []byte         `json:"searcherSig"`
	AuctioneerSig []byte         `json:"auctioneerSig"`
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

**File:** kaiax/auction/impl/bid_pool.go (L250-274)
```go
// AddBid adds a bid to the bid pool.
// Required mutex is locked in each function.
func (bp *BidPool) AddBid(bid *auction.Bid) (common.Hash, error) {
	if atomic.LoadUint32(&bp.running) == 0 {
		return common.Hash{}, auction.ErrAuctionPaused
	}

	if err := bp.validateBid(bid); err != nil {
		return common.Hash{}, err
	}

	if err := bp.insertBid(bid); err != nil {
		return common.Hash{}, err
	}

	gasLimit, err := bp.getBidTxGasLimit(bid)
	if err != nil {
		return common.Hash{}, err
	}
	bid.SetGasLimit(gasLimit)

	bp.newBidCh <- bid

	return bid.Hash(), nil
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

**File:** kaiax/auction/impl/builder.go (L31-69)
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
```

**File:** work/worker.go (L956-971)
```go
func (env *Task) shouldDiscardBundle(bundle *builder.Bundle) (bool, error) {
	if !bundle.TargetRequired {
		return false, nil
	}
	if env.tcount == 0 {
		return bundle.TargetTxHash != common.Hash{}, fmt.Errorf("target tx %s does not precede the bundle", bundle.TargetTxHash.Hex())
	} else {
		// if `env.tcount` is not zero, the `bundle.TargetTxHash` must not be empty hash
		if bundle.TargetTxHash != env.txs[env.tcount-1].Hash() {
			return true, fmt.Errorf("target tx %s does not precede the bundle", bundle.TargetTxHash.Hex())
		}
		if env.receipts[env.tcount-1].Status != types.ReceiptStatusSuccessful {
			return true, fmt.Errorf("target tx %s failed with status %d", bundle.TargetTxHash.Hex(), env.receipts[env.tcount-1].Status)
		}
	}
	return false, nil
```

**File:** kaiax/auction/README.md (L7-7)
```markdown
The bid is a data that contains the information to generate a transaction to be executed right after the target transaction is executed. All the winning bids is sent by `Auctioneer`, which is an independent service that is responsible for processing auction and submit winner's bid to the Kaia client (CN). The bid pool checks the `Auctioneer`'s signature to ensure the bid is coming from the `Auctioneer`.
```

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
