### Title
Unrestricted `fillOrder()` allows any solver to bypass the off-chain auction and settle at the guaranteed floor price, extracting the value the competitive bidding process was meant to capture for the user — (File: `evm/src/apps/intentsv2/IntentsBase.sol` / `evm/src/apps/IntentGatewayV2.sol`)

### Summary
The Intent Gateway's auction is entirely off-chain: solvers post signed bids to the `intents-coprocessor` pallet, the user picks the "best" bid, and only then is a transaction submitted on-chain. Unless the gateway is explicitly configured with `solverSelection = true`, `fillOrder()` performs **no on-chain check that the caller is the solver who won the auction** — any address can call it as long as it supplies outputs `>= order.output.assets`, i.e. the bare minimum the user agreed to accept at order-placement time. This is structurally the same broken invariant as the AuctionCrowdfund `bid()` finding: a public, permissionless function that commits another party's assets (the escrowed order inputs) can be triggered by anyone, and an attacker can settle it at the worst price the protocol still considers "valid," pocketing the surplus that the auction/off-chain bidding was designed to deliver to the user.

### Finding Description
`fillOrder()` only enforces the solver-selection check conditionally: [1](#0-0) 

```solidity
if (_params.solverSelection) {
    bytes32 storedSelectionHash;
    assembly { storedSelectionHash := tload(commitment) }
    bytes32 expectedSelectionHash = keccak256(abi.encode(msg.sender, order.session));
    if (storedSelectionHash != expectedSelectionHash) revert Unauthorized();
}
```

When `solverSelection` is `false` (the mode the docs explicitly describe as the *unprotected* one — "orders are protected from unauthorized fills" only *when* `solverSelection` is enabled), this branch is skipped entirely, and `fillOrder()` proceeds to accept any `msg.sender` whose `options.outputs[i]` amounts are `>=` the order's stated `output.assets` amounts, per the "Fill Flow" behavior documented for the protocol: [2](#0-1) 

The auction/bid-ranking mechanism (`sortBids`, `selectAndExecuteBest`, `BidManager`) exists purely at the SDK/coprocessor layer: [3](#0-2) 

Nothing in `fillOrder()`, `_fillSameChain()`, or `_fillCrossChain()` verifies that the caller's outputs match — or exceed — the best bid observed by the coprocessor; it only checks against the order's baseline `output.assets`, which is the *minimum* acceptable amount fixed at order placement: [4](#0-3) 

So once bids are broadcast publicly (bid data, including the exact winning solver's output amounts, is stored on-chain/off-chain and readable by anyone per the coprocessor's `Bids` storage and indexer schema): [5](#0-4) 

any third party — including a bystander who never competed honestly in the auction — can watch the mempool/coprocessor, then call `fillOrder()` directly with only the order's contractually-minimum output, claim the escrow, and set `_filled[commitment] = msg.sender`: [6](#0-5) 

This locks out the genuinely-selected higher bidder (whose tx now reverts on `Filled()`), and the user receives only the floor amount instead of the value the auction was designed to secure.

### Impact Explanation
This is a direct value-extraction/logic-attack path against user funds: the escrowed input tokens are released to an unauthorized party for less value delivered to the beneficiary than the protocol's own auction process determined was available. It matches the required impact categories of "unauthorized transaction/execution" and "logic attacks / transaction manipulation" — the corrupted value is the beneficiary's actual received output amount, which is coerced down to `order.output.assets` (the floor) instead of the competitively-discovered better price, with the escrowed input released to an arbitrary caller rather than the legitimately selected/rightful solver.

### Likelihood Explanation
High for any deployment that does not explicitly enable `solverSelection` (it is an optional, off-by-default flag in `Params`), since all information needed (order data, output requirements, winning bid amounts) is public by design of the auction mechanism, and `fillOrder()` is a `public` entrypoint reachable by any unprivileged EOA/contract with no signature or selection requirement gating it.

### Recommendation
Make solver-selection binding enforcement mandatory by default rather than opt-in, or otherwise require that `fillOrder()` always validate the caller against a recorded/attested best-bid commitment (not merely the order's static minimum), so that only the auction-selected solver — or a solver providing outputs at least as good as the disclosed best bid — can settle the order.

### Proof of Concept
1. User places a cross-chain/same-chain order with `output.assets = 100 DAI` via `placeOrder`, with `solverSelection` left disabled (default `Params`).
2. Solvers compete off-chain; Solver B lands the best bid of 105 DAI, visible in the pallet's `Bids` storage/indexer.
3. Attacker (not Solver B) observes the order and the disclosed floor requirement (`order.output.assets`), and — before Solver B's transaction lands — calls `fillOrder(order, FillOptions{outputs: [100 DAI]})` directly.
4. `fillOrder()` skips the `_params.solverSelection` branch (disabled), passes the `options.outputs[i] >= order.output.assets[i]` checks in `_fillSameChain`/`_fillCrossChain`, sets `_filled[commitment] = attacker`, and releases the escrowed input tokens to the attacker.
5. Solver B's later transaction reverts with `Filled()`; the user receives only 100 DAI instead of the 105 DAI the auction had determined was available, and the attacker settled the order themselves without ever having competed in or won the auction.

### Citations

**File:** evm/src/apps/IntentGatewayV2.sol (L413-436)
```text
    function fillOrder(Order calldata order, FillOptions calldata options) public payable nonReentrant {
        if (order.deadline < _blockNumber()) revert Expired();
        bytes32 commitment = keccak256(abi.encode(order));

        address hostAddr = host();
        bytes32 currentChain = keccak256(IDispatcher(hostAddr).host());
        bytes32 orderSource = keccak256(order.source);
        bytes32 orderDest = keccak256(order.destination);
        bool isSameChain = orderSource == orderDest;

        if (isSameChain && orderSource != currentChain) revert WrongChain();
        if (!isSameChain && orderDest != currentChain) revert WrongChain();

        if (_filled[commitment] != address(0)) revert Filled();

        if (_params.solverSelection) {
            bytes32 storedSelectionHash;
            assembly {
                storedSelectionHash := tload(commitment)
            }

            bytes32 expectedSelectionHash = keccak256(abi.encode(msg.sender, order.session));
            if (storedSelectionHash != expectedSelectionHash) revert Unauthorized();
        }
```

**File:** docs/content/developers/evm/intent-gateway/overview.mdx (L41-45)
```text
### Fill Flow

The solver calls `fillOrder(order, options)` on the **destination chain**. The function verifies the order hasn't expired (`order.deadline >= block.number`), confirms execution is on the correct chain, and checks the order hasn't already been filled. The solver must provide output amounts greater than or equal to the order's required amounts — any amount below the required amount reverts with `InvalidInput()`.

If the solver provides more tokens than required, the excess (surplus) is split according to `surplusShareBps`. If the order includes calldata, 100% of surplus goes to the protocol to prevent manipulation.
```

**File:** docs/content/developers/sdk/api/intent-gateway.mdx (L343-359)
```text
### sortBids(order, bids)

Sorts [`Bid`](#bid) objects by output value, using the same strategy the autopilot uses. Returns a new sorted array.

```typescript lineNumbers
async sortBids(order: Order, bids: Bid[]): Promise<Bid[]>
```

---

### selectAndExecuteBest(order, bids)

Autopilot bid selection over already-built [`Bid`](#bid) objects: sorts the bids, simulates each until one passes, then executes it.

```typescript lineNumbers
async selectAndExecuteBest(order: Order, bids: Bid[]): Promise<SelectBidResult>
```
```

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L54-80)
```text
    function _fillSameChain(Order calldata order, FillOptions calldata options, bytes32 commitment) internal {
        uint256 outputsLen = order.output.assets.length;

        _filled[commitment] = msg.sender;

        uint256 msgValue = msg.value;
        address beneficiary = address(uint160(uint256(order.output.beneficiary)));
        bool isFullyFilled = true;

        TokenInfo[] memory escrowedInputs = new TokenInfo[](outputsLen);
        TokenInfo[] memory outputFills = new TokenInfo[](outputsLen);

        for (uint256 i; i < outputsLen; i++) {
            bytes32 outputToken = order.output.assets[i].token;
            if (options.outputs[i].token != outputToken) revert InvalidInput();

            address token = address(uint160(uint256(outputToken)));
            uint256 totalRequired = order.output.assets[i].amount;
            uint256 solverAmount = options.outputs[i].amount;

            uint256 alreadyFilled = _partialFills[commitment][outputToken];
            uint256 remaining = totalRequired - alreadyFilled;
            if (remaining == 0 || solverAmount == 0) {
                if (solverAmount == 0 && remaining > 0) isFullyFilled = false;
                continue;
            }
            uint256 fillAmount;
```

**File:** modules/pallets/intents-coprocessor/src/lib.rs (L123-136)
```rust
	/// Storage for bids indexed by commitment and filler address
	/// Allows easy discovery of all bids for a given order commitment
	/// The actual bid data is stored in offchain storage
	/// We store the deposit amount here for accurate refunds
	#[pallet::storage]
	pub type Bids<T: Config> = StorageDoubleMap<
		_,
		Blake2_128Concat,
		H256, // commitment
		Blake2_128Concat,
		T::AccountId, // filler
		BalanceOf<T>, // deposit amount, actual bid data in offchain storage
		OptionQuery,
	>;
```
