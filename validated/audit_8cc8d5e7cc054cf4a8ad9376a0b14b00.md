## Title
Approvals cannot be revoked while an ERC20 token pair is disabled, exposing users to permanent allowance-drain risk - (File: `x/erc20/keeper/allowance.go`)

### Summary
This is the direct Cosmos EVM analog of the reNFT "blacklisted extensions can't be disabled" finding. In reNFT, a safe owner cannot disable a module once it is blacklisted, leaving the safe permanently exposed if that module turns malicious. In Cosmos EVM's `x/erc20` module, the same asymmetric-guard pattern exists for ERC20 allowances on the ERC20 precompile: revoking an `approve` (setting allowance to zero) is gated by the *same* "token pair must be enabled" check used for granting an allowance. Once a token pair is disabled (`pair.Enabled == false`), a user who previously granted an allowance to a spender has **no way to revoke it** until governance re-enables the pair — at which point the spender (which may since have become malicious/compromised) can immediately drain the still-valid allowance via `transferFrom`, before the victim has any chance to react.

### Finding Description
`k.setAllowance` in `x/erc20/keeper/allowance.go` is the single internal function backing both allowance creation and revocation: [1](#0-0) 

- `SetAllowance` (grant/increase) calls `setAllowance(..., allowDisabledTokenPair=false)`.
- `DeleteAllowance` (revoke, i.e. `approve(spender, 0)`) **also** calls `setAllowance(..., allowDisabledTokenPair=false)`.
- Both paths hit the guard:
```go
if !allowDisabledTokenPair && !tokenPair.Enabled {
    return errorsmod.Wrapf(types.ErrERC20TokenPairDisabled, ...)
}
```
So exactly like the reNFT `Guard.sol` check that gated both "enable module" and "disable module" behind the same whitelist check, this single check gates both *granting* and *revoking* an allowance behind the same "pair enabled" flag.

The precompile-level `Approve` method in `precompiles/erc20/approve.go` explicitly documents that setting the amount to zero on an existing allowance should delete it (case 3), but that call goes through `DeleteAllowance`, which will revert with `ErrERC20TokenPairDisabled` if the pair has been disabled: [2](#0-1) 

Meanwhile, only a privileged `MsgToggleConversion` (governed by `authority`) can disable a token pair: [3](#0-2) 

Critically, the actual value-moving path (`Transfer`/`TransferFrom`) does *not* check `pair.Enabled` at all before/independently of updating the allowance — the only place `pair.Enabled` is enforced in the ERC20 spend flow is inside `SetAllowance`/`DeleteAllowance`, which `transfer()` calls to update the allowance after a `transferFrom`: [4](#0-3) 

This has two consequences:
1. While the pair is disabled, `transferFrom` calls also fail (since the allowance-update step reverts), which incidentally blocks spending during the disabled window.
2. But the user's own attempt to proactively revoke the allowance (`approve(spender, 0)`) fails for the exact same reason. The allowance is never cleared while the pair is disabled.

The result: as soon as governance re-enables the pair (`ToggleConversion` again, a normal, expected operational action — token pairs are explicitly documented as re-enable-able, see the "even if token pair is disabled ... can be enabled later" comment), a spender holding a stale allowance can immediately execute `transferFrom` and drain the full still-valid allowance, before the legitimate owner — who was unable to revoke it during the entire disabled window — gets any opportunity to act. [5](#0-4) 

### Impact Explanation
This breaks the "Critical permanent freezing, locking, theft, or unauthorized extraction of user funds" invariant: any unprivileged user's spendable ERC20-precompile balance remains exposed to a previously-approved spender for the full duration a token pair is disabled, with the account owner having no self-service mechanism (revoke) to mitigate that exposure. Because pair disable/enable is a normal, expected lifecycle operation (not solely an emergency action) and disabling never auto-revokes outstanding allowances, an attacker who obtains an allowance grant (e.g. via a compromised or later-malicious spender/dApp) can wait for any disable→enable cycle and win the race to drain the allowance the instant the pair re-enables, while the affected user — who spent the entire disabled window unable to revoke — has no way to have protected themselves in advance.

### Likelihood Explanation
Likelihood is moderate-to-high: token pair `ToggleConversion` is a documented, reachable governance action (used operationally, not only in extreme emergencies), and every existing allowance interacts with the same `setAllowance` revocation guard. Any user who has ever approved a spender is affected the moment the pair is toggled off, and the exploit window opens automatically and deterministically the moment the pair is toggled back on — no special privilege is needed by the attacker beyond already holding a stale allowance.

### Recommendation
Decouple allowance revocation from the token-pair-enabled gate: allow `DeleteAllowance` (i.e., setting allowance to zero) to succeed even when `tokenPair.Enabled == false`, mirroring the `allowDisabledTokenPair` bypass that already exists for `UnsafeSetAllowance`/genesis. Granting new or increased allowances should remain blocked while disabled, but revocation should always be permitted so users can protect themselves at any time, exactly as the reNFT mitigation recommended letting users always disable a module regardless of whitelist status.

### Proof of Concept
1. Register/enable an ERC20 token pair `P` and mint balance to `owner`.
2. `owner` calls `approve(spender, X)` on the ERC20 precompile for pair `P` — succeeds (`SetAllowance`, pair enabled).
3. Governance calls `MsgToggleConversion` to disable pair `P` (a normal, non-emergency, and reversible operational action, per `toggleConversion` in `x/erc20/keeper/proposals.go`).
4. `owner`, now wanting to protect their funds, calls `approve(spender, 0)` to revoke — this reverts with `types.ErrERC20TokenPairDisabled` from `setAllowance` in `x/erc20/keeper/allowance.go`, exactly mirroring the reNFT PoC where `disableModule` reverts on a blacklisted extension.
5. Governance calls `MsgToggleConversion` again to re-enable pair `P`.
6. `spender` immediately calls `transferFrom(owner, spender, X)` — succeeds and drains `owner`'s balance, since the allowance was never revoked and `transfer()` in `precompiles/erc20/tx.go` performs the bank `Send` unconditionally once the allowance check passes. [6](#0-5) [7](#0-6)

### Citations

**File:** x/erc20/keeper/allowance.go (L40-96)
```go
// SetAllowance sets the allowance of the given owner and spender
// on the given erc20 precompile address.
func (k Keeper) SetAllowance(
	ctx sdk.Context,
	erc20 common.Address,
	owner common.Address,
	spender common.Address,
	value *big.Int,
) error {
	return k.setAllowance(ctx, erc20, owner, spender, value, false)
}

// DeleteAllowance deletes the allowance of the given owner and spender
// on the given erc20 precompile address.
func (k Keeper) DeleteAllowance(
	ctx sdk.Context,
	erc20 common.Address,
	owner common.Address,
	spender common.Address,
) error {
	return k.setAllowance(ctx, erc20, owner, spender, common.Big0, false)
}

// UnsafeSetAllowance sets the allowance of the given owner and spender with validation.
// It allows setting allowance for disabled token pairs.
// This should only be used in InitGenesis.
func (k Keeper) UnsafeSetAllowance(
	ctx sdk.Context,
	erc20 common.Address,
	owner common.Address,
	spender common.Address,
	value *big.Int,
) error {
	return k.setAllowance(ctx, erc20, owner, spender, value, true)
}

func (k Keeper) setAllowance(
	ctx sdk.Context,
	erc20 common.Address,
	owner common.Address,
	spender common.Address,
	value *big.Int,
	allowDisabledTokenPair bool,
) error {
	// validate existence of token pair
	tokenPairID := k.GetERC20Map(ctx, erc20)
	tokenPair, found := k.GetTokenPair(ctx, tokenPairID)
	if !found {
		return errorsmod.Wrapf(
			types.ErrTokenPairNotFound, "token pair for address '%s' not registered", erc20,
		)
	}
	if !allowDisabledTokenPair && !tokenPair.Enabled {
		return errorsmod.Wrapf(
			types.ErrERC20TokenPairDisabled, "token pair for address '%s' is disabled", erc20,
		)
	}
```

**File:** precompiles/erc20/approve.go (L47-60)
```go
	switch {
	case allowance.Sign() == 0 && amount != nil && amount.Sign() < 0:
		// case 1: no allowance, amount 0 or negative -> error
		err = ErrNegativeAmount
	case allowance.Sign() == 0 && amount != nil && amount.Sign() > 0:
		// case 2: no allowance, amount positive -> create a new allowance
		err = p.setAllowance(ctx, owner, spender, amount)
	case allowance.Sign() > 0 && amount != nil && amount.Sign() <= 0:
		// case 3: allowance exists, amount 0 or negative -> remove from spend limit and delete allowance if no spend limit left
		err = p.erc20Keeper.DeleteAllowance(ctx, p.Address(), owner, spender)
	case allowance.Sign() > 0 && amount != nil && amount.Sign() > 0:
		// case 4: allowance exists, amount positive -> update allowance
		err = p.setAllowance(ctx, owner, spender, amount)
	}
```

**File:** x/erc20/keeper/proposals.go (L116-138)
```go
// ToggleConversion toggles conversion for a given token pair
func (k Keeper) toggleConversion(
	ctx sdk.Context,
	token string,
) (types.TokenPair, error) {
	id := k.GetTokenPairID(ctx, token)
	if len(id) == 0 {
		return types.TokenPair{}, errorsmod.Wrapf(
			types.ErrTokenPairNotFound, "token '%s' not registered by id", token,
		)
	}

	pair, found := k.GetTokenPair(ctx, id)
	if !found {
		return types.TokenPair{}, errorsmod.Wrapf(
			types.ErrTokenPairNotFound, "token '%s' not registered", token,
		)
	}

	pair.Enabled = !pair.Enabled
	k.SetTokenPair(ctx, pair)
	return pair, nil
}
```

**File:** precompiles/erc20/tx.go (L85-116)
```go
	isTransferFrom := method.Name == TransferFromMethod
	spenderAddr := contract.Caller()
	newAllowance := big.NewInt(0)

	if isTransferFrom {
		prevAllowance, err := p.erc20Keeper.GetAllowance(ctx, p.Address(), from, spenderAddr)
		if err != nil {
			return nil, ConvertErrToERC20Error(err)
		}

		newAllowance = new(big.Int).Sub(prevAllowance, amount)
		if newAllowance.Sign() < 0 {
			return nil, ErrInsufficientAllowance
		}

		if newAllowance.Sign() == 0 {
			// If the new allowance is 0, we need to delete it from the store.
			err = p.erc20Keeper.DeleteAllowance(ctx, p.Address(), from, spenderAddr)
		} else {
			// If the new allowance is not 0, we need to set it in the store.
			err = p.erc20Keeper.SetAllowance(ctx, p.Address(), from, spenderAddr, newAllowance)
		}
		if err != nil {
			return nil, ConvertErrToERC20Error(err)
		}
	}

	msgSrv := NewMsgServerImpl(p.BankKeeper)
	if err = msgSrv.Send(ctx, msg); err != nil {
		// This should return an error to avoid the contract from being executed and an event being emitted
		return nil, ConvertErrToERC20Error(err)
	}
```

**File:** tests/integration/x/erc20/test_allowance.go (L453-459)
```go
			},
		},
		{
			// NOTES: GetAllowances() is only for genesis import & export.
			// Because disabled token pair can be enabled later,
			// when allowances related to disabled token pair should also be included in the exported state.
			"pass - even if token pair is disabled, return allowances",
```
