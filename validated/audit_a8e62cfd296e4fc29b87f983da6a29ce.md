Based on my investigation, the concrete analog is that `x/tokenfactory` lets any account create a denom and then set arbitrary bank `Metadata` (`Name`, `Symbol`, `Display`) for it, with no uniqueness check against existing tokens' names/symbols/display — the only validations enforced are basic field non-blankness and internal denom-unit consistency.

### Title
Unauthenticated token-metadata spoofing lets any tokenfactory creator impersonate a well-known token (e.g. `SEI`/`usei`) - (File: x/tokenfactory/keeper/msg_server.go)

### Summary
`MsgCreateDenom` lets any account mint an arbitrarily-supplied `factory/{creator}/{subdenom}` denom, and `MsgSetDenomMetadata` lets that same creator (as the denom's admin) attach arbitrary `Name`/`Symbol`/`Display` bank metadata to it, without checking whether that display/symbol collides with an existing, legitimate token (e.g. native `usei`/`SEI`, or a bridged asset). This is the same bug class as BitDice's "fake EOS": the attacker creates a token that presents itself (via metadata consumed by wallets/exchanges/frontends) as a well-known, trusted asset while the underlying `Base` denom is a completely separate, attacker-controlled supply that the attacker can mint at will.

### Finding Description
`CreateDenom` only checks that the sub-denom string hasn't been used by this creator before and that no bank supply already exists for the raw subdenom (an IBC-collision guard), but the resulting denom is always namespaced as `factory/{creator}/{subdenom}`, so it never collides with `usei` or other canonical denoms at the base-denom level: [1](#0-0) 

`SetDenomMetadata` only verifies that `msg.Sender` is the admin of `msg.Metadata.Base` (i.e. the attacker's own fake denom) and that `Metadata.Validate()` passes — there is no check comparing `Name`/`Symbol`/`Display` against any existing denom's metadata: [2](#0-1) 

`Metadata.Validate()` in the underlying bank module only enforces that `Name`/`Symbol` are non-blank and that `Base`/`Display` are individually valid denom strings with internally consistent `DenomUnits` — it performs no global uniqueness check against other denoms' `Name`, `Symbol`, or `Display`: [3](#0-2) 

This is confirmed by the module's own test suite, where a factory-created denom is successfully given `Display: "usei"`, `Name: "SEI"`, `Symbol: "SEI"` — i.e. identical metadata to the chain's native token: [4](#0-3) 

Because the attacker is the admin of their own `factory/{attacker}/subdenom`, they can freely `Mint` (via `MsgMint`) arbitrary amounts of this token and then pass it around under the guise of `SEI`/`usei` (or any other target token's) name/symbol/display in any system that renders tokens by their bank metadata (wallets, block explorers, bridges, or third-party listing/deposit-detection logic) rather than by the fully-qualified `Base` denom string. This mirrors the BitDice incident, where a lookalike/fake asset ("fake EOS") was accepted by external systems that trusted superficial identifying attributes instead of verifying the genuine issuing authority/contract.

### Impact Explanation
An attacker can mint unlimited quantities of a token whose display metadata is indistinguishable from a legitimate, valuable token (native `usei`/`SEI` or any IBC/bridged asset with recognizable branding) and deposit it to any exchange, bridge, or dApp that identifies tokens by `Symbol`/`Display`/`Name` rather than the canonical `Base` denom. If such a counterparty credits the deposit as the real asset (exactly as BitDice's guessing game credited a spoofed "EOS" transfer), the attacker can withdraw real funds against worthless self-minted tokens — a direct, unbounded fund-loss vector for any denom-metadata-trusting integration.

### Likelihood Explanation
Trivial and fully permissionless: any account can call `MsgCreateDenom` and `MsgSetDenomMetadata` at will, with no special authority, deposit, or governance approval required beyond paying gas. The chain code and its own test suite demonstrate that setting metadata identical to the native token's `Name`/`Symbol`/`Display` succeeds without error.

### Recommendation
Reject `MsgSetDenomMetadata` (and ideally `MsgCreateDenom`'s implicit metadata creation) when the submitted `Name`, `Symbol`, or `Display` matches the metadata of the chain's native denom or any other already-registered denom's metadata, unless the sender is that denom's legitimate admin. At minimum, maintain a reserved/denylist of protected symbols (`SEI`, `usei`, and canonical bridged-asset symbols) that cannot be claimed by tokenfactory-created denoms, and document to integrators that denom identity must always be verified via the fully-qualified `Base` denom, never via `Symbol`/`Display` alone.

### Proof of Concept
1. Attacker calls `MsgCreateDenom{Sender: attacker, Subdenom: "fake"}` → denom `factory/{attacker}/fake` is created, with `attacker` set as admin (`x/tokenfactory/keeper/createdenom.go:13-21`).
2. Attacker calls `MsgSetDenomMetadata{Sender: attacker, Metadata: {Base: "factory/{attacker}/fake", Display: "usei", Name: "SEI", Symbol: "SEI", DenomUnits: [...]}}` → succeeds because `Validate()` only checks internal consistency, and the admin check passes since attacker is the admin of their own denom (`x/tokenfactory/keeper/msg_server.go:188-217`; validated by `x/tokenfactory/keeper/admins_test.go:180-200`).
3. Attacker calls `MsgMint{Sender: attacker, Amount: {Denom: "factory/{attacker}/fake", Amount: <arbitrary>}}` to mint an unlimited supply.
4. Attacker deposits/transfers the token to any external system (exchange, bridge, integration) that displays/accepts tokens by `Symbol`/`Display` ("SEI"/"usei") instead of validating the full `Base` denom, and withdraws real value in exchange — reproducing the BitDice "fake EOS" fund-loss pattern.

### Citations

**File:** x/tokenfactory/keeper/createdenom.go (L52-70)
```go
func (k Keeper) validateCreateDenom(ctx sdk.Context, creatorAddr string, subdenom string) (newTokenDenom string, err error) {
	// Temporary check until IBC bug is sorted out
	if k.bankKeeper.HasSupply(ctx, subdenom) {
		return "", fmt.Errorf("temporary error until IBC bug is sorted out, " +
			"can't create subdenoms that are the same as a native denom")
	}

	denom, err := types.GetTokenDenom(creatorAddr, subdenom)
	if err != nil {
		return "", err
	}

	_, found := k.bankKeeper.GetDenomMetaData(ctx, denom)
	if found {
		return "", types.ErrDenomExists
	}

	return denom, nil
}
```

**File:** x/tokenfactory/keeper/msg_server.go (L188-217)
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

	ctx.EventManager().EmitEvents(sdk.Events{
		sdk.NewEvent(
			types.TypeMsgSetDenomMetadata,
			sdk.NewAttribute(types.AttributeDenom, msg.Metadata.Base),
			sdk.NewAttribute(types.AttributeDenomMetadata, msg.Metadata.String()),
		),
	})

	return &types.MsgSetDenomMetadataResponse{}, nil
}
```

**File:** sei-cosmos/x/bank/types/metadata.go (L18-78)
```go
func (m Metadata) Validate() error {
	if strings.TrimSpace(m.Name) == "" {
		return errors.New("name field cannot be blank")
	}

	if strings.TrimSpace(m.Symbol) == "" {
		return errors.New("symbol field cannot be blank")
	}

	if err := sdk.ValidateDenom(m.Base); err != nil {
		return fmt.Errorf("invalid metadata base denom: %w", err)
	}

	if err := sdk.ValidateDenom(m.Display); err != nil {
		return fmt.Errorf("invalid metadata display denom: %w", err)
	}

	var (
		hasDisplay      bool
		currentExponent uint32 // check that the exponents are increasing
	)

	seenUnits := make(map[string]bool)

	for i, denomUnit := range m.DenomUnits {
		// The first denomination unit MUST be the base
		if i == 0 {
			// validate denomination and exponent
			if denomUnit.Denom != m.Base {
				return fmt.Errorf("metadata's first denomination unit must be the one with base denom '%s'", m.Base)
			}
			if denomUnit.Exponent != 0 {
				return fmt.Errorf("the exponent for base denomination unit %s must be 0", m.Base)
			}
		} else if currentExponent >= denomUnit.Exponent {
			return errors.New("denom units should be sorted asc by exponent")
		}

		currentExponent = denomUnit.Exponent

		if seenUnits[denomUnit.Denom] {
			return fmt.Errorf("duplicate denomination unit %s", denomUnit.Denom)
		}

		if denomUnit.Denom == m.Display {
			hasDisplay = true
		}

		if err := denomUnit.Validate(); err != nil {
			return err
		}

		seenUnits[denomUnit.Denom] = true
	}

	if !hasDisplay {
		return fmt.Errorf("metadata must contain a denomination unit with display denom '%s'", m.Display)
	}

	return nil
}
```

**File:** x/tokenfactory/keeper/admins_test.go (L180-200)
```go
		{
			desc: "successful set denom metadata",
			msgSetDenomMetadata: *types.NewMsgSetDenomMetadata(suite.TestAccs[0].String(), banktypes.Metadata{
				Description: "test1",
				DenomUnits: []*banktypes.DenomUnit{
					{
						Denom:    suite.defaultDenom,
						Exponent: 0,
					},
					{
						Denom:    "usei",
						Exponent: 6,
					},
				},
				Base:    suite.defaultDenom,
				Display: "usei",
				Name:    "SEI",
				Symbol:  "SEI",
			}),
			expectedPass: true,
		},
```
