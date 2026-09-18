I found a concrete analog: an attacker-controlled, unbounded query loop in the `CW721ERC721Pointer.balanceOf` function, reachable by any EVM caller through the CW↔EVM pointer bridge.

### Title
Unbounded pagination loop in `CW721ERC721Pointer.balanceOf` allows a malicious CW721 contract/response to hang or exhaust gas of any EVM caller querying an NFT pointer - (File: contracts/src/CW721ERC721Pointer.sol)

### Summary
`CW721ERC721Pointer.balanceOf` paginates through a CosmWasm `tokens` query via the Wasmd precompile, looping until the JSON response hash equals a fixed "empty" terminator. The loop's termination and progress entirely depend on data returned by the underlying CW721 contract, which for pointer contracts is often an arbitrary/attacker-influenced target. If the response never matches the terminator (e.g., contract returns a token list whose last element repeats, or a response shape that is non-empty but never hashes to the terminator), the loop runs until gas is exhausted, mirroring the "infinite loop on crafted input" bug class in CVE-2022-0586 (RTMPT dissector looping forever on malformed input instead of validating and bounding progress).

### Finding Description
```solidity
function balanceOf(address owner_) public view override returns (uint256) {
    ...
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
        req = string.concat(string.concat("{\"tokens\":{", qb), "}}");
        response = WasmdPrecompile.query(Cw721Address, bytes(req));
    }
    return numTokens;
}
``` [1](#0-0) 

The loop's exit condition is a byte-exact match of the wasm query response against the constant `{"tokens":[]}` hash. This is fragile: any CW721 contract (which the pointer wraps and which is fully controlled by whoever deployed it, since pointer contracts can be created for arbitrary CosmWasm collections) can return a response that never exactly matches `{"tokens":[]}` — e.g., a contract returning the same non-empty token list forever, or a contract that returns tokens in a shape with extra whitespace/fields that still round-trips a non-empty list — causing `startAfter` to stop advancing (or cycle) and the `while` loop to iterate until the caller's gas is exhausted. Because this is a `view` function invoked via `staticcall`/`eth_call` or from another EVM contract, an unprivileged caller (or contract logic depending on `balanceOf`) can be forced into unbounded looping, consuming the full block gas limit on read paths, and any other EVM caller composing with the pointer (e.g., a DeFi contract calling `balanceOf` in a transaction) risks reverting only after burning maximum gas, or in RPC `eth_call` context, tying up node resources for extended time similar to the Wireshark dissector spin.

Unlike the referenced protobuf skip-loop implementations in this same codebase (`skip<X>` generated functions), which strictly bound progress via `iNdEx < l` checks tied to consumed bytes, this Solidity loop's progress is tied to semantic content of an external, attacker-influenceable contract's response rather than to a monotonic, protocol-enforced byte cursor.

### Impact Explanation
A caller (EOA or contract) invoking `balanceOf` against a pointer to a malicious/misbehaving CW721 contract can be forced to burn all available gas in a single call, and any composing EVM contract that unconditionally calls a pointer's `balanceOf` (e.g., in a `transfer`/pre-check hook) can be denial-of-serviced by an attacker who controls or influences the paired CW721 contract's query responses. This does not directly cause fund loss, but produces reliable gas-griefing / DoS against on-chain consumers and against `eth_call`-based RPC nodes serving `balanceOf` for that pointer, which is the class of impact this rubric accepts (crash/hang of default-configuration RPC nodes servicing reads, plus gas-griefing of any dependent transaction).

### Likelihood Explanation
Reachable by any account calling `balanceOf` on a CW721 pointer contract, or by any RPC client issuing `eth_call`/`eth_estimateGas` for that method — no special privilege required. Triggering it requires the paired CW721 contract to return a query response that never matches the exact `{"tokens":[]}` terminator, which is plausible for many non-standard or intentionally malicious CW721 implementations (nothing in the pointer creation path validates spec compliance of the CW721 target).

### Recommendation
Bound the pagination loop with an explicit iteration cap independent of the returned data (e.g., cap at a maximum number of pages/gas budget), and additionally detect lack of progress (e.g., compare `startAfter`/last-token value across iterations and abort if unchanged) rather than relying solely on an exact-hash match against an empty-list literal.

### Proof of Concept
1. Deploy or point a `CW721ERC721Pointer` at a CosmWasm contract whose `tokens` query handler always returns a fixed non-empty token list (e.g., always returns `{"tokens":["token1"]}` regardless of `start_after`), ignoring pagination cursors.
2. Call `balanceOf(anyOwner)` on the pointer contract via `eth_call` or from a contract.
3. Observe the `while` loop never encounters `keccak256(response) == keccak256("{\"tokens\":[]}")`, causing it to loop until gas is exhausted (out-of-gas revert) or, for RPC `eth_call` without a tight gas cap, tying up node CPU for an extended query. [1](#0-0)

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
