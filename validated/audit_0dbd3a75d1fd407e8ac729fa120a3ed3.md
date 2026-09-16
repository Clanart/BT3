## Finding

The AMM report's core complaint — no protocol-level slippage/price-limit protection so a caller's actual cost can be unpredictable — maps directly onto `kaiax/auction`'s bid tx construction, where the `MaxGasPrice` field a searcher signs into their bid is *never* enforced anywhere in the bid pool or the bid transaction generator.

### Title
Searcher's signed `MaxGasPrice` cap is never enforced when constructing/validating the BidTx, making the searcher's actual gas cost unbounded and unpredictable - (File: `kaiax/auction/impl/getter.go`)

### Summary
`Bid.MaxGasPrice` (v3.0 bids, KIP-249) is meant to let a searcher cap the gas price they are willing to pay for their bid transaction, analogous to a caller specifying a "price limit or maximum amount of collateral to be spent" as recommended in the AMM report. The EIP-712 digest that the searcher signs binds `MaxGasPrice` [1](#0-0) , so the searcher believes they are bounding their cost. However, when the actual `BidTx` is built by `GetBidTxGenerator`, the gas price fields are copied from the **target transaction**, not from `bid.MaxGasPrice`, and `bid.MaxGasPrice` is not consulted at all: [2](#0-1) .

### Finding Description
`GetBidTxGenerator` builds the `BidTx` with `types.TxValueKeyGasFeeCap: tx.GasFeeCap()` and `types.TxValueKeyGasTipCap: tx.GasTipCap()`, where `tx` is the target transaction the searcher is bidding to follow, not any value derived from the bid itself [3](#0-2) .

`BidPool.validateBid` — the only validation gate for an incoming bid — checks sender-winner conflicts, block-number range, `bid.Bid > 0`, data size, and call-gas-limit, and searcher/auctioneer signatures, but never checks or uses `bid.MaxGasPrice` [4](#0-3) . Grepping the whole `kaiax/auction` module confirms `MaxGasPrice` only appears in the EIP-712 encoding and the RPC input/decoding paths (`bid.go`, `eip712.go`, `api.go`) — it is read from RPC input and hashed into the signature, but is dropped after that; it never flows into the actual transaction that gets built, broadcast, and paid for [5](#0-4) .

Since the target transaction's `GasFeeCap`/`GasTipCap` is set entirely by an unrelated third-party sender (not the searcher, not the auctioneer) and can be arbitrarily high (subject only to normal tx-pool acceptance rules), the searcher's node-signed `BidTx` — which the searcher's own `NodeKey`/balance ultimately pays gas for — inherits a gas price the searcher never agreed to and has no way to cap. This is the same "unpredictable/unbounded cost due to a value the caller does not control" class the AMM report describes for `addLiquidity`/`removeLiquidity`, except here the "collateral spent" is real KAIA gas fees paid by the searcher's registered address for `BidTx` inclusion.

### Impact Explanation
A searcher who submits a `MaxGasPrice`-capped v3.0 bid can be forced to pay gas fees for the `BidTx` far above what they signed for, simply because the (attacker-influenced or organically high-fee) target transaction has a high `GasFeeCap`. Because bids require the signer's KAIA balance (`AuctionLenderMinBal`) to cover the `BidTx` [6](#0-5) , this results in unauthorized/unexpected value extraction from the searcher and defeats the entire purpose of the `MaxGasPrice` field defined by KIP-249.

### Likelihood Explanation
Any unprivileged party can submit a target transaction with an arbitrarily high `GasFeeCap`/`GasTipCap` (bounded only by normal pool acceptance rules, e.g. Magma base-fee rules) and any searcher/auctioneer using the v3.0 `MaxGasPrice` field will be exposed on every bid that targets such a transaction — no special privilege or timing is required beyond normal bid submission via `auction_submitBid`.

### Recommendation
Enforce `bid.MaxGasPrice` in `BidPool.validateBid` (reject or cap incoming bids whose signed `MaxGasPrice` is inconsistent with acceptance) and in `GetBidTxGenerator`, clamp the constructed `BidTx`'s `GasFeeCap`/`GasTipCap` to `min(bid.MaxGasPrice, targetTx.GasFeeCap())` (falling back to existing behavior only when `MaxGasPrice` is unset for v2.1 bids), so the searcher's actual paid gas price never exceeds what they cryptographically committed to.

### Proof of Concept
1. Searcher signs a v3.0 bid with `MaxGasPrice = X` via `auction_submitBid`, targeting `targetTx` [7](#0-6) .
2. An unrelated party (or the searcher's competitor) ensures/observes that `targetTx.GasFeeCap()` / `GasTipCap()` is set far above `X`.
3. When the bid wins, `GetBidTxGenerator` builds `BidTx` with `GasFeeCap = targetTx.GasFeeCap()`, `GasTipCap = targetTx.GasTipCap()` — ignoring `X` entirely [2](#0-1) .
4. The searcher's node key pays gas for `BidTx` at the inflated price, exceeding the `MaxGasPrice` they signed and validated against in `validateBid`, which never checked it [4](#0-3) .

### Citations

**File:** kaiax/auction/eip712.go (L102-118)
```go
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

**File:** kaiax/auction/impl/getter.go (L27-59)
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

**File:** kaiax/auction/README.md (L42-44)
```markdown
- Dependencies:
  - ChainConfig: To generate the latest signer.
  - NodeKey: For BidTxGenerator. The corresponding address should hold at least `AuctionLenderMinBal` of KAIA.
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
