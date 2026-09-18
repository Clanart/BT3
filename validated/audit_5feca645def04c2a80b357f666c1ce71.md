## Title
CW20/CW721/CW1155 ERC-pointer contracts violate the "view functions MUST NOT revert" expectation because `balanceOf()`/`totalSupply()`/`decimals()` proxy to a CosmWasm contract query that can fail or panic - ([File: contracts/src/CW20ERC20Pointer.sol])

### Summary
The Code4rena finding shows that `AccountingManager.totalAssets()` (and therefore `convertToShares`/`convertToAssets`) can revert because it transitively calls an external price oracle that is allowed to revert, violating the ERC-4626 "MUST NOT revert" guarantee. The same bug class exists in sei-chain's production ERC20/ERC721/ERC1155 "pointer" contracts, which wrap a CosmWasm (CW20/CW721/CW1155) contract behind a standard EVM token interface. Their core view functions do not compute state locally — they synchronously proxy every call through the `wasmd` precompile to the underlying CosmWasm contract, so any failure, panic, or unexpected response shape on the CW side makes the "MUST-not-fail" ERC20/721/1155 view functions revert.

### Finding Description
`CW20ERC20Pointer.balanceOf`, `totalSupply`, `decimals`, and `allowance` all build a JSON smart-query and call `WasmdPrecompile.query(Cw20Address, req)`, then decode the result with the `Json` precompile: [1](#0-0) 

`WasmdPrecompile.query()` is backed by `wasmd.go`'s `query()` executor, which calls `p.wasmdViewKeeper.QuerySmartSafe(ctx, contractAddr, req)` and propagates any error from the underlying CosmWasm contract straight back as a Go `error`, which the precompile framework turns into an EVM revert: [2](#0-1) 

This means `balanceOf()`/`totalSupply()` (functions that are foundational and universally assumed by EVM tooling/DeFi contracts to succeed for any valid address/denom) will revert whenever:
- the target CW20 contract's `balance`/`token_info` query panics (e.g., due to a bug, migration, or intentional pause) — `QuerySmartSafe` converts panics to errors, but the pointer contract has no fallback and simply bubbles the revert up;
- the target CW20 contract returns a response missing the expected JSON field, causing `JsonPrecompile.extractAsUint256` to revert with `Invalid ... response`-style errors (see the equivalent CW1155 pattern requiring `parseResponse.length == accounts.length`) — this can be seen mirrored in `CW1155ERC1155Pointer.balanceOfBatch`: [3](#0-2) 
- the contract-query gas budget is exceeded inside the CosmWasm execution, causing an out-of-gas panic recovered by `QuerySmartSafe` as an error rather than a graceful degraded return value.

Just as `NoyaValueOracle._getValue()` reverting cascades into `totalAssets()`, `convertToShares()`, and `convertToAssets()` all reverting, here a single misbehaving/underlying CW20/CW721/CW1155 contract reverting on query cascades into every pointer-contract standard interface method (`balanceOf`, `totalSupply`, `decimals`, `allowance`, `ownerOf`, etc.) reverting for every caller, with no way for callers to catch a "degraded" answer instead of an outright revert.

### Impact Explanation
Pointer contracts are the canonical, permissionless bridge that lets any EVM contract or wallet interact with a CosmWasm token as if it were a standard ERC20/721/1155 token. Any EVM-side protocol (DEX, lending market, liquidation bot, custody contract) that integrates a pointer token as collateral or as a tradable asset depends on `balanceOf()`/`ownerOf()` never reverting for normal accounts. If the underlying CW20/CW721/CW1155 contract becomes non-responsive to queries (through its own bug, a state migration, or simply exceeding gas under certain data sizes as seen in the paginated `tokens` query in `CW721ERC721Pointer`), every EVM consumer of that pointer is denied the ability to read balances/ownership. Contracts that gate withdrawals, liquidations, or transfers behind a `balanceOf`/`ownerOf` check can become permanently unable to release the underlying assets they hold, since there is no fallback path — this is a freezing-of-funds vector reached purely by ordinary use (deploying a pointer for a token whose CW20/CW721/CW1155 implementation later regresses), not by any privileged action.

### Likelihood Explanation
Pointer contracts are a first-class, permissionless bridging primitive on sei-chain reachable by any CosmWasm user or tokenfactory/CW20 deployer; a CW20/CW721/CW1155 contract's query implementation is entirely outside sei-chain's control once deployed (any contract deployer can point at it), and CosmWasm contracts are known to panic on bad state, hit gas limits on unbounded loops, or get migrated to break existing query shapes. Because the pointer's proxy functions have zero resiliency (no try/catch, no cached/last-known value, no bounded degrade path), the likelihood of the underlying CW contract failing at some point in its lifecycle is realistic and entirely outside the pointer deployer's control.

### Recommendation
Add defensive fallbacks in the pointer contracts' view functions (`CW20ERC20Pointer.balanceOf/totalSupply/decimals/allowance`, `CW721ERC721Pointer`, `CW1155ERC1155Pointer`) so that a failing `WasmdPrecompile.query()` call or malformed JSON response does not propagate as an EVM revert from a function callers assume cannot fail — for example, catch the query failure and return a cached last-known value, or clearly document/emit degraded state instead of a hard revert, mirroring the C4 recommendation of "return 0 instead of reverting" for unavailable-oracle cases.

### Proof of Concept
1. Deploy a CosmWasm CW20 contract and register it via the pointer precompile (`AddCW20Pointer`) to get a `CW20ERC20Pointer` EVM contract.
2. Any EVM contract (e.g., a lending market) integrates the pointer as an ERC20 collateral asset and calls `balanceOf(user)` as part of a withdrawal/liquidation flow — see `contracts/src/CW20ERC20Pointer.sol:36-42`.
3. Cause the underlying CW20 contract's `balance` query handler to panic or return a response without the `"balance"` key (via a contract migration, a bug, or an out-of-gas condition inside the CosmWasm VM during query execution).
4. `wasmdViewKeeper.QuerySmartSafe` returns an error (`precompiles/wasmd/legacy/v640/wasmd.go:454-458`), which the precompile execution model converts to an EVM revert; `CW20ERC20Pointer.balanceOf()` reverts for every caller instead of returning a value.
5. Any EVM contract relying on `balanceOf()` to succeed (as ERC20 conventionally guarantees) now permanently reverts on that code path, e.g., blocking withdrawal/liquidation of user funds held against that pointer token.

### Citations

**File:** contracts/src/CW20ERC20Pointer.sol (L30-48)
```text
    function decimals() public view override returns (uint8) {
        string memory req = _curlyBrace(_formatPayload("token_info", "{}"));
        bytes memory response = WasmdPrecompile.query(Cw20Address, bytes(req));
        return uint8(JsonPrecompile.extractAsUint256(response, "decimals"));
    }

    function balanceOf(address owner) public view override returns (uint256) {
        require(owner != address(0), "ERC20: balance query for the zero address");
        string memory ownerAddr = _formatPayload("address", _doubleQuotes(AddrPrecompile.getSeiAddr(owner)));
        string memory req = _curlyBrace(_formatPayload("balance", _curlyBrace(ownerAddr)));
        bytes memory response = WasmdPrecompile.query(Cw20Address, bytes(req));
        return JsonPrecompile.extractAsUint256(response, "balance");
    }

    function totalSupply() public view override returns (uint256) {
        string memory req = _curlyBrace(_formatPayload("token_info", "{}"));
        bytes memory response = WasmdPrecompile.query(Cw20Address, bytes(req));
        return JsonPrecompile.extractAsUint256(response, "total_supply");
    }
```

**File:** precompiles/wasmd/legacy/v640/wasmd.go (L420-463)
```go
	return
}

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
```

**File:** contracts/src/CW1155ERC1155Pointer.sol (L230-239)
```text
        ownerTokens = string.concat(ownerTokens, "]");
        string memory req = _curlyBrace(_formatPayload("balance_of_batch", ownerTokens));
        bytes memory response = WasmdPrecompile.query(Cw1155Address, bytes(req));
        bytes[] memory parseResponse = JsonPrecompile.extractAsBytesList(response, "balances");
        require(parseResponse.length == accounts.length, "Invalid balance_of_batch response");
        balances = new uint256[](parseResponse.length);
        for (uint256 i = 0; i < parseResponse.length; i++) {
            balances[i] = JsonPrecompile.extractAsUint256(parseResponse[i], "amount");
        }
    }
```
