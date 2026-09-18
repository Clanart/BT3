### Title
Unbounded owner-controlled pagination loop in `CW721ERC721Pointer.balanceOf` causes gas-exhaustion DoS - (File: contracts/src/CW721ERC721Pointer.sol)

### Summary
`CW721ERC721Pointer.balanceOf()` computes an owner's NFT count by repeatedly calling the `wasmd` `query` precompile in pages of 1000 tokens until the underlying CW721 contract returns an empty `tokens` list. The number of loop iterations (and thus gas consumed) is directly proportional to how many tokens a Sei address owns in the pointed-to CW721 collection — a count any unprivileged CosmWasm user can inflate by minting/holding many tokens to a target address. This mirrors the Alchemix `MAX_DELEGATES` bug class: an attacker-controlled, unbounded-length collection is walked in a loop inside a function that other contracts assume is cheap/bounded, causing out-of-gas reverts once the collection grows large enough.

### Finding Description
`balanceOf` uses a `while` loop keyed off a CosmWasm CW721 `{"tokens":{...}}` query with `"limit":1000` and a `start_after` cursor, incrementing `numTokens` until the terminator `{"tokens":[]}` hash is reached: [1](#0-0) 

Each loop iteration performs a full `WasmdPrecompile.query()` call (a CosmWasm smart-query dispatched through the `wasmd` precompile) plus a `JsonPrecompile.extractAsBytesList` decode of up to 1000 token IDs: [2](#0-1) 

There is no upper bound on how many times this loop can run — it runs `ceil(ownerTokenCount / 1000)` times. Because CW721 contracts frequently allow open/public minting (or an attacker can simply acquire/hold many tokens legitimately), any address's token count in the underlying CW721 collection is fully attacker-influenceable. An attacker can mint (or transfer) tens or hundreds of thousands of CW721 tokens to a victim Sei address — this requires only ordinary CosmWasm `execute` messages against the CW721 contract (an "unprivileged ... CosmWasm user" action), no special privilege on the pointer contract itself. Once the victim's token count is large enough that `numTokens/1000` iterations of wasmd cross-VM queries exceed the gas available to a single EVM call, every future call to `balanceOf(victim)` on the ERC721 pointer will revert with out-of-gas.

This differs from the reference report's exact mechanism (delegate-array traversal in Solidity) but is structurally identical: a public state-mutation path (CW721 mint/transfer) inflates an unbounded per-address collection that a different, gas-limited entry point (`balanceOf`) must fully traverse, with no pagination exposed to or usable by the caller. Unlike `x/tokenfactory`'s `GetAllDenomsFromCreator`, which is explicitly documented as "safe... gas metering bounds execution cost" for wasm bindings queries, this loop is reachable from ordinary EVM contract calls where callers expect ERC721's `balanceOf` semantics (O(1) or cheap) and gas budgets are typically sized accordingly (e.g. `21000`–`100000`+ gas calls from marketplaces, staking, or lending integrations).

### Impact Explanation
Once a victim's CW721 balance is inflated past the gas-affordable iteration count, `balanceOf()` becomes permanently unusable for that address through this pointer contract — a denial of service. Any EVM protocol composing with the ERC721 pointer (marketplaces, lending/collateral checks, airdrops, or any contract calling `IERC721(pointer).balanceOf(victim)`) will revert whenever it touches the victim's address, and this condition cannot be undone by the victim (they cannot burn/reject tokens minted to them by a third party in many CW721 implementations, and even if they could, doing so at scale is itself costly). This is a griefing/DoS impact against a public, unprivileged entry point of a pointer contract that Sei deploys/promotes as the canonical CW721↔ERC721 bridge, satisfying the "crash/permanent freezing/DoS of default-configuration surface" bar via unavailability of a public interoperability contract's core read function.

### Likelihood Explanation
Likelihood is moderate-to-high: any CosmWasm user can call `mint` (if the target CW721 allows public minting) or otherwise acquire/hold a large number of tokens and transfer them to a target address using only standard `MsgExecuteContract` calls — no elevated privileges, no consensus assumptions, and no reliance on validator or peer behavior. The cost to the attacker is bounded by CW721 mint/transfer gas times the number of tokens, which is linear and payable by a single actor over time; the resulting DoS is permanent for the pointer's `balanceOf` on that address until CW721 balances shrink (which the attacker controls, not the victim).

### Recommendation
- Cap the number of pages `balanceOf` will traverse and revert/return a bounded approximate result (or require callers to paginate explicitly) rather than looping until exhaustion.
- Alternatively, track and cache token counts on pointer creation/mint/transfer/burn CW→EVM sync events instead of re-deriving via repeated CW721 `tokens` queries at call time.
- If exact enumeration must remain query-based, expose a paginated `balanceOfPaged(owner, startAfter, limit)` and make the unmarked `balanceOf` either bounded (e.g., cap at N pages and document as an upper bound) or non-view (allow callers to supply gas explicitly with documented cost-per-token).

### Proof of Concept
1. Deploy/point a `CW721ERC721Pointer` at an existing CW721 contract that allows public minting (or acquire an existing collection where minting is open).
2. As an unprivileged CosmWasm user, repeatedly call `MsgExecuteContract{mint}` against the CW721 contract to mint, e.g., 200,000+ tokens to a target Sei address (`victim`), 1000 tokens costing normal execute gas each — this can be batched across many transactions/blocks and is fully within `x/wasm` execute limits (no special permission needed if the collection's minter policy is public, or the attacker is simply the collection owner minting to a chosen victim).
3. From any EVM contract or `eth_call`, invoke `balanceOf(victimEvmAddress)` on the deployed `CW721ERC721Pointer`.
4. Observe that the call must loop `ceil(200000/1000) = 200` times, each performing a `wasmd` cross-VM smart query plus JSON extraction; measure gas consumption per iteration and show that a typical caller-supplied gas limit (e.g., 1–8M gas, or the per-call `eth_call`/EVM sub-call stipend used by a composing contract) is exhausted before the loop terminates, causing `balanceOf` to revert with out-of-gas for `victim` going forward.

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

**File:** precompiles/wasmd/wasmd.go (L295-336)
```go
func (p PrecompileExecutor) query(ctx sdk.Context, method *abi.Method, args []interface{}, value *big.Int) (ret []byte, remainingGas uint64, rerr error) {
	defer func() {
		if err := recover(); err != nil {
			ret = nil
			remainingGas = 0
			rerr = fmt.Errorf("%s", err)
			return
		}
	}()
	if err := pcommon.ValidateNonPayable(value); err != nil {
		rerr = err
		return
	}

	if err := pcommon.ValidateArgsLength(args, 2); err != nil {
		rerr = err
		return
	}

	contractAddrStr := args[0].(string)
	// addresses will be sent in Sei format
	contractAddr, err := sdk.AccAddressFromBech32(contractAddrStr)
	if err != nil {
		rerr = err
		return
	}
	req := args[1].([]byte)

	rawContractMessage := wasmtypes.RawContractMessage(req)
	if err := rawContractMessage.ValidateBasic(); err != nil {
		rerr = err
		return
	}
	res, err := p.wasmdViewKeeper.QuerySmartSafe(ctx, contractAddr, req)
	if err != nil {
		rerr = err
		return
	}
	ret, rerr = method.Outputs.Pack(res)
	remainingGas = pcommon.GetRemainingGas(ctx, p.evmKeeper)
	return
}
```
