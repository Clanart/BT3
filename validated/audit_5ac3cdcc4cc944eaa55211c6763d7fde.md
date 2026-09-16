## Analysis

The report's bug class is: *a value that was locked in / committed to (a stored price agreed at request time) is silently ignored, and a different, currently-computed value is substituted instead, harming the party who relied on the commitment.*

The reachable Kaia analog is in the **auction module** (KIP-249), specifically in how a winning searcher's bid is turned into an on-chain `BidTx`.

### Root cause

A searcher's `Bid` includes a `MaxGasPrice` field that the searcher explicitly signs as part of the EIP-712 typed data for v3.0 auctions — it is the maximum gas price the searcher has committed to pay: [1](#0-0) [2](#0-1) 

The proposer accepts and stores this bid in the bid pool, but `validateBid` never checks `bid.MaxGasPrice` against anything (current base fee, target tx price, etc.) — it only checks block-number range, non-zero bid amount, data size, gas-limit cap, and signatures: [3](#0-2) 

When the proposer later materializes the winning bid into an actual `BidTx` to insert into the block, `GetBidTxGenerator` does **not** use the searcher's signed `MaxGasPrice` at all. Instead it sets the `BidTx`'s `GasFeeCap`/`GasTipCap` to the **target transaction's** current gas price fields: [4](#0-3) 

```go
tx, err := types.NewTransactionWithMap(types.TxTypeEthereumDynamicFee, map[types.TxValueKeyType]interface{}{
    ...
    types.TxValueKeyGasFeeCap:  tx.GasFeeCap(),
    types.TxValueKeyGasTipCap:  tx.GasTipCap(),
    ...
})
```

`tx` here is the parameter passed in — the *target* transaction that triggered the bid — not the bid's own committed `MaxGasPrice`. This is structurally identical to `AccountableAsyncRedeemVault::fulfillRedeemRequest` ignoring the locked `processingMode`/stored price and substituting `sharePrice()` (a different, current value) at settlement time.

### Impact

The `BidTx` that is actually signed and broadcast by the node's own key (`a.InitOpts.NodeKey`, held by the CN operator acting as executor of the auctioneer-approved bid) pays whatever gas price the target transaction happens to carry, not the price ceiling the searcher cryptographically committed to via `MaxGasPrice`. If the target transaction's `GasFeeCap`/`GasTipCap` exceeds the searcher's signed `MaxGasPrice`, the searcher's bid is executed at a higher effective gas price than they agreed to — the guarantee encoded in the EIP-712 signature (and validated on-chain via `AuctionEntryPoint.call`, which is expected to check `maxGasPrice` per KIP-249 v3.0) is broken client-side before the transaction is even built, since the client never propagates the field into the constructed transaction. This is unauthorized fee/value extraction from an auction bidder — a value reachable purely by submitting a bid via `auction_submitBid` (an unprivileged, permissionless RPC call) and having it selected as the winning bid.

### Likelihood

Any searcher submitting a v3.0 bid with a `MaxGasPrice` lower than the eventual target transaction's `GasFeeCap`/`GasTipCap` is affected; no special privilege, timing race, or malicious actor is required — it is a deterministic code path taken every time a v3.0 bid is turned into a `BidTx`.

### Recommendation
`GetBidTxGenerator` (and/or `EncodeAuctionCallData`) should honor `bid.MaxGasPrice` when constructing/encoding the `BidTx`, e.g., cap the constructed transaction's `GasFeeCap`/`GasTipCap` at `bid.MaxGasPrice`, and `validateBid` should reject bids whose `MaxGasPrice` cannot satisfy the current/target-tx-required price before admission, ensuring the signed searcher commitment is actually enforced end-to-end.

### Proof of Concept
1. Searcher signs and submits (via `auction_submitBid`) a v3.0 `Bid` with `MaxGasPrice = X` for a specific `targetTxHash`/`blockNumber`, per `ToBid`/`SubmitBid`: [5](#0-4) .
2. The bid passes `validateBid` unconditionally with respect to `MaxGasPrice` (no such check exists): [3](#0-2) .
3. Auctioneer/proposer picks this bid as winner for the block; `ExtractTxBundles` calls `GetBidTxGenerator(tx, bid)` where `tx` is the target transaction carrying `GasFeeCap`/`GasTipCap` = Y > X: [6](#0-5) .
4. The generated, signed `BidTx` is built with `GasFeeCap = Y`, `GasTipCap = Y` (ignoring the searcher's signed `MaxGasPrice = X`): [7](#0-6) .
5. The searcher's bid, whose signed commitment capped the price at `X`, is executed on-chain paying `Y`, in violation of their signed max-price guarantee.

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

**File:** kaiax/auction/eip712.go (L96-118)
```go
type bidV3 struct{ *Bid }

func (b bidV3) EncodeType() []byte {
	return auctionTypeHashV3.Bytes()
}

func (b bidV3) EncodeData() []byte {
	maxGasPrice := b.MaxGasPrice
	if maxGasPrice == nil {
		maxGasPrice = new(big.Int)
	}
	encoded := make([]byte, 0, 10*32)
	encoded = append(encoded, b.TargetTxHash.Bytes()...)
	encoded = append(encoded, common.LeftPadBytes(common.Int64ToByteBigEndian(b.BlockNumber), 32)...)
	encoded = append(encoded, common.LeftPadBytes(b.Sender.Bytes(), 32)...)
	encoded = append(encoded, common.LeftPadBytes(b.To.Bytes(), 32)...)
	encoded = append(encoded, common.LeftPadBytes(common.Int64ToByteBigEndian(b.Nonce), 32)...)
	encoded = append(encoded, common.LeftPadBytes(b.BidData.Bid.Bytes(), 32)...)
	encoded = append(encoded, common.LeftPadBytes(maxGasPrice.Bytes(), 32)...)
	encoded = append(encoded, common.LeftPadBytes(common.Int64ToByteBigEndian(b.CallGasLimit), 32)...)
	encoded = append(encoded, crypto.Keccak256Hash(b.Data).Bytes()...)
	return encoded
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

**File:** kaiax/auction/impl/getter.go (L27-67)
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
```

**File:** kaiax/auction/impl/api.go (L88-142)
```go
func ToBid(bidInput BidInput) *auction.Bid {
	bidData := auction.BidData{
		TargetTxHash:  bidInput.TargetTxHash,
		BlockNumber:   bidInput.BlockNumber,
		Sender:        bidInput.Sender,
		To:            bidInput.To,
		Nonce:         bidInput.Nonce,
		Bid:           bidInput.Bid.ToInt(),
		CallGasLimit:  bidInput.CallGasLimit,
		Data:          bidInput.Data,
		SearcherSig:   bidInput.SearcherSig,
		AuctioneerSig: bidInput.AuctioneerSig,
	}
	if bidInput.MaxGasPrice != nil {
		bidData.MaxGasPrice = bidInput.MaxGasPrice.ToInt()
	}
	return &auction.Bid{BidData: bidData}
}

func toTx(targetTxRaw []byte) (*types.Transaction, error) {
	if len(targetTxRaw) == 0 {
		return nil, ErrEmptyTargetTxRaw
	}
	tx := new(types.Transaction)
	if err := rlp.DecodeBytes(targetTxRaw, tx); err != nil {
		return nil, err
	}
	return tx, nil
}

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

**File:** kaiax/auction/impl/builder.go (L29-71)
```go
var _ kaiax.TxBundlingModule = (*AuctionModule)(nil)

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
