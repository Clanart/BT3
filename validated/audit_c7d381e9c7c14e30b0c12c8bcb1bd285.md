### Title
Auction bid signature/encoding version-mismatch may allow silent stripping of `maxGasPrice` slippage protection - ([File: blockchain/system/auction.go], [File: kaiax/auction/impl/getter.go])

### Summary
The Kaia auction module (KIP‑249) reads the on‑chain `AUCTION_VERSION` of the active `AuctionEntryPoint` contract once per block and uses that value both to select the EIP‑712 typehash for verifying a searcher's bid signature and to ABI‑encode the winning bid into a `call` transaction. Because the version used to interpret/encode a bid is read fresh at execution time rather than pinned to the version the searcher actually signed against, a version transition (v2.1 `"0.0.1"` → v3.0 `"0.0.2"`, or the reverse) between bid submission and block building can change how the `Bid`/`MaxGasPrice` fields are packed — the same class of "amount reinterpreted under a different mode" bug described in the external report for HoneyFactory.

### Finding Description
`ReadAuctionVersion` reads `AUCTION_VERSION()` from the live `AuctionEntryPoint` contract at each block (`blockchain/system/auction.go:71-77`), and `updateAuctionInfo` refreshes `auctionEntryPointVersion` on every `PostInsertBlock` (`kaiax/auction/impl/execution.go:52-108`).

`EncodeAuctionCallData(bid, version)` (`blockchain/system/auction.go:83-119`) branches purely on this live `version` string:
- if `version == "0.0.2"` it packs the v3 ABI including `MaxGasPrice` (defaulting to `0` when `bid.MaxGasPrice == nil`);
- otherwise it silently falls back to the v2.1 ABI, which has no `MaxGasPrice` field at all.

`GetBidTxGenerator` (`kaiax/auction/impl/getter.go:27-47`) calls `system.EncodeAuctionCallData(bid, a.bidPool.GetAuctionEntryPointVersion())` at the moment the CN builds the bid transaction for inclusion — i.e., using the version read from the *current* chain state, not the version that was in effect (or that the searcher/auctioneer assumed) when the bid's EIP‑712 signature was produced via `Bid.GetHashTypedData(chainId, verifyingContract, version)` (`kaiax/auction/eip712.go:120-149`), which also selects the v2 vs v3 struct hash based on a caller-supplied `version` string.

This mirrors the HoneyFactory bug class exactly: a single numeric/field semantic (`MaxGasPrice`, a slippage/cap protection analogous to `maxHoneyAmount`) is interpreted differently depending on a *mode* (contract version) that can change between when the off-chain actor commits to a value and when the on-chain transaction is actually assembled and executed. If the `AuctionEntryPoint` is upgraded from v2.1 to v3.0 between the time a searcher/auctioneer signs a bid (assuming v2.1, no `MaxGasPrice` field/protection) and the block in which the CN encodes and submits the bid transaction, `EncodeAuctionCallData` will now take the v3.0 branch and pack `MaxGasPrice = 0` (since `bid.MaxGasPrice` was never set by the v2.1-era bid). Depending on how the v3.0 `IAuctionEntryPoint.call()` implementation treats a zero `maxGasPrice` (e.g., "no cap" vs "reject"), this can either strip the searcher's intended gas-price cap protection or cause unintended reverts/mis-execution of the bid — all outside the searcher's control and without any on-chain slippage guard, exactly as HoneyFactory's `mint()` amount reinterpretation without a `maxHoneyAmount` parameter.

### Impact Explanation
If the version transition silently drops the `MaxGasPrice` cap (interpreted as "unlimited"), the searcher's bid could be executed at a gas price far above what they intended to authorize, causing unexpected value loss for the auction bidder. This is a fee/economic abuse vector reachable purely by submitting a bid through the public `auction_submitBid` API and having block assembly happen during a version transition — no privileged access required, matching the "fee delegation/auction settlement" analog class explicitly permitted by the rules.

### Likelihood Explanation
Low-to-Medium likelihood: it requires an `AuctionEntryPoint` contract version upgrade (v2.1↔v3.0) to occur in the narrow window between a bid's signature creation and its inclusion in a block, plus a searcher/auctioneer service that has not synchronized to the new version before creating bids. This is a real but infrequent operational scenario (contract upgrades are rare events), and I could not fully verify from the available code whether the on-chain v3.0 `AuctionEntryPoint.call()` implementation treats `maxGasPrice == 0` as "unlimited" or as a hard reject, since the actual Solidity contract source (only the ABI/bindings are present in this repo) was not available in the index.

### Recommendation
- Bind the EIP‑712 version and ABI-encoding version used for a bid to the version that was in effect when the bid was first accepted into the bid pool (pin it to the bid, not re-derive it live at block-building time), and reject/re-validate bids whose pinned version no longer matches the live `AUCTION_VERSION` at inclusion time.
- Explicitly reject encoding a bid under the v3.0 ABI when `bid.MaxGasPrice` was not set by a v3-aware signer, rather than silently defaulting to `0`.
- Add a check in `updateAuctionInfo`/`bid_pool` that clears/re-validates bids in the pool whenever `auctionEntryPointVersion` changes between blocks (similar to how it already clears bids when the auctioneer address changes).

### Proof of Concept
Conceptual sequence (verified against the code paths above, but not executed against a live v3.0 contract since its Solidity source was not available in the index):
1. `AuctionEntryPoint` is currently v2.1 (`AUCTION_VERSION() == "0.0.1"`). A searcher/auctioneer service builds and signs a `Bid` with `GetHashTypedData(chainId, addr, "0.0.1")`, leaving `MaxGasPrice` unset (nil), since v2.1 has no such field.
2. Before the bid's target transaction is mined, governance/registry upgrades `AuctionEntryPoint` to v3.0 (`AUCTION_VERSION() == "0.0.2"`).
3. On the next block, `updateAuctionInfo` (`kaiax/auction/impl/execution.go:103`) reads the new version `"0.0.2"` and stores it via `bp.updateAuctionInfo(...)`.
4. When the CN builds the bid transaction, `GetBidTxGenerator` → `system.EncodeAuctionCallData(bid, "0.0.2")` takes the v3.0 branch (`blockchain/system/auction.go:84-103`), packing `MaxGasPrice = new(big.Int)` (zero) into the call data sent to the live v3.0 contract, even though the searcher never agreed to any particular gas-price cap semantics under v3.0.
5. The resulting on-chain `call()` executes with `maxGasPrice = 0`, whose effect (unlimited vs. reverted) is entirely determined by the v3.0 contract's own interpretation — a value the searcher did not consent to and could not have foreseen at signing time.