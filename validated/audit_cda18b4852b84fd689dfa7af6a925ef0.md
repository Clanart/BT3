### Title
Unbounded on-chain query loop in `CW721ERC721Pointer.balanceOf` enables gas-exhaustion DoS when an attacker inflates a victim's CW721 token count - (File: contracts/src/CW721ERC721Pointer.sol)

### Summary
`CW721ERC721Pointer.balanceOf` computes an owner's NFT balance by repeatedly querying the underlying CosmWasm CW721 contract in pages of 1000 tokens, looping until an empty-page terminator is returned, with no bound on the number of pages or total gas consumed. [1](#0-0)  Because CW721 tokens can be minted/transferred cheaply and in bulk to any address, an attacker can inflate a victim's (or their own) token count on the wrapped collection so that any EVM call path that invokes `balanceOf` on the pointer runs out of gas, exactly the "unbounded owned-token enumeration" DoS pattern described in the RabbitHole `getOwnedTokenIdsOfQuest`/`Quest.claim` report.

### Finding Description
`balanceOf(address owner_)` builds a CW721 `tokens` query with `"limit":1000` and loops with `while (keccak256(response) != terminator)`, issuing one `WasmdPrecompile.query` per 1000 tokens owned and accumulating `numTokens` until an empty list is returned. [2](#0-1)  Each iteration performs a full CosmWasm smart-contract query (JSON marshal/unmarshal plus a wasm VM query call) via the `IWasmd` precompile, which is far more gas-expensive per unit than a native Go/state read. [3](#0-2)  There is no cap on the number of pages processed: an address holding tens or hundreds of thousands of tokens on the wrapped CW721 collection causes `balanceOf` to loop that many times, and the same unbounded-enumeration approach appears in the underlying CW721 pointer's Rust query helpers (`query_tokens`, iterating token-by-token up to `num_tokens`). [4](#0-3)  `balanceOf` is a standard ERC721 method that other on-chain contracts (marketplaces, NFT-gated staking/lending, airdrop/claim contracts, DeFi integrations) commonly call as part of state-changing transaction logic to check a user's holdings; if that check is embedded in a transaction (not just an `eth_call`), the caller pays for however many pages are enumerated. An attacker can grief any address by transferring/minting a large number of cheap CW721 tokens into it on the wrapped collection (the same griefing technique described in the RabbitHole report, where a griefer sends already-claimed receipt tokens to a victim to inflate their owned-token count), permanently degrading or breaking any downstream contract logic that calls `balanceOf` on the pointer for that address, and forcing the victim (or callers interacting with the victim's address) to burn gas on failing transactions.

### Impact Explanation
Any EVM transaction whose execution path calls `CW721ERC721Pointer.balanceOf` for an address holding a large, attacker-inflated number of tokens can be forced to exceed the block gas limit, permanently reverting. This causes fee loss for the caller and a durable denial of service for legitimate use of the pointer contract (and any protocol built on top of it) for the targeted address, matching the accepted Medium-severity impact class in the referenced report (transaction fee loss / inability to interact due to unbounded owned-token enumeration).

### Likelihood Explanation
The attack requires only ordinary, unprivileged actions: minting/transferring CW721 tokens to a target address is a normal wasm message that any user can send, and no special permissions are needed to make many such transfers cheaply. The only cost to the attacker is the gas/fees to mint or transfer a large batch of tokens once; after that, every subsequent `balanceOf` call for the victim (from any dependent contract) is degraded, so the griefing is persistent and cheap relative to its impact.

### Recommendation
Avoid full enumeration for balance computation. Cache/track balances incrementally in contract storage instead of recomputing via a paginated on-chain query loop, or cap the number of pages queried (returning a bounded/approximate result or reverting deterministically past a threshold) similar to how `getOwnedTokenIdsOfQuest`-style enumeration should be replaced with an off-chain-provided token list plus on-chain verification, as recommended in the analog report. At minimum, bound the loop with a fixed maximum iteration count so gas cost is predictable and capped regardless of how many tokens are transferred to an address.

### Proof of Concept
1. Deploy/point a `CW721ERC721Pointer` at a CW721 collection controlled by the attacker (or any collection with cheap minting).
2. Attacker mints or transfers a very large number of tokens (e.g., tens of thousands) to the victim's Sei-associated address.
3. Any contract or user transaction that calls `pointer.balanceOf(victim)` as part of its logic (e.g., an NFT-gated action) must page through the entire holding in batches of 1000 via `WasmdPrecompile.query` inside `balanceOf`'s `while` loop. [5](#0-4) 
4. Once the number of pages required exceeds what fits in the block gas limit, the calling transaction reverts out-of-gas, denying the victim (and any integration depending on their balance) the ability to use `balanceOf`-dependent functionality, while the caller still pays the failed transaction's fee.

### Citations

**File:** contracts/src/CW721ERC721Pointer.sol (L9-11)
```text
import {IWasmd} from "./precompiles/IWasmd.sol";
import {IJson} from "./precompiles/IJson.sol";
import {IAddr} from "./precompiles/IAddr.sol";
```

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

**File:** example/cosmwasm/cw721/src/contract.rs (L350-384)
```rust
pub fn query_tokens(
    deps: Deps<EvmQueryWrapper>,
    env: Env,
    owner: String,
    start_after: Option<String>,
    limit: Option<u32>,
) -> StdResult<TokensResponse> {
    let erc_addr = ERC721_ADDRESS.load(deps.storage)?;
    let querier = EvmQuerier::new(&deps.querier);
    let num_tokens = query_num_tokens(deps, env.clone())?.count;
    let start_after_id = Int256::from_str(&start_after.unwrap_or("-1".to_string()))?;
    let limit = limit.unwrap_or(DEFAULT_LIMIT).min(MAX_LIMIT) as usize;

    let mut cur = Int256::zero();
    let mut counter = 0;
    let mut tokens: Vec<String> = vec![];
    while counter < num_tokens && tokens.len() < limit {
        let cur_str = cur.to_string();
        let t_owner = match querier.erc721_owner(
            env.clone().contract.address.into_string(),
            erc_addr.clone(),
            cur_str.to_string(),
        ) {
            Ok(res) => res.owner,
            Err(_) => "".to_string(),
        };
        if t_owner != "" {
            counter += 1;
            if (owner.is_empty() || t_owner == owner) && cur > start_after_id {
                tokens.push(cur_str);
            }
        }
        cur += Int256::one();
    }
    Ok(TokensResponse { tokens })
```
