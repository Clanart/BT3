## Title
Pointer-precompile CW-type confusion: `AddCW721Pointer`/`AddCW1155Pointer` use an identical, non-discriminating validation query, allowing registration of a wrong-type ERC wrapper pointer for any CW contract - ([File: precompiles/pointer/pointer.go])

### Summary
The OtterSec report describes how Solana's execution model performs no runtime type checking of accounts, so an unprivileged caller can freely substitute an account of the wrong type into an instruction unless the program explicitly validates it. In sei-chain's EVM↔CosmWasm pointer precompile, `AddCW721Pointer` and `AddCW1155Pointer` are meant to validate that a caller-supplied CosmWasm address is actually a CW721/CW1155 contract before minting a permanently-registered ERC wrapper for it, but both use the exact same, non-type-specific smart query and accept whatever it returns.

### Finding Description
`PrecompileExecutor.AddCW721` and `PrecompileExecutor.AddCW1155` are reachable by any EVM caller (no signer/owner restriction, only non-payable + arg-length checks) through the pointer precompile at `0x...100b`: [1](#0-0) [2](#0-1) 

Both functions perform the *identical* validation step — querying `{"contract_info":{}}` on the target CosmWasm address and extracting only `name`/`symbol` from the JSON response — before calling `UpsertERCCW721Pointer` / `UpsertERCCW1155Pointer` respectively: [3](#0-2) [4](#0-3) 

Nothing in this code path distinguishes a CW721 contract from a CW1155 contract, or even validates that the target implements either spec beyond returning a JSON object with `name` and `symbol` keys — which is trivial for any attacker-deployed CosmWasm contract to satisfy. This is a direct analog of the "no execution-level typing" issue described in the report: the precompile has no way to enforce that the account passed in is of the expected type, and relies entirely on unauthenticated, attacker-influenced query output for that determination.

Downstream, the wasmd precompile's `execute()` path enforces pointer↔callingContract binding purely by address equality against whichever CW type table (`GetERC20CW20Pointer`/`GetERC721CW721Pointer`/`GetERC1155CW1155Pointer`) happens to match: [5](#0-4) 

Because `AddCW721`/`AddCW1155` do not verify the actual CW spec implemented by the target contract, an attacker can register, for example, a CW1155-typed pointer against a real CW721 NFT contract (or any other CW contract that returns a `name`/`symbol` JSON blob), or vice versa. Once registered, the generated ERC1155-template wrapper contract will thereafter construct and send CW1155-shaped execute/query messages (e.g., token-id + amount based transfer/balance messages) against a contract that actually implements the CW721 schema (owner-based, non-fungible), and the reverse pointer registry (`PointerReverseRegistryKey`) and the `execute()` pointer-authorization check will treat the mismatched wrapper as an authorized delegatecaller for that address.

### Impact Explanation
This breaks the type-safety invariant the pointer system depends on to keep ERC wrapper contracts semantically aligned with the CosmWasm contracts they represent. Because the registration is permanent (`ErrorPointerToPointerNotAllowed`/existing-pointer checks only guard against re-registering a *pointer* to a pointer, not against wrong-type registration) and callable by any address with no privilege, a malicious actor can force a legitimate CW721/CW1155 asset contract to be wrapped by a wrapper of the wrong interface. Any subsequent unauthorized/mismatched execute-message construction against the underlying contract is a low-level building block for unauthorized-transfer style attacks and for user/dApp confusion that misrepresents token supply/ownership across the EVM/CW bridge.

### Likelihood Explanation
High: `addCW721Pointer`/`addCW1155Pointer` are permissionless EVM calls with only trivial argument validation (non-payable, arg length), reachable by any transaction sender, and the discriminating check (`contract_info` query) is satisfiable by any attacker-controlled or even legitimate but wrong-type CW contract.

### Recommendation
`AddCW721` and `AddCW1155` should query type-specific, spec-mandated endpoints that are mutually exclusive between CW721 and CW1155 (e.g., attempt an NFT-specific query such as `num_tokens`/`all_tokens` for CW721, and a CW1155-specific balance/is-approved query for CW1155) and reject registration if the target does not respond according to the expected interface, rather than relying solely on a generic `name`/`symbol` query shared by both specs.

### Proof of Concept
1. Attacker deploys (or identifies) an existing CosmWasm contract `C` that responds to `{"contract_info":{}}"` with `{"name":"X","symbol":"Y", ...}` — trivially true for any real CW721 or CW1155 contract, or a purpose-built malicious contract.
2. Attacker calls `addCW1155Pointer(C)` on the pointer precompile even though `C` is a real CW721 NFT contract (or `addCW721Pointer` against a real CW1155 contract).
3. `PrecompileExecutor.AddCW1155` (precompiles/pointer/pointer.go:198-228) performs no CW1155-specific validation, succeeds, and calls `UpsertERCCW1155Pointer`, permanently registering a CW1155-typed ERC wrapper against the CW721 contract's address.
4. Subsequent calls through the wasmd `execute()` precompile pass the pointer-authorization check (precompiles/wasmd/wasmd.go:224-233) because the registered `ERC1155CW1155Pointer` entry now matches the calling wrapper contract, despite the underlying contract being a different asset-type schema.

### Citations

**File:** precompiles/pointer/pointer.go (L166-196)
```go
func (p PrecompileExecutor) AddCW721(ctx sdk.Context, method *ethabi.Method, caller common.Address, args []interface{}, value *big.Int, evm *vm.EVM, hooks *tracing.Hooks) (ret []byte, remainingGas uint64, err error) {
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
	res, err := p.wasmdKeeper.QuerySmartSafe(ctx, cwAddress, []byte("{\"contract_info\":{}}"))
	if err != nil {
		return nil, 0, err
	}
	formattedRes := map[string]interface{}{}
	if err := json.Unmarshal(res, &formattedRes); err != nil {
		return nil, 0, err
	}
	name := formattedRes["name"].(string)
	symbol := formattedRes["symbol"].(string)
	contractAddr, err := p.evmKeeper.UpsertERCCW721Pointer(ctx, evm, cwAddr, utils.ERCMetadata{Name: name, Symbol: symbol})
	if err != nil {
		return nil, 0, err
	}

	ret, err = method.Outputs.Pack(contractAddr)
	remainingGas = pcommon.GetRemainingGas(ctx, p.evmKeeper)
	return
}
```

**File:** precompiles/pointer/pointer.go (L198-228)
```go
func (p PrecompileExecutor) AddCW1155(ctx sdk.Context, method *ethabi.Method, caller common.Address, args []interface{}, value *big.Int, evm *vm.EVM, hooks *tracing.Hooks) (ret []byte, remainingGas uint64, err error) {
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
	res, err := p.wasmdKeeper.QuerySmartSafe(ctx, cwAddress, []byte("{\"contract_info\":{}}"))
	if err != nil {
		return nil, 0, err
	}
	formattedRes := map[string]interface{}{}
	if err := json.Unmarshal(res, &formattedRes); err != nil {
		return nil, 0, err
	}
	name := formattedRes["name"].(string)
	symbol := formattedRes["symbol"].(string)
	contractAddr, err := p.evmKeeper.UpsertERCCW1155Pointer(ctx, evm, cwAddr, utils.ERCMetadata{Name: name, Symbol: symbol})
	if err != nil {
		return nil, 0, err
	}

	ret, err = method.Outputs.Pack(contractAddr)
	remainingGas = pcommon.GetRemainingGas(ctx, p.evmKeeper)
	return
}
```

**File:** precompiles/wasmd/wasmd.go (L224-233)
```go
	// type assertion will always succeed because it's already validated in p.Prepare call in Run()
	contractAddrStr := args[0].(string)
	if ctx.EVMPrecompileCalledFromDelegateCall() {
		erc20pointer, _, erc20exists := p.evmKeeper.GetERC20CW20Pointer(ctx, contractAddrStr)
		erc721pointer, _, erc721exists := p.evmKeeper.GetERC721CW721Pointer(ctx, contractAddrStr)
		erc1155pointer, _, erc1155exists := p.evmKeeper.GetERC1155CW1155Pointer(ctx, contractAddrStr)
		if (!erc20exists || erc20pointer.Cmp(callingContract) != 0) && (!erc721exists || erc721pointer.Cmp(callingContract) != 0) && (!erc1155exists || erc1155pointer.Cmp(callingContract) != 0) {
			return nil, 0, fmt.Errorf("%s is not a pointer of %s", callingContract.Hex(), contractAddrStr)
		}
	}
```
