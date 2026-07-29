### Title
Unbounded ERC20 `name`/`symbol` metadata stored via permissionless `RegisterERC20`, then read with fixed-gas precompile queries — enables gas-underpriced, unbounded-cost `Name`/`Symbol` DOS - ([File: precompiles/erc20/erc20.go])

### Summary
`MsgRegisterERC20` can be submitted permissionlessly (when `PermissionlessRegistration` is enabled) by any account for any ERC20 contract. During registration, the module reads `name()`/`symbol()` directly from the target contract and stores them as bank denom metadata, but only `Name` is length-capped via `SanitizeERC20Name` (128 chars) — `Symbol` is stored completely unsanitized and unbounded. The ERC20 precompile then serves `Name()`/`Symbol()` queries with a **fixed** `RequiredGas` (`GasName = 3,421`, `GasSymbol = 3,464`), regardless of the actual size of the returned string. This is the same bug class as the Hats `uri` issue: attacker-controlled unbounded string data is priced at a flat, size-independent gas cost when read.

### Finding Description
- `k.RegisterERC20` explicitly documents itself as permissionless: [1](#0-0) 
- `CreateCoinMetadata` builds the bank `Metadata.Symbol` directly from `erc20Data.Symbol` (the ERC20 contract's raw `symbol()` return value) with **no sanitization or length limit**, whereas `Name` is capped through `SanitizeERC20Name`: [2](#0-1) 
- `SanitizeERC20Name` caps at 128 characters, but is only applied to the `Name`/`Display` fields, never `Symbol`: [3](#0-2) 
- A malicious deployer can write an ERC20 contract whose `symbol()` returns a very large string (multi-hundred-KB to low-MB range is achievable within a single call's EVM memory-expansion gas budget). Registering that contract stores this oversized string permanently in bank module state via `k.bankKeeper.SetDenomMetaData(ctx, metadata)`.
- Every subsequent call to the corresponding ERC20 precompile's `Symbol()` (and similarly `Name()`, capped only to 128 chars but still non-trivial) is charged a **flat** gas cost independent of string size: `GasSymbol = 3,464`, `GasName = 3,421`: [4](#0-3) [5](#0-4) 
- The actual query implementation returns the full stored metadata string via ABI packing regardless of size: [6](#0-5) 

Because these queries execute deterministically inside EVM transaction processing (`Execute`/`HandleMethod`) during ordinary `DeliverTx`, every validator performs the same expensive ABI-encoding/memory work for a gas cost that does not scale with the payload. This decouples metered gas from actual computational/state-read cost on a consensus-critical execution path.

### Impact Explanation
This does not directly enable unauthorized minting, burning, or fund theft. Its impact is a gas-mispricing/computational-DoS vector: an attacker can register a token pair whose `Symbol()`/`Name()` value is arbitrarily large, then have any other user (or a contract that calls into this precompile, e.g. via `transferFrom`/wrapping flows or off-chain automation) trigger unbounded memory-copy/ABI-encoding work for a nearly-zero, flat gas charge. If exploited at scale (many large-metadata token pairs, called repeatedly within blocks), this degrades block-processing time for all validators identically — a shared-fate/available-liveness risk rather than a state-corruption or fund-theft bug. I could not fully confirm, given the available search surface, the exact maximum achievable `symbol()` string size in this codebase's EVM parameters (e.g., custom gas schedule, code/output size caps), nor whether bank's `Metadata.Validate()` (from the imported cosmos-sdk dependency) already enforces a field-length bound that would preclude this — that logic lives in the `cosmos-sdk` dependency and was not available in the indexed contents for verification.

### Likelihood Explanation
Likelihood is moderate: it requires (1) `PermissionlessRegistration` param to be enabled (a governance/deployment configuration choice — if disabled, only governance-authorized addresses can register, reducing this to a privileged-actor issue and taking it out of scope) and (2) the attacker deploying and registering a purpose-built malicious ERC20 contract, both of which are low-cost, ordinary user actions with no special privilege beyond what any address is permitted to do. I could not verify from the available code whether `PermissionlessRegistration` defaults to `true` or `false` in production deployments of this repo — that would materially affect exploitability and should be checked directly in the chain's genesis/params configuration.

### Recommendation
- Apply `SanitizeERC20Name`-style truncation (or an equivalent, explicit max-length check, e.g. matching the bank module's typical symbol/display conventions) to `erc20Data.Symbol` before constructing `Metadata` in `CreateCoinMetadata` (`x/erc20/keeper/proposals.go`).
- Consider capping `Name` and `Symbol` return values read from the target contract at query time (`QueryERC20`) rather than only at registration, to also protect any legacy/ungoverned data.
- Make `RequiredGas` for `NameMethod`/`SymbolMethod` scale with the actual stored string length (or enforce a hard length cap validated at registration and never exceeded thereafter), so gas charged tracks real computational cost, consistent with the recommended fix pattern in the referenced Hats Protocol report (fixed length caps for user-controlled string fields).

### Proof of Concept
1. Deploy a malicious ERC20 contract whose `symbol()` function returns a string of the maximum size affordable within a single EVM call's gas budget (bounded chiefly by EVM memory-expansion cost, which permits on the order of 10^5–10^6 bytes within a ~30M gas call).
2. Submit `MsgRegisterERC20` for this contract's address (permissionless registration path in `x/erc20/keeper/msg_server.go`, `RegisterERC20`).
3. This invokes `CreateCoinMetadata` (`x/erc20/keeper/proposals.go`), which stores `Metadata.Symbol = erc20Data.Symbol` unbounded into the bank module via `SetDenomMetaData`.
4. Any subsequent caller (directly, or induced via a contract integration) invokes the ERC20 precompile's `Symbol()` method for this token pair. The call is charged the fixed `GasSymbol = 3,464` (`precompiles/erc20/erc20.go`), while `Symbol` (`precompiles/erc20/query.go`) reads and ABI-packs the full oversized string from bank metadata, consuming CPU/memory disproportionate to gas paid.
5. Repeating this call across many transactions in a block imposes disproportionate processing cost on every validator relative to the gas fees collected, for a metered cost that never reflects the true resource usage.

### Citations

**File:** x/erc20/keeper/msg_server.go (L324-335)
```go
// RegisterERC20 implements the gRPC MsgServer interface. Any account can permissionlessly
// register a native ERC20 contract to map to a Cosmos Coin.
func (k *Keeper) RegisterERC20(goCtx context.Context, req *types.MsgRegisterERC20) (*types.MsgRegisterERC20Response, error) {
	ctx := sdk.UnwrapSDKContext(goCtx)

	params := k.GetParams(ctx)

	if !params.PermissionlessRegistration {
		if err := k.validateAuthority(req.Signer); err != nil {
			return nil, err
		}
	}
```

**File:** x/erc20/keeper/proposals.go (L77-103)
```go
	metadata := banktypes.Metadata{
		Description: types.CreateDenomDescription(strContract),
		Base:        base,
		// NOTE: Denom units MUST be increasing
		DenomUnits: []*banktypes.DenomUnit{
			{
				Denom:    base,
				Exponent: 0,
			},
		},
		Name:    types.CreateDenom(strContract),
		Symbol:  erc20Data.Symbol,
		Display: base,
	}

	// only append metadata if decimals > 0, otherwise validation fails
	if erc20Data.Decimals > 0 {
		nameSanitized := types.SanitizeERC20Name(erc20Data.Name)
		metadata.DenomUnits = append(
			metadata.DenomUnits,
			&banktypes.DenomUnit{
				Denom:    nameSanitized,
				Exponent: uint32(erc20Data.Decimals), //#nosec G115 -- int overflow is not a concern here
			},
		)
		metadata.Display = nameSanitized
	}
```

**File:** x/erc20/types/utils.go (L46-56)
```go
// SanitizeERC20Name enforces 128 max string length, deletes leading numbers
// removes special characters  (except /)  and spaces from the ERC20 name
func SanitizeERC20Name(name string) string {
	name = removeLeadingNumbers(name)
	name = removeSpecialChars(name)
	if len(name) > 128 {
		name = name[:128]
	}
	name = removeInvalidPrefixes(name)
	return name
}
```

**File:** precompiles/erc20/erc20.go (L30-39)
```go
	GasTransfer     = 9_000
	GasTransferFrom = 30_500
	GasApprove      = 8_100
	GasName         = 3_421
	GasSymbol       = 3_464
	GasDecimals     = 427
	GasTotalSupply  = 2_480
	GasBalanceOf    = 2_870
	GasAllowance    = 3_225
)
```

**File:** precompiles/erc20/erc20.go (L100-140)
```go
// RequiredGas calculates the contract gas used for the
func (p Precompile) RequiredGas(input []byte) uint64 {
	// NOTE: This check avoid panicking when trying to decode the method ID
	if len(input) < 4 {
		return 0
	}

	methodID := input[:4]
	method, err := p.MethodById(methodID)
	if err != nil {
		return 0
	}

	// TODO: these values were obtained from Remix using the ERC20.sol from OpenZeppelin.
	// We should execute the transactions using the ERC20MinterBurnerDecimals.sol from Cosmos EVM testnet
	// to ensure parity in the values.
	switch method.Name {
	// ERC-20 transactions
	case TransferMethod:
		return GasTransfer
	case TransferFromMethod:
		return GasTransferFrom
	case ApproveMethod:
		return GasApprove
	// ERC-20 queries
	case NameMethod:
		return GasName
	case SymbolMethod:
		return GasSymbol
	case DecimalsMethod:
		return GasDecimals
	case TotalSupplyMethod:
		return GasTotalSupply
	case BalanceOfMethod:
		return GasBalanceOf
	case AllowanceMethod:
		return GasAllowance
	default:
		return 0
	}
}
```

**File:** precompiles/erc20/query.go (L62-84)
```go
// Symbol returns the symbol of the token. If the token metadata is registered in the
// bank module, it returns its symbol. Otherwise, it returns the base denomination of
// the token in uppercase (e.g. uatom -> ATOM).
func (p Precompile) Symbol(
	ctx sdk.Context,
	_ *vm.Contract,
	_ vm.StateDB,
	method *abi.Method,
	_ []interface{},
) ([]byte, error) {
	metadata, found := p.BankKeeper.GetDenomMetaData(ctx, p.tokenPair.Denom)
	if found {
		return method.Outputs.Pack(metadata.Symbol)
	}

	baseDenom, err := p.getBaseDenomFromIBCVoucher(ctx, p.tokenPair.Denom)
	if err != nil {
		return nil, ConvertErrToERC20Error(err)
	}

	symbol := strings.ToUpper(baseDenom[1:])
	return method.Outputs.Pack(symbol)
}
```
