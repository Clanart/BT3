### Title
Sign-losing conversion in `ExtractAsUint256` causes the JSON precompile to silently transform negative CW20 query values into large positive `uint256` amounts - ([File: precompiles/json/json.go])

### Summary
The `json` precompile's `extractAsUint256` method (used by CosmWasm→ERC20 pointer contracts such as `CW20ERC20Pointer.sol`) parses an arbitrary JSON field from a CW20 query response into a signed `big.Int` and then encodes it as an ABI `uint256` via `big.Int.FillBytes`, which silently drops the sign and uses the absolute value. This is the same bug class as the reported Firewall issue: a signed integer extracted from untrusted data is coerced into an unsigned value used downstream in balance/allowance logic, producing unexpected results.

### Finding Description
`PrecompileExecutor.ExtractAsUint256` decodes an arbitrary field of a CW20 JSON query response and converts it with `new(big.Int).SetString(strValue, 10)`, which accepts negative decimal strings (e.g. `"-100"`) and returns a negative `*big.Int` with `success == true`. There is no `Sign()` check rejecting negative values. [1](#0-0) 

The caller (`Execute`) then packs this value as a `uint256` output by calling `uint_.FillBytes(byteArr)`: [2](#0-1) 

`big.Int.FillBytes` formats the **absolute value** of `x` into the buffer — it does not reject or panic on negative numbers, it simply drops the sign. So a query response field of `"-100"` becomes ABI-encoded `100` (as a `uint256`), exactly analogous to the Firewall bug where a signed value is coerced to unsigned and loses its true (negative) semantics before being used in a comparison.

This function is wired into the ERC20↔CW20 pointer bridge. `CW20ERC20Pointer.sol` uses `extractAsUint256` to read `balance`, `allowance`, `total_supply`, and `decimals` directly from a CosmWasm CW20 contract's JSON query response: [3](#0-2) 

The `allowance` value returned this way then feeds directly into `approve()`'s signed-looking arithmetic (`currentAllowance - amount` / `amount - currentAllowance`) which decides whether to call `increase_allowance` or `decrease_allowance` on the underlying CW20 contract, and by how much: [4](#0-3) 

Any CosmWasm user can permissionlessly instantiate/upload a CW20-like contract and register an ERC20 pointer for it (`AddCW20`), then interact with it through the EVM ERC20 interface. If the underlying CW20 contract's query handler ever returns (or can be made to return, e.g. through migration, a bug, or adversarial contract logic) a negative numeric string for `balance`/`allowance`/`total_supply`, the JSON precompile silently turns it into an unsigned magnitude rather than rejecting it or preserving its sign, corrupting the value that `CW20ERC20Pointer` treats as an authoritative `uint256` balance/allowance for all downstream ERC20 consumers of that pointer.

### Impact Explanation
This meets the "unauthorized transfer via precompile or pointer" / "fund loss" bar conceptually the same way the Firewall report does: a value that should be rejected or sign-checked is instead coerced into an unsigned magnitude and used in a threshold-style computation (`allowance` comparisons in `approve()`, and any downstream consumer relying on `balanceOf`/`allowance`/`totalSupply` being non-negative `uint256`). Because `CW20ERC20Pointer` is the canonical bridge exposing arbitrary CW20 tokens to the EVM ecosystem, an incorrect (flipped-sign-to-positive) balance or allowance value reported through this precompile can desynchronize the EVM-side view of token accounting from the true CosmWasm-side state, enabling incorrect allowance adjustments or ERC20 consumers (DEXes, wallets) acting on a corrupted `uint256` value.

### Likelihood Explanation
Reaching this code requires only a CosmWasm-capable, unprivileged user to deploy a wasm contract whose query response includes a negative numeric field for one of the keys queried by the pointer (`balance`, `allowance`, `total_supply`, `decimals`) and register/point an ERC20 pointer at it — both of which are permissionless, single-transaction operations. The precompile itself performs no `Sign()`/non-negativity validation before treating the parsed value as a `uint256`, so the coercion is deterministic and always reachable whenever the queried field happens to be negative (whether by contract design, a bug in a real CW20-like contract, or migration state), not requiring any privileged actor.

### Recommendation
In `precompiles/json/json.go::ExtractAsUint256`, reject negative values explicitly (e.g., `if value.Sign() < 0 { return nil, fmt.Errorf(...) }`) before returning, rather than allowing `FillBytes` to silently take the absolute value. Document that `extractAsUint256` is only valid for genuinely unsigned JSON fields, and audit all consumers (e.g. `CW20ERC20Pointer.sol`) to ensure they cannot be fed attacker-influenced negative numeric strings without an explicit error.

### Proof of Concept
1. Deploy (or upgrade) a CosmWasm contract that implements the CW20 query interface but returns, e.g., `{"balance":"-100"}` for a `balance` query (trivially achievable by any wasm developer since the contract's query handler is fully under the deployer's control).
2. Register an ERC20 pointer for this contract via the `pointer` precompile's `AddCW20` method (permissionless).
3. Call `balanceOf(owner)` on the resulting `CW20ERC20Pointer` contract from the EVM side.
4. Trace the call: `WasmdPrecompile.query` returns `{"balance":"-100"}` → `JsonPrecompile.extractAsUint256(response, "balance")` parses `"-100"` into a negative `big.Int` (`SetString` succeeds) → `Execute` calls `uint_.FillBytes(byteArr)`, which encodes the **absolute value** `100` as the returned `uint256`.
5. Result: `balanceOf` (and identically `allowance`/`totalSupply`) returns `100` instead of erroring on the invalid negative value, silently corrupting the reported ERC20 state and any contract logic (e.g. `approve()`'s allowance-delta computation) built on top of it — the same "signed value silently reinterpreted as unsigned, bypassing intended validation" pattern as the reported Firewall vulnerability.

### Citations

**File:** precompiles/json/json.go (L88-98)
```go
	case ExtractAsUint256Method:
		var uint_ *big.Int
		if uint_, err = p.ExtractAsUint256(ctx, method, args, value); err == nil {
			if uint_.BitLen() > 256 {
				err = errors.New("value does not fit in 32 bytes")
			} else {
				byteArr := make([]byte, 32)
				uint_.FillBytes(byteArr)
				bz = byteArr
			}
		}
```

**File:** precompiles/json/json.go (L184-199)
```go

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
```

**File:** contracts/src/CW20ERC20Pointer.sol (L30-56)
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

    function allowance(address owner, address spender) public view override returns (uint256) {
        string memory o = _formatPayload("owner", _doubleQuotes(AddrPrecompile.getSeiAddr(owner)));
        string memory s = _formatPayload("spender", _doubleQuotes(AddrPrecompile.getSeiAddr(spender)));
        string memory req = _curlyBrace(_formatPayload("allowance", _curlyBrace(_join(o, s, ","))));
        bytes memory response = WasmdPrecompile.query(Cw20Address, bytes(req));
        return JsonPrecompile.extractAsUint256(response, "allowance");
    }
```

**File:** contracts/src/CW20ERC20Pointer.sol (L59-77)
```text
    function approve(address spender, uint256 amount) public override returns (bool) {
        // if amount is larger uint128 then set amount to uint128 max
        if (amount > type(uint128).max) {
            amount = type(uint128).max;
        }
        uint256 currentAllowance = allowance(msg.sender, spender);
        if (currentAllowance > amount) {
            string memory spenderAddr = _formatPayload("spender", _doubleQuotes(AddrPrecompile.getSeiAddr(spender)));
            string memory amt = _formatPayload("amount", _doubleQuotes(Strings.toString(currentAllowance - amount)));
            string memory req = _curlyBrace(_formatPayload("decrease_allowance", _curlyBrace(_join(spenderAddr, amt, ","))));
            _execute(bytes(req));
        } else if (currentAllowance < amount) {
            string memory spenderAddr = _formatPayload("spender", _doubleQuotes(AddrPrecompile.getSeiAddr(spender)));
            string memory amt = _formatPayload("amount", _doubleQuotes(Strings.toString(amount - currentAllowance)));
            string memory req = _curlyBrace(_formatPayload("increase_allowance", _curlyBrace(_join(spenderAddr, amt, ","))));
            _execute(bytes(req));
        }
        return true;
    }
```
