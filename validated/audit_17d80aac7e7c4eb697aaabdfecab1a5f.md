### Title
Incomplete Bid Identity Comparison Allows Auction Winner Bid Substitution - ([File: kaiax/auction/bid.go])

### Summary
`Bid.Equals()` only compares `BlockNumber` and `TargetTxHash`, omitting the bid amount, calldata, recipient, and gas-limit fields. This mirrors the reported "missing destination chain ID in seeds" bug class: a struct/field set used for identity/uniqueness checks is missing security-relevant components, causing two materially different objects to be treated as identical. Here, this incomplete comparison is used directly in the auction admission-control logic that is supposed to reject a *different* bid from a sender who has already won a block's auction slot.

### Finding Description
`Bid.Equals` is defined as: [1](#0-0) 

It is used in `BidPool.senderHasDifferentWinner`, which is the sole guard preventing a sender who already has a winning bid recorded for a given block from submitting another, different bid for the same `(BlockNumber, TargetTxHash)` slot: [2](#0-1) 

This function is invoked in `validateBid` as check #1, and returning `false` (i.e., "not different") allows the new bid to bypass `ErrBidSenderExists` and be admitted to the pool: [3](#0-2) 

Because `Equals` ignores `Sender`-independent economic and execution fields (`Bid` amount, `To`, `Data`, `CallGasLimit`, `Nonce`, `MaxGasPrice`), any subsequent, fully-signed, valid bid from the same sender that shares the same `BlockNumber`/`TargetTxHash` is treated as "the same bid" regardless of its actual bid amount or execution payload. The admission-control check that is supposed to stop the winner from submitting a competing/replacing bid for that slot is effectively neutered — it can never trigger for bids sharing block+target, which is exactly the scenario it's meant to guard.

Note that `bid.Hash()` (full RLP hash of `BidData`) is different, so the "already exists" duplicate check does not block a modified bid either: [4](#0-3) 

### Impact Explanation
This affects the `auction`/`gasless`-style bidding path reachable by any bidder (searcher) submitting bids to a public-RPC-connected node — no privileged access required. A searcher who has already won a block's auction slot with a high bid can subsequently submit a second, independently-signed bid for the identical `(BlockNumber, TargetTxHash)` slot with a materially lower `Bid` amount (while keeping `Bid.Sign() > 0` to pass validation) or a different `To`/`Data`/`CallGasLimit` payload. Because `senderHasDifferentWinner` incorrectly reports "not different," this substitute bid is accepted into the pool instead of being rejected, undermining the auction admission invariant that a slot winner cannot displace their own committed bid with a cheaper one. This falls under the accepted impact category of auction/gasless settlement abuse — a proposer/network can be defrauded of the priority fee revenue the original higher bid committed to, or a lower-value/rewritten transaction could be substituted for the one the entry point contract and other participants expected to be executed for that slot.

### Likelihood Explanation
Likelihood is high for any actor who can submit auction bids (an "auction bidder," explicitly in scope). No node collusion, network-level manipulation, or privileged access is required — a bidder simply submits two independently valid, correctly-signed `Bid` messages sharing the same `BlockNumber` and `TargetTxHash` but differing in `Bid`/`Data`/`To`/`CallGasLimit`. The flaw is a pure logic bug in `Equals`/`senderHasDifferentWinner`, reachable through the normal `AddBid`/`HandleBid` bid-submission path.

### Recommendation
Update `Bid.Equals` (and any other identity comparisons relying on it, e.g., `senderHasDifferentWinner`) to compare the full set of security-relevant `BidData` fields (at minimum `Bid`, `To`, `Data`, `CallGasLimit`, `Nonce`, `MaxGasPrice`, `Sender`, in addition to `BlockNumber`/`TargetTxHash`), or simply compare `bid.Hash() == other.Hash()` for exact identity. This ensures that a sender who has already won a slot cannot have a materially different bid silently accepted as if unchanged.

### Proof of Concept
1. Searcher `S` submits `Bid1{BlockNumber: N, TargetTxHash: H, Bid: 100 KAIA, To: X, Data: D1, CallGasLimit: G1}`, correctly signed; it is validated, added to the pool, and recorded as the winner for `(N, S)` in `bidWinnerMap`.
2. Searcher `S` then crafts and signs `Bid2{BlockNumber: N, TargetTxHash: H, Bid: 1 KAIA, To: X, Data: D2, CallGasLimit: G2}` — same block/target, but a much lower bid and different payload.
3. In `validateBid`, `bp.bidMap[bid2.Hash()]` lookup misses (different hash → not `ErrBidAlreadyExists`).
4. `senderHasDifferentWinner(bid2)` looks up `bidWinnerMap[N][S] -> hash(Bid1)`, then calls `bid2.Equals(bid1)`, which returns `true` because only `BlockNumber` and `TargetTxHash` match — despite `Bid`, `Data`, and `CallGasLimit` being entirely different.
5. `Bid2` is therefore admitted into the pool instead of being rejected with `ErrBidSenderExists`, allowing the searcher to substitute a much cheaper/different committed bid for the slot they already won.

Some downstream details (exactly how `bidWinnerMap` is updated during block assembly and whether a later, lower bid can ultimately be selected as the executed winner) could not be fully traced within the available tool budget; this should be verified against `kaiax/auction/impl/builder.go`/`handler.go` block-building logic to confirm the exact on-chain settlement consequence, but the identity-check flaw itself is confirmed directly in the cited code.

### Citations

**File:** kaiax/auction/bid.go (L70-77)
```go
func (b *Bid) Hash() common.Hash {
	if hash := b.hash.Load(); hash != nil {
		return hash.(common.Hash)
	}
	hash := rlpHash(b.BidData)
	b.hash.Store(hash)
	return hash
}
```

**File:** kaiax/auction/bid.go (L132-134)
```go
func (b *Bid) Equals(other *Bid) bool {
	return b.BlockNumber == other.BlockNumber && b.TargetTxHash == other.TargetTxHash
}
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
