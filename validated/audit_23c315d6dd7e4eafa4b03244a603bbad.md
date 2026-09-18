### Title
Unbounded-length denom metadata in `MsgSetDenomMetadata` allows creation of oversized `Metadata` objects served by public queries - (File: `sei-cosmos/x/bank/types/metadata.go`)

### Summary
GitLab CVE-2022-2592 is a lack of length validation on a user-supplied text field (Snippet description) that lets an authenticated user create an oversized object; every subsequent read of that object (with or without auth) then imposes excessive load, causing a DoS. Sei-chain has an analogous pattern: `bank.types.Metadata.Validate()` validates presence/format of `Name`, `Symbol`, `Base`, `Display`, and `DenomUnits`, but never bounds the length of `Name`, `Symbol`, `Description`, `DenomUnit.Denom`, or `DenomUnit.Aliases`. [1](#0-0) 

### Finding Description
Any tokenfactory denom admin can submit `MsgSetDenomMetadata` for a denom they administer. The handler only performs a "defense in depth" call to `msg.Metadata.Validate()` before persisting the metadata via `bankKeeper.SetDenomMetaData`: [2](#0-1) 

`ValidateBasic` on the message itself likewise only calls `Metadata.Validate()` and denom decomposition — again with no size checks: [3](#0-2) 

Since `Validate()` never checks `len(Name)`, `len(Symbol)`, `len(Description)`, or the size/count of `DenomUnits`/`Aliases`, an attacker who legitimately created (or administers) a tokenfactory denom can pack these string fields with data up to the maximum allowed by the overall transaction size limit (there is no field-specific cap analogous to `MaxWasmSize`/`MaxLabelSize` used elsewhere in the codebase, e.g. in wasmd's `validateWasmCode`/`validateLabel`): [4](#0-3) 

Once stored, this metadata is retrievable through the public `bank` gRPC/REST/CLI query surface (`DenomMetadata`, `DenomsMetadata`) and consumed by any node processing those requests, as well as by the EVM bank precompile and other internal consumers that read `Metadata` objects. Every unauthenticated or authenticated caller who queries denom metadata (directly or the aggregate `DenomsMetadata` listing all denoms) forces the node to marshal/unmarshal and serialize the oversized object repeatedly — an unbounded-size, attacker-controlled payload that is read on every such request, exactly mirroring the GitLab Snippet-description bug class (create-once, load-many amplification).

### Impact Explanation
This does not enable fund loss, but it is a resource-exhaustion vector reachable by any account that creates a tokenfactory denom (a permissionless, unprivileged action) followed by a single `MsgSetDenomMetadata` call. Because the field has no bound distinct from the raw transaction-size limit, an attacker can inflate metadata close to the max tx size and force excess CPU/memory/bandwidth on every node serving `bank` metadata queries thereafter, degrading public RPC nodes that serve this data — a Medium-severity denial-of-service risk consistent with the reported bug class and rules requiring "crash of default-configuration RPC nodes" or similar service degradation impact.

### Likelihood Explanation
Likelihood is moderate-to-high: creating a tokenfactory denom and calling `MsgSetDenomMetadata` requires no special permission beyond being the denom's admin (which the creator automatically becomes), and both `ValidateBasic` and the in-keeper `Validate()` call permit arbitrarily large `Name`/`Symbol`/`Description`/`DenomUnits` content as long as the message fits within normal transaction size limits.

### Recommendation
Add explicit maximum-length bounds (analogous to `MaxLabelSize`/`MaxWasmSize` in `sei-wasmd/x/wasm/types/validation.go`) for `Metadata.Name`, `Metadata.Symbol`, `Metadata.Description`, each `DenomUnit.Denom`, and each `DenomUnit.Aliases` entry, plus a cap on the number of `DenomUnits`/`Aliases`, inside `bank.types.Metadata.Validate()` (and/or `MsgSetDenomMetadata.ValidateBasic()`), so that stored metadata objects cannot grow unbounded relative to overall transaction size.

### Proof of Concept
1. Attacker creates a tokenfactory denom via `MsgCreateDenom` (permissionless), becoming its admin.
2. Attacker submits `MsgSetDenomMetadata` with `Metadata.Description` (and/or `Name`/`Symbol`/`DenomUnits[].Aliases`) filled with hundreds of KB of data — up to the raw tx-size limit, since `Metadata.Validate()` ( [1](#0-0) ) and `MsgSetDenomMetadata.ValidateBasic()` ( [3](#0-2) ) impose no length checks.
3. The message is processed by `SetDenomMetadata` in `x/tokenfactory/keeper/msg_server.go` and persisted ( [2](#0-1) ).
4. Any subsequent unauthenticated caller queries `bank.DenomMetadata` or `bank.DenomsMetadata` for the denom (or all denoms), forcing every serving node to repeatedly marshal/transmit the oversized payload, degrading RPC node performance for all clients — the same "create-once, request-many amplification" pattern as CVE-2022-2592.

### Citations

**File:** sei-cosmos/x/bank/types/metadata.go (L18-34)
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

**File:** sei-wasmd/x/wasm/types/validation.go (L1-33)
```go
package types

import (
	sdkerrors "github.com/sei-protocol/sei-chain/sei-cosmos/types/errors"
)

var (
	// MaxLabelSize is the longest label that can be used when Instantiating a contract
	MaxLabelSize = 128 // extension point for chains to customize via compile flag.

	// MaxWasmSize is the largest a compiled contract code can be when storing code on chain
	MaxWasmSize = 800 * 1024 // extension point for chains to customize via compile flag.
)

func validateWasmCode(s []byte) error {
	if len(s) == 0 {
		return sdkerrors.Wrap(ErrEmpty, "is required")
	}
	if len(s) > MaxWasmSize {
		return sdkerrors.Wrapf(ErrLimit, "cannot be longer than %d bytes", MaxWasmSize)
	}
	return nil
}

func validateLabel(label string) error {
	if label == "" {
		return sdkerrors.Wrap(ErrEmpty, "is required")
	}
	if len(label) > MaxLabelSize {
		return sdkerrors.Wrap(ErrLimit, "cannot be longer than 128 characters")
	}
	return nil
}
```
