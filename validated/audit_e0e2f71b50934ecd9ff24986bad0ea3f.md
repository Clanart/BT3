Interesting find: the CW721/CW1155 ERC-pointer contracts' `balanceOf(owner)` loops over the wasm contract's paginated token list until a terminator hash, calling `WasmdPrecompile.query` in a `while` loop for every 1000 tokens.## Title
DoS: A CW721 owner can mint enough tokens to a single address to make `CW721ERC721Pointer.balanceOf()` and any function that relies on it revert or run out of gas ([File: contracts/src/CW721ERC721Pointer.sol])

### Summary
`CW721ERC721Pointer.balanceOf()` computes an NFT owner's balance by repeatedly querying the underlying CW721 contract's `tokens` endpoint in a `while` loop, paging 1000 token IDs at a time via the `Wasmd` query precompile, until an empty-list terminator is returned. This mirrors the FactoryDAO bug class: an unprivileged party (here, the CW721 minter/owner, or in permissionless-mint collections, any minter) can cause an address to accumulate an unbounded number of token entries, and every future `balanceOf()` call — including ones invoked internally by `ERC2981`/other logic, and by any EVM contract or off-chain caller depending on it — must iterate the *entire* token list of that owner before returning.

### Finding Description
`balanceOf(address owner_)` in `CW721ERC721Pointer.sol` is implemented as an unbounded pagination loop over the CosmWasm `tokens` query: [1](#0-0) 

Each iteration performs a synchronous cross-VM CosmWasm query through `WasmdPrecompile.query`, decodes JSON via the `JsonPrecompile`, and pages by `start_after`. The number of iterations is `ceil(ownerTokenCount / 1000)`. There is no upper bound on how many tokens an owner (or a mint-enabled minter targeting a single victim address, or the owner minting to themselves) can hold on the underlying CW721 contract, and no cap on the number of loop iterations `balanceOf` will perform. Because `WasmdPrecompile.query` and `JsonPrecompile.extractAsBytesList` are gas-metered EVM precompile calls (not free), the total gas cost of `balanceOf()` grows linearly (and, given repeated JSON+query overhead per token page, effectively unbounded) with the number of tokens minted to that address.

This is the same bug class as the referenced report: an attacker-controlled, permissionless action (minting many NFTs to an address, analogous to creating many minimal-value "receipts") inflates a per-account entry count that a *later, unrelated* caller must fully iterate through in a single call/transaction. Any EVM contract or transaction that calls `balanceOf()` on the pointer (directly, or transitively — e.g. `ERC2981`/marketplace or DeFi integrations that check `balanceOf`) inherits this unbounded cost.

### Impact Explanation
- Any EVM transaction (or `eth_call` from a public RPC client) that invokes `balanceOf()` on a `CW721ERC721Pointer` for a heavily-minted owner can run out of gas or hit the EVM block gas limit, permanently reverting/DoS'ing that call path.
- Because the pointer is the canonical ERC721 bridge for a CW721 collection, contracts (marketplaces, lending protocols, wallets) that rely on `balanceOf()` for a given address can be made permanently unusable for that address by anyone able to mint (or transfer) enough tokens to it — this is a real griefing/DoS vector reachable purely through public CosmWasm mint/transfer transactions plus a public EVM call, matching the "public EVM JSON-RPC surface" and "CW<->EVM pointers" in-scope surfaces.
- This does not cause direct fund loss but can permanently freeze/DoS a specific account's interaction with the pointer contract and anything built on top of it (Medium severity DoS, consistent with the referenced finding's classification).

### Likelihood Explanation
Likelihood is moderate-to-high in permissionless-mint CW721 collections, and always possible for the collection creator/owner against their own users or against themselves as a griefing vector, since minting many tokens to one address requires no special privilege beyond normal `mint` calls (cost scales only with the attacker's own gas/fees on the CW execution side, which is far cheaper per-token than the resulting `balanceOf` iteration cost on the EVM side). No consensus-level protection currently bounds `balanceOf`'s iteration count.

### Recommendation
- Cap the number of pages `balanceOf()` will traverse (revert or return a best-effort/lower-bound count past a fixed iteration limit), or
- Track balances incrementally in the pointer contract state (e.g., update a mapping on mint/transfer/burn events observed via the bridge) rather than recomputing by paginated query on every call, or
- Charge gas proportional to iteration count up front / require the caller to supply enough gas per page and fail fast rather than silently consuming unbounded precompile calls.

### Proof of Concept
1. Deploy/identify a CW721 contract with a `CW721ERC721Pointer` registered via `registerPointerForERC721`.
2. As the CW721 minter (an unprivileged, permissionless-mint deployer, or the collection owner as a self-DoS/griefing setup), mint N tokens (e.g., N = 50,000) to a single target address `victim`.
3. Call `pointerContract.balanceOf(victim)` from any EVM transaction or `eth_call`.
4. Observe that the call performs `ceil(N/1000)` sequential `WasmdPrecompile.query` + `JsonPrecompile.extractAsBytesList` round trips [2](#0-1) , consuming gas proportional to N and eventually exceeding the EVM gas limit for large N, reverting the call and denying any contract that depends on `balanceOf(victim)`.

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
