### Title
Unbounded pagination loop in `CW721ERC721Pointer.balanceOf` can spin on a hash-equality terminator that a CW721 contract can prevent from ever matching - (File: contracts/src/CW721ERC721Pointer.sol)

### Summary
`CW721ERC721Pointer.balanceOf()` paginates a wrapped CosmWasm CW721 contract's `tokens` query using a `while (keccak256(response) != terminator)` loop, where `terminator` is the hash of the literal string `{"tokens":[]}`. Termination depends entirely on the wrapped CW721 contract eventually returning that exact byte string. Any CW721 contract whose `tokens` query response never becomes exactly `{"tokens":[]}` — because it ignores `start_after`, returns whitespace/field-order variance, or simply always returns at least one token — makes the loop run for as long as gas allows, driving repeated expensive CosmWasm precompile queries per call. [1](#0-0) 

### Finding Description
`balanceOf` builds a `tokens` query, repeatedly calls `WasmdPrecompile.query` (which delegates into a CosmWasm contract execution/read via the wasmd precompile), and only stops when the raw response bytes hash exactly to `keccak256("{\"tokens\":[]}")`: [2](#0-1) 

This is analogous to the pypdf bug class (CWE-834, uncontrolled loop with a fragile/absent termination condition — the DCTDecode inline-image scan loops until an EOI marker that may never appear). Here the loop's termination condition is an exact-byte-match on a single hardcoded JSON string produced by an external, attacker-influenced program (the CosmWasm CW721 contract behind the pointer):

- The `Cw721Address` pointed-to contract is arbitrary CosmWasm code (pointers can be created for any CW721-compatible contract via governance/permissionless pointer creation flows elsewhere in the codebase).
- Nothing in `balanceOf` bounds the number of iterations, requires `startAfter` progress, or validates that the wrapped contract's pagination actually converges.
- A CW721 contract that (a) ignores the `start_after` field, (b) always returns a non-empty `tokens` list, or (c) returns a response whose bytes are never byte-identical to `{"tokens":[]}` (e.g., pretty-printed/whitespace differences, or an intentionally crafted contract) causes the loop to never satisfy the exit condition.
- Each iteration performs a full CosmWasm contract query through the `wasmd` precompile — noticeably more expensive per iteration than a raw byte scan — so an attacker-controlled contract can force many expensive precompile calls per top-level EVM call before the caller's gas is exhausted.

Unlike the pypdf case (a genuinely unbounded loop with no resource limit at all), this loop is nominally bounded by EVM gas: each `WasmdPrecompile.query` call consumes gas, so a state-changing transaction will eventually revert with out-of-gas. However, this is a materially weaker protection than a real termination guarantee:
- `eth_call`/`eth_estimateGas` JSON-RPC paths on public RPC nodes typically use a large gas cap for view calls (a `balanceOf` in the wild is treated as a cheap view function by wallets, block explorers, marketplaces, and other integrating contracts that call it eagerly and repeatedly), so a node can spend substantial wall-clock time per call.
- Because `balanceOf` is a standard ERC721 interface method invoked automatically by many callers (marketplaces, aggregators, wallets, other contracts implementing `IERC721Enumerable`-style flows), the attack does not require the victim to intentionally call an obscure function — a widely-used view method is the affected surface.

### Impact Explanation
A malicious or buggy CW721 contract behind an ERC721 pointer can turn `balanceOf(address)` — a function any unprivileged EVM caller or integrating contract invokes — into an expensive, effectively-unbounded loop of CosmWasm precompile queries. Repeated invocation (e.g., by indexers, RPC clients probing balances, or other contracts) can consume disproportionate node CPU/time relative to the gas charged, since a hash-equality-only pagination terminator does not force convergent progress. This does not directly cause fund loss, but it degrades public RPC node availability and can slow down execution of transactions/queries that touch this pointer, which is the class of impact the analog targets (resource exhaustion / hang risk from an unterminated parsing loop keyed on an external, attacker-influenced input stream).

### Likelihood Explanation
Likelihood is moderate: it requires a CW721 pointer to exist for a CW721 contract whose `tokens` query pagination is non-standard or adversarial. Since CW721 contracts can be arbitrary CosmWasm code and pointer creation is a normal, permissionless/governance-supported flow in this codebase, an attacker who deploys their own CW721 contract and registers a pointer for it fully controls the response semantics and can trivially make `{"tokens":[]}` unreachable. The affected function (`balanceOf`) is called broadly by external tooling without any special privilege.

### Recommendation
Do not rely on exact-byte-match against a hardcoded terminator string to end pagination. Instead:
- Enforce an explicit iteration cap (e.g., stop after N pages regardless of response content) and revert/return a bounded result if the cap is hit.
- Parse the `tokens` array length from the JSON response (already done via `JsonPrecompile.extractAsBytesList`) and terminate when the returned list length is `0`, rather than comparing the entire raw response hash to a fixed string.
- Require monotonic progress of `start_after` and treat a non-progressing/repeating cursor as an error condition to abort the loop safely instead of looping again.

### Proof of Concept
1. Deploy a CosmWasm CW721-like contract whose `tokens` query handler ignores `start_after` and/or `limit`, and always returns a non-empty, non-canonical JSON body (e.g., `{"tokens": ["1"]}` with a trailing space or reordered fields on every call, so it is never byte-identical to `{"tokens":[]}`).
2. Register/obtain a `CW721ERC721Pointer` for that contract (via the standard native/CW-to-ERC pointer creation path elsewhere in the codebase).
3. Call `balanceOf(anyAddress)` on the pointer contract from an EVM client, or have any wallet/marketplace/indexer call it as part of normal `IERC721` integration.
4. Observe that the `while (keccak256(response) != terminator)` loop in `balanceOf` (contracts/src/CW721ERC721Pointer.sol:67) never exits normally; it issues repeated `WasmdPrecompile.query` calls until the caller's supplied gas (or an `eth_call` gas cap) is exhausted, each iteration performing a full CosmWasm contract execution through the wasmd precompile. [1](#0-0)

### Citations

**File:** contracts/src/CW721ERC721Pointer.sol (L55-77)
```text
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
```
