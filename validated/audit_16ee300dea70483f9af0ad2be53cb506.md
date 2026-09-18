### Title
Unbounded pagination loop in `CW721ERC721Pointer.balanceOf()` allows permanent DoS via NFT-count inflation - (File: `contracts/src/CW721ERC721Pointer.sol`)

### Summary
`CW721ERC721Pointer.balanceOf()` paginates through the underlying CosmWasm CW721 contract's token list in a `while` loop that only terminates once an empty page is returned, mirroring the `claimForAllWindows()` pattern where a for-loop bound grows without limit and is externally, cheaply increasable by anyone.

### Finding Description
`balanceOf()` builds a CW721 `tokens` query filtered by `owner`, paginating 1000 results at a time, and loops until the CW contract returns an empty `tokens` array: [1](#0-0) 

The number of loop iterations is `ceil(number_of_tokens_owned_by(owner) / 1000)`. This count is fully controlled by whoever can mint/transfer tokens on the underlying CW721 contract to a target address — for any collection with permissionless or low-friction minting, an attacker can mint an arbitrarily large number of tokens to a victim address. Each iteration performs a `WasmdPrecompile.query` call plus JSON extraction, so once enough tokens are minted to an address, a single call to `balanceOf(victim)` will consume more gas than fits in an EVM call/block, permanently reverting for that address — analogous to `Splitter.claimForAllWindows()` becoming permanently uncallable once `currentWindow` (attacker-incrementable via `incrementWindow()`) grows too large.

Unlike the original Solidity `ERC721` implementation this pointer overrides, which tracks balances in O(1) storage, this pointer recomputes balance by re-enumerating all owned tokens on every call, with no page/result cap exposed to the caller.

### Impact Explanation
Any EVM contract (marketplace, lending/collateral check, airdrop/allowlist gate, wrapper contract) that calls `balanceOf()` on a CW721 EVM pointer to check NFT ownership can be permanently denied service against a specific address once that address is attacker-inflated with tokens on the underlying CW721 contract. Because `balanceOf` is a standard ERC721 interface function relied upon by other on-chain integrations, this can cause a broken/unusable integration for the targeted holder (freezing operations gated on a successful `balanceOf()` call) with no way to recover short of the pointer contract being redeployed with pagination-limited semantics.

### Likelihood Explanation
Exploitability depends on the underlying CW721 contract allowing an attacker to mint (or otherwise cause) a very large number of tokens to be owned by the victim address; for permissionless-mint or attacker-owned/controlled CW721 collections behind a pointer, this is straightforward and cheap to trigger, matching the report's threat model where an unprivileged actor repeatedly grows an unbounded counter (`incrementWindow()`) to break a downstream aggregation function.

### Recommendation
Bound `balanceOf()`'s work: either cap the number of pagination rounds performed within a single call, expose a paginated `balanceOfRange(owner, startAfter, limit)`-style accessor instead of forcing full enumeration, or track balances incrementally in local pointer storage (mirroring standard OpenZeppelin ERC721 bookkeeping) rather than recomputing them by exhaustively querying the CW721 contract on every call.

### Proof of Concept
1. Deploy/identify a `CW721ERC721Pointer` for a CW721 collection that allows an attacker to mint tokens (or repeatedly transfer minted tokens) to a victim address.
2. Attacker mints/transfers enough tokens to the victim so that `ceil(tokenCount/1000)` pagination rounds through `WasmdPrecompile.query` exceed the gas available to a single EVM call/block.
3. Any subsequent call to `balanceOf(victim)` on the pointer reverts out-of-gas, permanently breaking any on-chain consumer of that call for the victim's address, as shown by the unbounded loop at: [2](#0-1)

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
