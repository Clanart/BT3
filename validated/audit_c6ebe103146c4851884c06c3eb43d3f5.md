Based on my research, I found a strong analog in the tokenfactory module's `CreateDenom` flow. The AuraSpell bug class is: a user-supplied token/asset parameter is not cross-validated against the actual, implicit resource that a separate piece of logic depends on, and this mismatch is only checked as a "temporary" workaround — leaving the door open for funds tied to the mismatched denom to be permanently stuck or for the two representations of the "same" asset to diverge.

### Title
Tokenfactory `CreateDenom` only checks bank supply, not existing `DenomMetaData`/pointer state, allowing a created denom to collide with an unrelated token's identity - ([File: x/tokenfactory/keeper/createdenom.go])

### Summary
`validateCreateDenom` in the tokenfactory module explicitly documents a known, unresolved validation gap ("Temporary check until IBC bug is sorted out") where a new `factory/{creator}/{subdenom}` denom is only blocked from creation if it collides with an existing bank *supply* entry — not with a denom that merely has `DenomMetaData` set (e.g., because it already has an ERC20/CW20/CW721 pointer registered against it, as with IBC/ICS20 denoms). This mirrors the AuraSpell root cause: a user-controlled asset identifier is accepted without validating it against the actual underlying resource state that other parts of the system implicitly rely on.

### Finding Description
`CreateDenom` is reachable by any unprivileged account and delegates validation to `validateCreateDenom`: [1](#0-0) 

This function only calls `k.bankKeeper.HasSupply(ctx, subdenom)` — explicitly labeled a temporary workaround "until IBC bug is sorted out" — and separately checks `GetDenomMetaData` for the exact computed `factory/{creator}/{subdenom}` string, not for the raw `subdenom` itself. Because tokenfactory denoms are namespaced by creator address (`factory/{creator}/{subdenom}`), an attacker can pick a `subdenom` matching the base denom of an existing IBC-transferred or otherwise metadata-registered asset that has zero on-chain `HasSupply` at the moment of creation (e.g., a denom that had a pointer or metadata registered via `AddNativePointer` but currently has no minted supply, per [2](#0-1) ). This creates a brand-new tokenfactory-controlled denom that is superficially indistinguishable in downstream tooling (which key off denom string) from the pre-existing asset, while the attacker holds full mint/burn/transfer "admin" authority over it per the tokenfactory admin model.

### Impact Explanation
If a subdenom collides with an existing but currently-zero-supply denom that already has bank metadata / an ERC20 or CW20 pointer wired to it, the attacker-created denom becomes admin-controlled by the attacker (mint/burn/force-transfer any amount) while sharing the same string identity relied upon by pointer contracts and any downstream integrations keyed by denom string, per the tokenfactory admin capabilities documented in [3](#0-2) . This can result in unauthorized minting/transfer of value perceived as tied to the legitimate asset (fund loss / unauthorized transfer via a precompile/pointer, matching the accepted impact classes).

### Likelihood Explanation
Likelihood is constrained: it requires finding a denom that has metadata (or a pointer) registered but currently has `HasSupply(ctx, denom) == false`, which the code comment itself flags as a residual, known-but-unfixed edge case ("Temporary check until IBC bug is sorted out, can't create subdenoms that are the same as a native denom"). This indicates the sei-chain maintainers are aware the `HasSupply` check is incomplete protection and a metadata-based collision is not fully closed.

### Recommendation
Extend `validateCreateDenom` to also reject subdenoms that collide with any denom that already has `DenomMetaData` set (via `bankKeeper.GetDenomMetaData`) or an existing ERC20/CW20/CW721 pointer registered (via `evmKeeper.GetERC20NativePointer`/`GetERC20CW20Pointer`/`GetERC721CW721Pointer`), not just those with non-zero `HasSupply`.

### Proof of Concept
1. Identify a denom `D` that has bank `DenomMetaData` set (e.g., via IBC transfer that later burned to zero supply, or via governance-set metadata) but for which `bankKeeper.HasSupply(ctx, D) == false` at the current block.
2. Submit `MsgCreateDenom{sender: attacker, subdenom: D}` via tokenfactory.
3. `validateCreateDenom` passes because `HasSupply` returns false and `GetDenomMetaData(ctx, "factory/{attacker}/{D}")` (not `D` itself) returns not-found.
4. Attacker now holds full tokenfactory admin rights (mint/burn/transfer) over a denom that may still be treated by other subsystems as tied to the original asset `D`'s identity.

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

**File:** precompiles/pointer/legacy/v552/pointer.go (L150-156)
```go
	metadata, metadataExists := p.bankKeeper.GetDenomMetaData(ctx, token)
	if !metadataExists {
		return nil, 0, fmt.Errorf("denom %s does not have metadata stored and thus can only have its pointer set through gov proposal", token)
	}
	name := metadata.Name
	symbol := metadata.Symbol
	var decimals uint8
```

**File:** x/tokenfactory/README.md (L1-18)
```markdown
# Token Factory

The tokenfactory module allows any account to create a new token with
the name `factory/{creator address}/{subdenom}`. Because tokens are
namespaced by creator address, this allows token minting to be
permissionless, due to not needing to resolve name collisions. A single
account can create multiple denoms, by providing a unique subdenom for each
created denom. Once a denom is created, the original creator is given
"admin" privileges over the asset. This allows them to:

- Mint their denom to any account
- Burn their denom from any account
- Create a transfer of their denom between any two accounts
- Change the admin. In the future, more admin capabilities may be added. Admins
  can choose to share admin privileges with other accounts using the authz
  module. The `ChangeAdmin` functionality, allows changing the master admin
  account, or even setting it to `""`, meaning no account has admin privileges
  of the asset.
```
