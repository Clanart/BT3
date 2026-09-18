### Title
Unrestricted characters in tokenfactory/CW20 token Name and Symbol metadata enable display-spoofing of ERC20 pointer contracts - (File: `precompiles/pointer/pointer.go`, `sei-cosmos/x/bank/types/metadata.go`)

### Summary
The pointer precompile (`AddNativePointer`/`AddCW20Pointer`) copies a token's `Name`/`Symbol` verbatim from either bank denom metadata or a CW20 contract's `token_info` query response into the constructor arguments of a newly deployed ERC20 pointer contract, without restricting the character set. Because `Metadata.Validate()` only checks that `Name`/`Symbol` are non-blank and never restricts their content, an attacker fully controls these strings — including ANSI escape sequences, control characters, and bidirectional/RTL Unicode override characters — that will subsequently be returned by the pointer's `name()`/`symbol()` functions and displayed by wallets, block explorers, and CLI tooling.

### Finding Description
`Metadata.Validate()` in `sei-cosmos/x/bank/types/metadata.go:18-25` (and the duplicate in `giga/deps/xbank/types/metadata.go:18-25`) only enforces: [1](#0-0) 
```
func (m Metadata) Validate() error {
	if strings.TrimSpace(m.Name) == "" {
		return errors.New("name field cannot be blank")
	}
	if strings.TrimSpace(m.Symbol) == "" {
		return errors.New("symbol field cannot be blank")
	}
```
No restriction is placed on the character set of `Name`/`Symbol` — unlike `subdenom`/`Base`/`Display`, which go through `sdk.ValidateDenom` (a strict alphanumeric charset). This metadata is settable permissionlessly via `MsgSetDenomMetadata`, whose `ValidateBasic` only calls `m.Metadata.Validate()` before checking the denom is tokenfactory-owned: [2](#0-1) 

Separately, for CW20 tokens (fully permissionless to instantiate via CosmWasm), the pointer precompile takes `name`/`symbol` directly from the contract's own `token_info` smart query response with zero validation: [3](#0-2) 
```
res, err := p.wasmdKeeper.QuerySmartSafe(ctx, cwAddress, []byte("{\"token_info\":{}}"))
...
name := formattedRes["name"].(string)
symbol := formattedRes["symbol"].(string)
contractAddr, err := p.evmKeeper.UpsertERCCW20Pointer(ctx, evm, cwAddr, utils.ERCMetadata{Name: name, Symbol: symbol})
```
Similarly, `AddNative` copies `metadata.Name`/`metadata.Symbol` from bank denom metadata directly into the pointer's constructor arguments: [4](#0-3) 

The `AddCW20Pointer` and `AddNativePointer` methods are callable by any unprivileged EOA or contract (no admin/permission check on the precompile call itself), and CW20 contract instantiation and tokenfactory denom creation with metadata are both fully permissionless actions.

### Impact Explanation
This is directly analogous to CVE-2012-3867: an unprivileged, user-controlled identity/name field is not restricted from containing control characters, which is then relied upon by downstream tooling (here: wallets, MetaMask-style popups, block explorers, and `seid`/JSON-RPC CLI output rendering `name()`/`symbol()`) to represent the asset to a human. An attacker can craft a CW20 contract or tokenfactory denom whose `Name`/`Symbol` embeds ANSI terminal escape sequences (to overwrite/hide terminal output) or Unicode bidi-override characters (to visually spoof a well-known token's ticker, e.g., render as "USDC" while the on-chain bytes differ), then register it as a pointer ERC20 via `addCW20Pointer`/`addNativePointer`. A victim relying on the spoofed `name()`/`symbol()` display can be tricked into approving or sending funds to this malicious pointer's underlying denom/contract believing it is a legitimate, trusted asset, resulting in unauthorized transfer of funds via the pointer mechanism.

### Likelihood Explanation
Both preconditions are trivially reachable by any unprivileged actor with no special permission: (1) CW20 contract instantiation and tokenfactory `MsgCreateDenom`/`MsgSetDenomMetadata` are open to any account, and (2) `addCW20Pointer`/`addNativePointer` precompile calls are unrestricted. Crafting a control-character or bidi-override payload requires no special access — only standard tx submission.

### Recommendation
Extend `Metadata.Validate()` (and equivalent CW20 `token_info` ingestion in the pointer precompiles) to reject `Name`/`Symbol`/`Description` values containing non-printable ASCII control characters (0x00–0x1F, 0x7F), ANSI escape sequences (ESC, 0x1B), and Unicode bidirectional control characters (e.g., U+202A–U+202E, U+2066–U+2069). Apply this sanitation both when metadata is set on-chain (`MsgSetDenomMetadata`) and when copying values sourced from an external CW20 contract into pointer bytecode in `precompiles/pointer/pointer.go` and its legacy versions.

### Proof of Concept
1. Instantiate a CW20 contract (or use `MsgSetDenomMetadata` on a tokenfactory denom) whose `token_info.name`/`symbol` contains an ANSI escape sequence or Unicode RTL-override character sequence designed to visually spoof a well-known ticker (e.g., embed `\u202E` to reverse-render characters, making a malicious symbol display as "USDC").
2. Call `pointer.addCW20Pointer(cwAddr)` (or `addNativePointer(denom)`) from any account; the precompile deploys an ERC20 pointer contract whose constructor is packed with the unsanitized name/symbol, as shown in `precompiles/pointer/pointer.go:140-159`.
3. Query `name()`/`symbol()` on the deployed pointer via `eth_call`; verify the returned string still contains the raw control/override characters, which wallets/explorers will render in spoofed form, misleading a user into trusting/approving the wrong asset.

### Citations

**File:** sei-cosmos/x/bank/types/metadata.go (L18-25)
```go
func (m Metadata) Validate() error {
	if strings.TrimSpace(m.Name) == "" {
		return errors.New("name field cannot be blank")
	}

	if strings.TrimSpace(m.Symbol) == "" {
		return errors.New("symbol field cannot be blank")
	}
```

**File:** x/tokenfactory/types/msgs.go (L211-228)
```go
func (m MsgSetDenomMetadata) ValidateBasic() error {
	_, err := sdk.AccAddressFromBech32(m.Sender)
	if err != nil {
		return sdkerrors.Wrapf(sdkerrors.ErrInvalidAddress, "Invalid sender address (%s)", err)
	}

	err = m.Metadata.Validate()
	if err != nil {
		return err
	}

	_, _, err = DeconstructDenom(m.Metadata.Base)
	if err != nil {
		return err
	}

	return nil
}
```

**File:** precompiles/pointer/pointer.go (L140-159)
```go
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
```

**File:** precompiles/pointer/legacy/v605/pointer.go (L106-124)
```go
	token := args[0].(string)
	metadata, metadataExists := p.bankKeeper.GetDenomMetaData(ctx, token)
	if !metadataExists {
		return nil, 0, fmt.Errorf("denom %s does not have metadata stored and thus can only have its pointer set through gov proposal", token)
	}
	name := metadata.Name
	symbol := metadata.Symbol
	var decimals uint8
	for _, denomUnit := range metadata.DenomUnits {
		if denomUnit.Exponent > uint32(decimals) && denomUnit.Exponent <= math.MaxUint8 {
			decimals = uint8(denomUnit.Exponent)
			name = denomUnit.Denom
			symbol = denomUnit.Denom
			if len(denomUnit.Aliases) > 0 {
				name = denomUnit.Aliases[0]
			}
		}
	}
	contractAddr, err := p.evmKeeper.UpsertERCNativePointer(ctx, evm, token, utils.ERCMetadata{Name: name, Symbol: symbol, Decimals: decimals})
```
