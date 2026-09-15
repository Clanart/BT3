### Title
`bid.MaxGasPrice` is committed to in the searcher's EIP-712 signature but never validated by the bid pool or enforced when constructing the actual bid transaction - ([File: kaiax/auction/impl/bid_pool.go], [File: kaiax/auction/impl/getter.go])

### Summary
The v3.0 auction protocol (KIP-249) added a `MaxGasPrice` field to `AuctionTx`/`Bid` that the searcher signs via EIP-712 as part of the bid terms [1](#0-0) . This mirrors the Seaport pattern of a field (`numerator`/`denominator`) that is part of the authenticated order data and is expected to gate/validate subsequent behavior, but for the `CONTRACT` code path was never actually checked before being relied on. In Kaia's auction module, `MaxGasPrice` is likewise never validated by `BidPool.validateBid` and is never consulted when the actual bid transaction is built, so the signed/committed term and the executed transaction can diverge.

### Finding Description
`Bid.BidData.MaxGasPrice` is part of the struct that is hashed and EIP-712-signed by the searcher (`bidV3.EncodeData`), and it is transmitted end-to-end through `EncodeAuctionCallData`/`DecodeAuctionCallData` into the on-chain `AuctionEntryPoint.call()` invocation [2](#0-1) [3](#0-2) . This makes it appear to be a binding, validated constraint of the auction bid — analogous to `numerator`/`denominator` being part of the signed `AdvancedOrder` data in Seaport.

However:
1. `BidPool.validateBid`, which enforces all the documented bid-pool admission rules (sender/winner checks, block range, non-zero bid, data size, gas limit, signatures), never reads or checks `bid.MaxGasPrice` at all [4](#0-3) .
2. When the node actually builds the bid transaction to inject into the block via `GetBidTxGenerator`, the gas price fields (`GasFeeCap`, `GasTipCap`) are copied from the **target transaction** (`tx.GasFeeCap()`/`tx.GasTipCap()`), not derived from or bounded by `bid.MaxGasPrice` in any way [5](#0-4) .

So the field that the searcher cryptographically committed to (as their accepted upper bound on gas price for this auction) plays no role whatsoever in either (a) admission to the bid pool, or (b) construction of the transaction that is ultimately executed. This is the same class of defect as the report: a per-type/per-version field that is recorded/emitted as part of validated, signed data is never cross-checked against what actually happens during execution, allowing the emitted/recorded state (the signed bid, and the on-chain call which encodes `maxGasPrice`) to diverge from the executed reality (a bid tx whose gas price bears no relation to that cap).

### Impact Explanation
Because gas price for the actual bid transaction tracks the target transaction's fee cap rather than the searcher-approved `MaxGasPrice`, a block proposer (or the auction module code itself, in a future contract version that relies on this field for a fee/reward calculation) can produce and settle bid transactions whose effective price exceeds what the searcher agreed to sign for. This creates a mismatch between the auction terms searchers believe are enforced (backed by their own EIP-712 signature) and what is actually settled on-chain, which falls under "fee ... or auction settlement" divergence: the recorded/signed term is decoupled from the executed value, similar to how Seaport's event emission diverged from the order that was actually processed.

### Likelihood Explanation
This path is reachable purely from the `auction_submitBid` RPC by any Auctioneer/searcher submitting a v3 bid with a `MaxGasPrice`; every bid flows through `BidPool.AddBid → validateBid` and later `GetBidTxGenerator`, both of which are shown to ignore the field [6](#0-5) . No special privilege beyond normal bid submission is required to trigger the code path where the field is silently dropped from enforcement.

### Recommendation
Mirror the report's two remediation options:
- Hoist a check into `BidPool.validateBid` (or into `GetBidTxGenerator`) that rejects/adjusts bid transactions whose actual gas price would exceed `bid.MaxGasPrice` when `MaxGasPrice` is non-nil/non-zero, i.e., validate it the same way `bid.Bid`, `bid.CallGasLimit`, and `bid.Data` size are validated in `validateBid`.
- Alternatively, explicitly enforce in `GetBidTxGenerator` that the generated transaction's `GasFeeCap`/`GasTipCap` is capped at `bid.MaxGasPrice` for v3 bids, so the signed term is actually the term that is executed.

### Proof of Concept
1. A searcher submits a v3 bid via `auction_submitBid` with `MaxGasPrice` set to a low value (e.g., 1 gwei), signing the EIP-712 digest that includes this cap [2](#0-1) .
2. `BidPool.AddBid` → `validateBid` accepts the bid without ever inspecting `MaxGasPrice` [4](#0-3) .
3. When the target transaction is detected in a block with a high `GasFeeCap`/`GasTipCap` (e.g., 1000 gwei), `GetBidTxGenerator` builds the bid transaction using that target transaction's fee cap/tip cap values, completely independent of the signed 1 gwei cap [5](#0-4) .
4. The bid transaction is signed by the node's key and included on-chain at a gas price far exceeding what the searcher's signature attested to, with no validation step anywhere in the pipeline rejecting or truncating it to the committed cap.

**Note on uncertainty:** I was not able to inspect the production (non-mock) `AuctionEntryPoint` v3.0 Solidity contract's `call()` implementation to confirm whether it independently re-validates `maxGasPrice` on-chain against `tx.gasprice`. If it does, the impact is likely limited to bid-transaction reverts (availability/DoS on searcher bids) rather than direct fee-abuse; if it does not, the field is effectively decorative and offers no real protection to searchers, which is the core issue described above. This should be verified against the actual system contract source before finalizing severity.

### Citations

**File:** kaiax/auction/eip712.go (L26-35)
```go
const (
	auctionType      = "AuctionTx(bytes32 targetTxHash,uint256 blockNumber,address sender,address to,uint256 nonce,uint256 bid,uint256 callGasLimit,bytes data)"
	auctionTypeV3    = "AuctionTx(bytes32 targetTxHash,uint256 blockNumber,address sender,address to,uint256 nonce,uint256 bid,uint256 maxGasPrice,uint256 callGasLimit,bytes data)"
	EIP712DomainType = "EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)"
	auctionName      = "KAIA_AUCTION"
	// Auction version strings match the on-chain AUCTION_VERSION view of the
	// corresponding entry-point contract. v3.0 selects the typehash that
	// includes maxGasPrice.
	AuctionVersionV2 = "0.0.1"
	AuctionVersionV3 = "0.0.2"
```

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

**File:** blockchain/system/auction.go (L83-103)
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
```

**File:** kaiax/auction/impl/bid_pool.go (L250-273)
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
