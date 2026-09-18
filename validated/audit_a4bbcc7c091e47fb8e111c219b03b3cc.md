### Title
Tokenfactory denom metadata `Name`/`Symbol` are not neutralized for leading/trailing whitespace, enabling visually-indistinguishable spoofed tokens - ([File: sei-cosmos/x/bank/types/metadata.go])

### Summary
`Metadata.Validate()`, used to validate a tokenfactory denom's bank metadata, only checks that `Name` and `Symbol` are not blank after trimming — it never rejects or strips leading/trailing whitespace itself. Because any account can permissionlessly create a tokenfactory denom and, as its admin, set this metadata, an attacker can register a token whose `Name`/`Symbol` is identical to a legitimate token except for invisible padding (e.g. `"USDC "` vs `"USDC"`). This metadata is later copied verbatim into the ERC20 pointer contract's `name()`/`symbol()` fields and into the bank precompile's `name`/`symbol` view functions, so wallets, block explorers, and EVM dApps display a value that is indistinguishable from the genuine token.

### Finding Description
`Metadata.Validate()` in [1](#0-0)  only rejects names/symbols that are blank after `strings.TrimSpace`; it does not reject strings that merely contain leading/trailing whitespace.

This validation is invoked as "defense in depth" by `MsgServer.SetDenomMetadata`, which is reachable by any account that is the admin of a tokenfactory denom (by default the denom's creator, itself permissionless via `MsgCreateDenom`): [2](#0-1) 

Because tokenfactory denom creation is fully permissionless (`x/tokenfactory/README.md`: "allows any account to create a new token... permissionless"), an attacker can:
1. Create `factory/{attacker}/usdc` via `MsgCreateDenom`.
2. Call `MsgSetDenomMetadata` with `Name = "USDC "` / `Symbol = "USDC "` (or with an internal invisible unicode/whitespace variant that passes `TrimSpace` non-blank check) — this passes `Metadata.Validate()` unmodified.

This tainted `Name`/`Symbol` is then propagated to:
- The EVM `pointer` precompile's `AddNative`, which reads `metadata.Name`/`metadata.Symbol` directly and uses them as ERC20 constructor arguments for the deployed pointer contract, e.g. [3](#0-2) 
- The `bank` precompile's `name`/`symbol` view functions, which return `metadata.Name`/`metadata.Symbol` unmodified to any EVM caller: [4](#0-3) 

The integration test suite confirms that a tokenfactory denom's metadata `Name`/`Symbol` become the ERC20 pointer's `name()`/`symbol()` verbatim: [5](#0-4) 

### Impact Explanation
An attacker can deploy an ERC20 pointer contract (or expose a native-token metadata query) whose displayed `name`/`symbol` is visually indistinguishable from a legitimate, high-value token (e.g. `factory/attacker/usdc` displaying as `"USDC "`). Wallets, DEX front-ends, and block explorers that key off `name()`/`symbol()` for display (rather than contract address) can mislead users into approving, sending, or swapping into the attacker's spoofed token, believing it is the real one — a direct vector for fund loss via token-spoofing/phishing, matching the "unauthorized transfer via precompile/pointer" and "concrete fund loss" impact categories.

### Likelihood Explanation
Likelihood is Medium: creating a tokenfactory denom and calling `SetDenomMetadata` on it are unprivileged operations any wallet can perform in a couple of transactions, and deploying the corresponding ERC20 pointer via `addNativePointer` is also permissionless. The primary limiting factor is that end-users/tooling must be tricked into trusting the display name, which requires an active phishing/social-engineering component, consistent with the CVSS UI:N/PR:L but user-confusion nature of the original report.

### Recommendation
In `Metadata.Validate()` (`sei-cosmos/x/bank/types/metadata.go`), reject `Name`/`Symbol`/alias values that differ from their `strings.TrimSpace()` form (i.e., require `m.Name == strings.TrimSpace(m.Name)`), rather than only checking for blankness. Apply the same normalization/rejection when denom metadata is consumed by the `pointer` and `bank` precompiles as an additional defense-in-depth layer, and consider normalizing Unicode confusables in `Name`/`Symbol` more broadly.

### Proof of Concept
1. Attacker sends `MsgCreateDenom{Subdenom: "usdc"}` → creates `factory/{attacker}/usdc`, attacker becomes admin.
2. Attacker sends `MsgSetDenomMetadata{Metadata: {Base: "factory/{attacker}/usdc", Name: "USDC ", Symbol: "USDC ", DenomUnits: [...]}}` — passes `Metadata.Validate()` since `TrimSpace("USDC ") != ""`.
3. Attacker (or anyone) calls the `pointer` precompile `addNativePointer("factory/{attacker}/usdc")`, deploying an ERC20 whose `name()`/`symbol()` return `"USDC "`.
4. A wallet/dApp displaying `name()`/`symbol()` shows a token indistinguishable from the real `USDC` pointer, and a victim is tricked into interacting with the attacker's token/contract instead.

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

**File:** x/tokenfactory/keeper/msg_server.go (L188-206)
```go
func (server msgServer) SetDenomMetadata(goCtx context.Context, msg *types.MsgSetDenomMetadata) (*types.MsgSetDenomMetadataResponse, error) {
	ctx := sdk.UnwrapSDKContext(goCtx)

	// Defense in depth validation of metadata
	err := msg.Metadata.Validate()
	if err != nil {
		return nil, err
	}

	authorityMetadata, err := server.GetAuthorityMetadata(ctx, msg.Metadata.Base)
	if err != nil {
		return nil, err
	}

	if msg.Sender != authorityMetadata.GetAdmin() {
		return nil, types.ErrUnauthorized
	}

	server.bankKeeper.SetDenomMetaData(ctx, msg.Metadata)
```

**File:** precompiles/pointer/pointer.go (L106-124)
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

**File:** precompiles/bank/legacy/v620/bank.go (L299-315)
```go
func (p PrecompileExecutor) name(ctx sdk.Context, method *abi.Method, args []interface{}, value *big.Int) ([]byte, uint64, error) {
	if err := pcommon.ValidateNonPayable(value); err != nil {
		return nil, 0, err
	}

	if err := pcommon.ValidateArgsLength(args, 1); err != nil {
		return nil, 0, err
	}

	denom := args[0].(string)
	metadata, found := p.bankKeeper.GetDenomMetaData(ctx, denom)
	if !found {
		return nil, 0, fmt.Errorf("denom %s not found", denom)
	}
	bz, err := method.Outputs.Pack(metadata.Name)
	return bz, pcommon.GetRemainingGas(ctx, p.evmKeeper), err
}
```

**File:** integration_test/precompile_tests/precompiles/pointer.spec.ts (L84-91)
```typescript
        it('the deployed pointer is a live ERC20 whose metadata mirrors the denom', async () => {
            const erc20 = new ethers.Contract(pointerAddress, ERC20_ABI, provider);
            // Tokenfactory metadata: name/symbol are the FULL factory/… denom
            // string and the single denom unit has exponent 0.
            expect(await erc20.name()).to.equal(denom);
            expect(await erc20.symbol()).to.equal(denom);
            expect(await erc20.decimals()).to.equal(0n);
        });
```
