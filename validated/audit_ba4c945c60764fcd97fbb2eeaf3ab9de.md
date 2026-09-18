### Title
CW20/CW721/CW1155 pointer contracts permit unrestricted, attacker-chosen `name`/`symbol` metadata enabling token identity spoofing - (File: precompiles/pointer/pointer.go)

### Summary
The `pointer` precompile's `AddCW20`, `AddCW721`, and `AddCW1155` methods let *any* caller deploy an ERC20/ERC721/ERC1155 pointer contract for an arbitrary CosmWasm contract, using the `name`/`symbol` values returned by that CW contract's own `token_info`/`contract_info` query with zero validation, uniqueness checking, or comparison against already-registered pointers.

### Finding Description
`AddCW20` reads `name`/`symbol` straight from the target CW20 contract's `token_info` query response and passes them unmodified into `UpsertERCCW20Pointer`, which mints an EVM-visible ERC20 pointer contract carrying that branding: [1](#0-0) 

The same pattern is repeated for CW721 (`contract_info`) and CW1155: [2](#0-1) 

Registration of a pointer is keyed only by the CosmWasm contract address (`cwAddr`), via `UpsertERCCW20Pointer`/`UpsertERCCW721Pointer`/`UpsertERCCW1155Pointer` in `x/evm/keeper/pointer.go`, not by `name`/`symbol`. Since anyone can permissionlessly instantiate a new CW20/CW721/CW1155 contract (CosmWasm instantiation is unpermissioned) and set its `name`/`symbol` fields to match any well-known token (e.g., "USD Coin"/"USDC", or the name/symbol of an already-registered legitimate pointer), an attacker can then call `addCW20Pointer`/`addCW721Pointer`/`addCW1155Pointer` to mint a brand-new EVM contract that is visually indistinguishable — in wallets, block explorers, and dApps that read `name()`/`symbol()` — from the real pointer for the legitimate token, while pointing at a completely different, attacker-controlled underlying CW20/CW721/CW1155 contract and address.

This is a direct analog of the referenced CVE's "insufficient policy enforcement... allowed a remote attacker to perform domain spoofing via a crafted domain name": here, the "domain" is the on-chain token identity (name/symbol) shown to EVM users, and the "insufficient policy" is the complete absence of any uniqueness/authenticity check when the identity claims of a CW20/CW721/CW1155 contract are copied into a newly minted, publicly reachable ERC-standard pointer contract.

### Impact Explanation
A user or integrator (wallet, DEX, bridge UI, aggregator) that identifies a token by its `name`/`symbol` rather than its contract address can be tricked into approving/transferring/depositing into the attacker's spoofed pointer contract instead of the legitimate one. Because CW20/CW721/CW1155 admin logic is entirely attacker-controlled in the spoofed contract, this results in concrete fund loss: users sending funds or granting approvals believing they are interacting with the well-known token end up interacting with an attacker-controlled contract, and those funds/NFTs can be drained or never delivered as expected.

### Likelihood Explanation
The attack requires only: (1) a permissionless CW20/CW721/CW1155 instantiate call with attacker-chosen `name`/`symbol` metadata, and (2) a single subsequent EVM precompile call (`addCW20Pointer`/`addCW721Pointer`/`addCW1155Pointer`) from any address — both fully reachable by an unprivileged transaction sender/contract deployer with no special permissions, gas cost beyond normal contract deployment, or governance approval. No race condition or privileged access is needed since the check is only against the `cwAddr`, not the identity strings.

### Recommendation
Add validation and/or a warning surface at pointer-creation time: (1) reject/flag pointer registrations whose `name`/`symbol` collide with an already-registered pointer's `name`/`symbol` (case-insensitively, and with basic homoglyph/whitespace normalization), (2) expose in the pointer registry/query API which pointer is canonical for a given `name`/`symbol` combination if collisions are allowed, and (3) emit a distinguishing event/attribute (e.g., "duplicate-name pointer") so downstream indexers and wallets can flag likely spoofed tokens.

### Proof of Concept
1. Attacker instantiates a new CW20 contract whose `token_info` query returns `name: "USD Coin"`, `symbol: "USDC"` (identical to the real bridged USDC's CW20 pointer's metadata).
2. Attacker calls the `pointer` precompile's `addCW20Pointer(cwAddr)` with their own CW20 address.
3. `AddCW20` in `precompiles/pointer/pointer.go` queries `token_info`, extracts `name`/`symbol` unmodified, and calls `UpsertERCCW20Pointer`, which deploys a brand-new ERC20 contract with `name()=="USD Coin"`, `symbol()=="USDC"` at a new EVM address distinct from the real USDC pointer.
4. A user/dApp querying tokens by symbol/name (rather than pinned contract address) can be shown or select the attacker's pointer, then approve/transfer funds to it, resulting in fund loss to the attacker's CW20 contract logic.

### Citations

**File:** precompiles/pointer/pointer.go (L146-163)
```go
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
```

**File:** precompiles/pointer/pointer.go (L166-228)
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
