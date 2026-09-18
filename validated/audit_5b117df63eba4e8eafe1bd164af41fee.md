### Title
CW20→ERC20 pointer `decimals()` silently truncates out-of-`uint8`-range CW20 metadata instead of reverting, letting a malicious CW20 spoof its EVM-visible decimals - (File: `contracts/src/CW20ERC20Pointer.sol`)

### Summary
`CW20ERC20Pointer.decimals()` queries a CW20 contract's `token_info` and casts the returned JSON `decimals` field directly to `uint8` without any range validation. Because the value is parsed as an arbitrary `uint256` and then narrowed with a raw Solidity cast, any CW20 contract that reports a `decimals` value outside `[0,255]` (e.g. `300`) causes silent modulo-256 wraparound rather than a revert, producing an incorrect, attacker-chosen `decimals()` result from a completely permissionless, ordinary pointer-registration flow.

### Finding Description
`CW20ERC20Pointer.decimals()` does:
```solidity
function decimals() public view override returns (uint8) {
    string memory req = _curlyBrace(_formatPayload("token_info", "{}"));
    bytes memory response = WasmdPrecompile.query(Cw20Address, bytes(req));
    return uint8(JsonPrecompile.extractAsUint256(response, "decimals"));
}
``` [1](#0-0) 

`JsonPrecompile.extractAsUint256` (the `IJson` precompile) parses whatever string is under the `decimals` key into an arbitrary-precision `big.Int` with no upper bound check besides a raw string length cap of 100 characters:
```go
func (p PrecompileExecutor) ExtractAsUint256(...) (*big.Int, error) {
    ...
    strValue := strings.Trim(string(result), "\"")
    if len(strValue) > 100 {
        return nil, fmt.Errorf("value string too long: got %d, max 100", len(strValue))
    }
    value, success := new(big.Int).SetString(strValue, 10)
    ...
    return value, nil
}
``` [2](#0-1) 

There is no validation that `decimals` fits within the canonical CW20 `u8` range. A wasm contract is not forced to use `cw20-base`'s Rust `u8` type for its `token_info` response — any contract that satisfies the `{"token_info":{}}` query shape (returning JSON with `name`, `symbol`, `decimals`, `total_supply`) can be pointed to via the permissionless `AddCW20` pointer-registration method:
```go
func (p PrecompileExecutor) AddCW20(ctx sdk.Context, ...) (ret []byte, remainingGas uint64, err error) {
    ...
    cwAddr := args[0].(string)
    ...
    res, err := p.wasmdKeeper.QuerySmartSafe(ctx, cwAddress, []byte("{\"token_info\":{}}"))
    ...
    contractAddr, err := p.evmKeeper.UpsertERCCW20Pointer(ctx, evm, cwAddr, utils.ERCMetadata{Name: name, Symbol: symbol})
    ...
}
``` [3](#0-2) 

Note that `AddCW20` itself only forwards `name`/`symbol` into the deployed pointer's constructor; `decimals` is deliberately *not* cached at deployment time — it is re-queried live on every `decimals()` call from the CW20 contract, meaning a malicious contract can also change its reported `decimals` at will after the pointer is created and being relied upon by integrators. Since Solidity's `uint8(x)` on a `uint256` performs silent modulo-256 truncation rather than reverting, a contract reporting `decimals = 300` yields `decimals() == 44`, `decimals = 256` yields `0`, etc., with no error surfaced to any caller.

This is the same underlying bug class as the referenced report: decimals-handling code that implicitly assumes the value fits a narrow range (there, `uint8`/`<=18` causing a revert; here, `uint8` causing silent wraparound) instead of explicitly validating and safely rejecting out-of-range values.

### Impact Explanation
`decimals()` is the standard mechanism DeFi integrators (DEXes, wallets, price oracles, aggregators) use to convert raw ERC20 balances/amounts into human/economic value. Since `balanceOf`/`transfer` amounts on the pointer are unscaled raw CW20 amounts, any integrator computing `amount / 10**decimals()` (or similar) against a maliciously-configured pointer will use an attacker-chosen, wrapped decimals value. An attacker can deploy a CW20 contract that reports a manipulated `decimals`, get it pointer-registered (a permissionless, unauthenticated operation any address can invoke), and then trade against protocols that trust the pointer's `decimals()` to price the token — extracting value through the resulting mispricing. This is a fund-loss vector reachable purely through normal CosmWasm contract deployment plus the public `AddCW20` pointer registration, not requiring any privileged role.

### Likelihood Explanation
Likelihood is moderate: exploitation requires (1) deploying a non-standard CW20 contract that reports an out-of-`u8`-range `decimals` field (trivial, since CosmWasm contracts are not required to use the `cw20-base` implementation), (2) registering it as a pointer via the public `AddCW20` precompile call, and (3) inducing a downstream integrator/protocol to treat the pointer as a normal token and rely on its `decimals()`. All three steps are achievable by an ordinary, unprivileged transaction sender/CosmWasm deployer with no special permissions.

### Recommendation
Validate the `decimals` value returned from `token_info` before casting: reject (revert) values outside `[0, 255]` explicitly, or better, change `IJson.extractAsUint256`/its callers to expose a bounds-checked extraction method (e.g., `extractAsUint8`) that reverts instead of silently truncating. Consider caching `decimals` at pointer-registration time (like `name`/`symbol`) so that its value cannot be changed after deployment by a mutable/malicious CW20 contract.

### Proof of Concept
1. Deploy a custom CosmWasm contract (not `cw20-base`) that implements `{"token_info":{}}` returning `{"name":"X","symbol":"X","decimals":"300","total_supply":"1000000000000000000"}`.
2. Call the pointer precompile's `addCW20Pointer(contractAddr)` (permissionless) to deploy a `CW20ERC20Pointer` for it — see `AddCW20` at [4](#0-3) .
3. Call `decimals()` on the deployed pointer; observe it returns `44` (`300 mod 256`) instead of reverting, per the cast in [1](#0-0) .
4. Any integrator computing value as `rawAmount / 10**decimals()` will use the wrong scale factor, enabling mispriced swaps/valuations against the attacker's token.

### Citations

**File:** contracts/src/CW20ERC20Pointer.sol (L30-34)
```text
    function decimals() public view override returns (uint8) {
        string memory req = _curlyBrace(_formatPayload("token_info", "{}"));
        bytes memory response = WasmdPrecompile.query(Cw20Address, bytes(req));
        return uint8(JsonPrecompile.extractAsUint256(response, "decimals"));
    }
```

**File:** precompiles/json/json.go (L164-200)
```go
func (p PrecompileExecutor) ExtractAsUint256(_ sdk.Context, _ *abi.Method, args []interface{}, value *big.Int) (*big.Int, error) {
	if err := pcommon.ValidateNonPayable(value); err != nil {
		return nil, err
	}

	if err := pcommon.ValidateArgsLength(args, 2); err != nil {
		return nil, err
	}

	// type assertion will always succeed because it's already validated in p.Prepare call in Run()
	bz := args[0].([]byte)
	decoded := map[string]gjson.RawMessage{}
	if err := gjson.Unmarshal(bz, &decoded); err != nil {
		return nil, err
	}
	key := args[1].(string)
	result, ok := decoded[key]
	if !ok {
		return nil, fmt.Errorf("input does not contain key %s", key)
	}

	// Assuming result is your byte slice
	// Convert byte slice to string and trim quotation marks
	strValue := strings.Trim(string(result), "\"")

	if len(strValue) > 100 {
		return nil, fmt.Errorf("value string too long: got %d, max 100", len(strValue))
	}

	// Convert the string to big.Int
	value, success := new(big.Int).SetString(strValue, 10)
	if !success {
		return nil, fmt.Errorf("failed to convert %s to big.Int", strValue)
	}

	return value, nil
}
```

**File:** precompiles/pointer/pointer.go (L134-164)
```go
func (p PrecompileExecutor) AddCW20(ctx sdk.Context, method *ethabi.Method, caller common.Address, args []interface{}, value *big.Int, evm *vm.EVM, hooks *tracing.Hooks) (ret []byte, remainingGas uint64, err error) {
	if err := pcommon.ValidateNonPayable(value); err != nil {
		return nil, 0, err
	}
	if err := pcommon.ValidateArgsLength(args, 1); err != nil {
		return nil, 0, err
	}
	cwAddr := args[0].(string)
	cwAddress, err := sdk.AccAddressFromBech32(cwAddr)
	if err != nil {
		return nil, 0, err
	}
	res, err := p.wasmdKeeper.QuerySmartSafe(ctx, cwAddress, []byte("{\"token_info\":{}}"))
	if err != nil {
		return nil, 0, err
	}
	formattedRes := map[string]interface{}{}
	if err := json.Unmarshal(res, &formattedRes); err != nil {
		return nil, 0, err
	}
	name := formattedRes["name"].(string)
	symbol := formattedRes["symbol"].(string)
	contractAddr, err := p.evmKeeper.UpsertERCCW20Pointer(ctx, evm, cwAddr, utils.ERCMetadata{Name: name, Symbol: symbol})
	if err != nil {
		return nil, 0, err
	}

	ret, err = method.Outputs.Pack(contractAddr)
	remainingGas = pcommon.GetRemainingGas(ctx, p.evmKeeper)
	return
}
```
