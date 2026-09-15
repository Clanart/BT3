### Title
`Bid.Equals` compares only `BlockNumber`/`TargetTxHash`, letting a searcher bypass the one-winning-bid-per-sender-per-block check with a different bid - ([File: kaiax/auction/impl/bid_pool.go])

### Summary
The auction module's bid-admission logic is intended to allow only one *winning* bid per sender per block: if a sender already has a recorded winner for a block, a *different* new bid from the same sender must be rejected with `ErrBidSenderExists`. The "different" check is implemented with `Bid.Equals`, which only compares `BlockNumber` and `TargetTxHash` and ignores every other field of the bid struct (`Bid` amount, `CallGasLimit`, `Data`, `Nonce`, `MaxGasPrice`, signatures). This mirrors the reported bug class: a struct-identity/hash check that omits fields of the full struct, causing semantically different objects to be treated as identical.

### Finding Description
`BidData`/`Bid` carries many economically meaningful fields: [1](#0-0) 

`Bid.Equals` is defined to compare only two of these fields: [2](#0-1) 

This is used by `senderHasDifferentWinner`, which is the sole guard preventing a sender from displacing/duplicating an already-recorded winning bid for the same block with a distinct bid: [3](#0-2) 

and is invoked in `validateBid` as rule #1, explicitly commented as "the new bid isn't equal to the previous bid": [4](#0-3) 

Because `Equals` ignores `Bid` (the bid amount), `CallGasLimit`, `Data`, `Nonce`, and `MaxGasPrice`, any sender who already has a recorded winner for `(blockNumber, targetTxHash)` can submit an entirely different bid — different bid amount, different calldata/gas limit, or a stale/replayed `Nonce` — signed with a fresh, independently valid `SearcherSig`/`AuctioneerSig` (EIP-712 digest per `GetHashTypedData` in `kaiax/auction/eip712.go`), and it will pass rule #1 because `Equals` reports them as the same bid, even though `bid.Hash()` (which rlp-hashes the full `BidData`) will differ and the bid is otherwise accepted as new/valid by all remaining checks (rules 2-6 in `validateBid`).

### Impact Explanation
This breaks the intended invariant "one distinct winning bid per sender per block" for the same target transaction. A searcher can effectively supersede/duplicate their own previously admitted winning bid with a different bid amount, gas limit, or calldata without being blocked by the sender-uniqueness check, undermining fair auction admission and potentially enabling low-bid submissions to slip in under the guise of "the same" bid, or bid-amount manipulation that the sender-winner accounting logic assumes cannot happen. Depending on how `bidWinnerMap`/`bidTargetMap` are subsequently consumed for block-proposal winner selection, this can lead to acceptance of a bid the auction winner-selection invariants did not intend to admit — a form of auction admission-control bypass. I could not fully trace the downstream winner-selection code (`SelectWinner`/consumption of `bidWinnerMap`) within the available iterations, so the exact downstream consequence (e.g., whether a lower/manipulated bid can ultimately win block inclusion) is not fully confirmed and should be verified against `kaiax/auction/impl/handler*.go` and block-assembly integration code.

### Likelihood Explanation
Reachable by any unprivileged searcher/bidder submitting bids via the public bid-submission RPC (`kaiax/auction/impl/api.go` `SubmitBid`/`ToBid`). No special privileges, peer position, or validator role are required — only two normally-signed bids for the same target transaction and block from the same sender, differing in economically relevant fields.

### Recommendation
Change `Bid.Equals` to compare the full `BidData` (or use `bid.Hash()` equality, which already rlp-hashes all `BidData` fields) instead of just `BlockNumber` and `TargetTxHash`:
```go
func (b *Bid) Equals(other *Bid) bool {
    return b.Hash() == other.Hash()
}
```
This ensures the sender-uniqueness check in `senderHasDifferentWinner` correctly rejects any bid that differs from the recorded winner in any field, closing the gap between the intended "identical bid" semantics and the actual comparison performed.

### Proof of Concept
1. Searcher `S` submits `bidA` targeting `targetTxHash T` for block `N` with `Bid.Bid = 10`, valid `SearcherSig`/`AuctioneerSig` computed over the full `BidData` via `GetHashTypedData`. `bidA` is admitted and recorded as the winner for `(N, S)` in `bidWinnerMap`.
2. Searcher `S` crafts `bidB` with the same `BlockNumber = N` and `TargetTxHash = T`, but a different `Bid.Bid` (e.g., `1`), different `CallGasLimit`/`Data`, and a fresh valid EIP-712 signature over this new `BidData`.
3. `validateBid(bidB)` calls `senderHasDifferentWinner(bidB)`, which calls `bidA.Equals(bidB)` (via `bp.bidMap[hash]` lookup) — since `Equals` only checks `BlockNumber`/`TargetTxHash`, it returns `true`, so `senderHasDifferentWinner` returns `false`, and rule #1 in `validateBid` does not reject `bidB` even though it is a materially different bid.
4. `bidB` proceeds through the remaining validation and is inserted into the pool, despite the code's explicit intent (per the comment on rule #1) to block exactly this scenario.

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

**File:** kaiax/auction/impl/bid_pool.go (L132-134)
```go
	// Only close channels if they haven't been closed before
	if atomic.CompareAndSwapUint32(&bp.stopped, 0, 1) {
		close(bp.bidMsgCh)
```

**File:** kaiax/auction/impl/bid_pool.go (L335-343)
```go
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

**File:** kaiax/auction/impl/bid_pool.go (L345-361)
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
```
