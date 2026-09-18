### Title
`CW721ERC721Pointer.balanceOf` performs an unbounded on-chain-growing loop that will eventually revert due to gas exhaustion - ([File: contracts/src/CW721ERC721Pointer.sol])

### Summary
`CW721ERC721Pointer.balanceOf(address)` computes an ERC721 `balanceOf` result by repeatedly calling the CosmWasm `WasmdPrecompile.query` in pages of 1000 tokens, looping until an empty-page terminator is returned. The number of iterations is proportional to the number of NFTs the address owns in the underlying CW721 collection — a value that grows unboundedly over time as more tokens are minted/transferred to that owner. There is no cap on the number of loop iterations or on the total work done, so once an owner's token count grows large enough, the accumulated gas cost of the query loop exceeds what is available in a single call, and `balanceOf` reverts permanently for that address. This mirrors the Bond Protocol `BondAggregator.liveMarketsBy` bug: a view/read function whose cost scales with an ever-growing on-chain set is invoked with no pagination exposed to the caller, so it inevitably becomes unusable.

### Finding Description
`balanceOf` builds a query for `{"tokens":{"limit":1000,"owner":"<seiaddr>"}}`, sends it via `WasmdPrecompile.query`, and loops: [1](#0-0) 

Each iteration performs one CosmWasm smart query (crossing the Wasmd precompile boundary from the EVM), parses up to 1000 token IDs, and issues a new query using `start_after` to continue. The loop only terminates when the CW721 contract's response matches the fixed `{"tokens":[]}` terminator. There is no maximum iteration count, no gas-based early exit, and no ability for the caller to request a bounded/paginated result the way the underlying CosmWasm `tokens` query itself supports (`start_after`/`limit`) — the pointer contract fully consumes the pagination internally rather than exposing it to the EVM caller.

Because the CW721ERC721Pointer is the standard ERC721 façade sei-chain creates for every CW721 collection (per `x/evm/AGENTS.md`, pointer contracts are the primary CW↔EVM interoperability mechanism: "CW721 NFTs get an ERC721 pointer"), `balanceOf` is a core, frequently-invoked entry point that any EVM contract or off-chain client can call for an arbitrary owner address, including via `eth_call`, `staticcall` from another contract, or as part of a transaction.

This is directly analogous to the reported Bond Protocol bug: an unbounded loop over state that grows monotonically (owned token count) with no pagination surfaced to the caller, invoked as a value/view computation, with cost scaling per-iteration cross-VM query. As the CW721 collection owner accumulates more tokens (e.g., a marketplace contract, a whale collector, or a contract that itself is the "owner" of thousands of wrapped/staked NFTs), the number of required 1000-token pages grows, and eventually the gas required exceeds the block gas limit / call gas stipend, causing `balanceOf` to permanently revert for that address.

### Impact Explanation
Unlike `x/tokenfactory/keeper/creators.go`'s `GetAllDenomsFromCreator`, which is explicitly documented and reviewed as bounded-by-gas-metering and thus acceptable for the CosmWasm-only query path ( [2](#0-1) ), `CW721ERC721Pointer.balanceOf` is exposed as the canonical ERC721 balance-of function relied upon for composability: other EVM smart contracts (DeFi protocols, marketplaces, access-control gates gated on NFT ownership) call it expecting standard ERC721 semantics with O(1) or bounded cost. Once an address's token count in the wrapped CW721 collection grows large enough that the internal pagination loop exceeds available gas, `balanceOf` becomes permanently and unconditionally unusable for that address — no caller-supplied gas amount can fix it, since the loop is driven by data size, not gas headroom control. This breaks:
- Any downstream contract logic gating behavior on `balanceOf` (permanent denial of service / broken invariant for large holders).
- Off-chain indexers/wallets calling `balanceOf` via `eth_call`, which will get reverts instead of a balance.

This matches the "crash/broken read path reachable by any EVM caller for a pointer contract" bug class explicitly in scope (CW↔EVM pointers and the wasm bridge).

### Likelihood Explanation
Likelihood grows deterministically over time and does not require any adversarial action: normal usage of the CW721 collection (many mints/transfers accumulating to one address — e.g., a marketplace escrow contract, a staking contract, or simply a large collector) will trigger the condition. An attacker could also deliberately accelerate this by minting/transferring a very large number of tokens to a single target address (their own or a victim contract) to intentionally "brick" `balanceOf` for that address, since each on-chain mint/transfer is cheap relative to the disproportionate future query cost it imposes.

### Recommendation
Change `CW721ERC721Pointer.balanceOf` to avoid unbounded iteration:
- Prefer relying on a bounded per-owner counter maintained by the underlying CW721 contract if available, or
- Cap the number of pages iterated (returning an error/using a fixed max rather than looping until exhaustion), or
- Expose pagination to the caller (e.g., a `balanceOfPaged(owner, startAfter, limit)`-style helper) and document that `balanceOf` is only guaranteed correct/callable up to a bounded token count, or
- Track and update balances incrementally on transfer/mint events instead of recomputing via full enumeration on every call.

### Proof of Concept
1. Deploy/point a `CW721ERC721Pointer` at a CW721 collection.
2. Mint (or transfer) more than N thousand tokens to a single address `A`, where N is large enough that iterating `ceil(N/1000)` CosmWasm cross-VM `tokens` queries (each parsing and returning up to 1000 token IDs) exceeds the gas available to a single EVM call (e.g., the block gas limit or a contract-imposed call gas stipend).
3. Call `CW721ERC721Pointer.balanceOf(A)` via `eth_call` or from another contract.
4. Observe the call reverts due to out-of-gas, and will continue to revert for any caller regardless of gas supplied above the per-call ceiling (e.g., the EVM block gas limit), permanently breaking `balanceOf` for address `A`. [1](#0-0)

### Citations

**File:** contracts/src/CW721ERC721Pointer.sol (L51-79)
```text
    function balanceOf(address owner_) public view override returns (uint256) {
        if (owner_ == address(0)) {
            revert ERC721InvalidOwner(address(0));
        }
        uint256 numTokens = 0;
        string memory startAfter;
        string memory qb = string.concat(
            string.concat("\"limit\":1000,\"owner\":\"", AddrPrecompile.getSeiAddr(owner_)),
            "\""
        );
        bytes32 terminator = keccak256("{\"tokens\":[]}");

        bytes[] memory tokens;
        uint256 tokensLength;
        string memory req = string.concat(string.concat("{\"tokens\":{", qb), "}}");
        bytes memory response = WasmdPrecompile.query(Cw721Address, bytes(req));
        while (keccak256(response) != terminator) {
            tokens = JsonPrecompile.extractAsBytesList(response, "tokens");
            tokensLength = tokens.length;
            numTokens += tokensLength;
            startAfter = string.concat(",\"start_after\":", string(tokens[tokensLength-1]));
            req = string.concat(
                string.concat("{\"tokens\":{", string.concat(qb, startAfter)),
                "}}"
            );
            response = WasmdPrecompile.query(Cw721Address, bytes(req));
        }
        return numTokens;
    }
```

**File:** x/tokenfactory/keeper/creators.go (L26-37)
```go
// GetAllDenomsFromCreator returns every denom for a creator with no page cap.
// Safe to use in the wasm query path: gas metering bounds execution cost, so unbounded iteration does not pose a DoS risk.
func (k Keeper) GetAllDenomsFromCreator(ctx sdk.Context, creator string) []string {
	store := k.GetCreatorPrefixStore(ctx, creator)
	iterator := store.Iterator(nil, nil)
	defer func() { _ = iterator.Close() }()
	var denoms []string
	for ; iterator.Valid(); iterator.Next() {
		denoms = append(denoms, string(iterator.Key()))
	}
	return denoms
}
```
