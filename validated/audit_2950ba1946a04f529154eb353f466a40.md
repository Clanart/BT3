### Title
Incorrect Authorization in EVM/Sei Address Association Precompile Allows Remapping an EVM Address Away From Its Direct-Cast Identity, Orphaning Staking/Distribution State - ([File: precompiles/addr/addr.go])

### Summary
The `addr` precompile's `associate` / `associatePubKey` methods (and every legacy copy of them) authorize a new Sei↔EVM address mapping by checking only that the target Sei address has no existing association. They never check whether the EVM address's implicit "direct-cast" Sei identity (`sdk.AccAddress(evmAddr[:])`) already carries on-chain state such as staking or distribution records. This is the same bug class as GitLab CVE-2023-3920 — the check for whether it is safe to create a relationship between two entities is performed on only one side of the relationship, letting a caller create a state configuration ("fork") that documentation/invariants (one canonical Sei identity per EVM address) forbid.

### Finding Description
`associateAddresses` in the precompile only guards against re-associating a Sei address that is already mapped: [1](#0-0) 

It never calls `GetSeiAddress`/inspects the EVM address's *cast* Sei identity (`sdk.AccAddress(evmAddr[:])`) for existing locked balances, delegations, or validator state before rebinding that EVM address to a brand-new, unrelated Sei address.

`AssociateAddresses` in the shared helper only migrates spendable bank coins and wei balances from the cast address to the new Sei address; it does not migrate or block re-association when the cast address holds `LockedCoins` (i.e., staked funds): [2](#0-1) [3](#0-2) 

Critically, the sei-chain codebase itself documents this exact bug class as dangerous. The EVM ante-handler pre-associates EIP-7702 authorization authorities specifically to prevent this scenario, and the comment spells out the consequence verbatim: [4](#0-3) 

That mitigation only covers authorities listed in the *current* EIP-7702 transaction. It does nothing for the general-purpose `associate`/`associatePubKey` precompile calls, which any unprivileged EVM caller can invoke directly and which have the identical missing check: they let a caller point an EVM address's canonical Sei identity at a brand-new address while state (staking bonds, distribution records) tied to the EVM address's original direct-cast identity is left behind, unmigrated and now orphaned from the mapping that the rest of the protocol assumes is 1:1.

### Impact Explanation
If a delegator/validator self-bond, or other keeper state that is indexed by the direct-cast Sei address of an EVM account, exists prior to association, a subsequent call to `associate`/`associatePubKey` from that same EVM key can rebind the EVM address to a different Sei address without migrating or blocking on that staking/distribution state. The ante-handler's own comment states this class of remap "can then halt the chain via the distribution validator-removal hook," i.e., a validator-halt / consensus-disrupting condition, which meets the required Medium+ impact bar (validator halt / permanent chain disruption).

### Likelihood Explanation
Medium. The path requires an attacker/validator to first place funds or staking state under an EVM address's direct-cast Sei identity (achievable simply by sending `usei` or delegating using `sdk.AccAddress(evmAddr[:])` before ever calling `associate`), then invoking the public, permissionless `associate`/`associatePubKey` precompile method — reachable by any EOA or contract with access to precompile `0x1004`, with no privilege or ownership check on the operation.

### Recommendation
In `associateAddresses` (precompiles/addr/addr.go and all `legacy/vXXX` copies) and `AssociateAddresses` (utils/helpers/associate.go and its legacy copies), before rebinding `evmAddr` to a new `seiAddr`, check whether the cast address `sdk.AccAddress(evmAddr[:])` has non-zero `LockedCoins` (or other staking/distribution-relevant state) and reject/require full migration in that case, mirroring the protection already implemented for EIP-7702 authorities in `EVMPreprocessDecorator.AnteHandle`.

### Proof of Concept
1. Generate EVM keypair with address `X`; do not call `associate` yet. `castAddr(X) = sdk.AccAddress(X[:])` is a normal, unassociated Sei account.
2. Fund `castAddr(X)` directly via a bank transfer, then use it as delegator (`MsgDelegate`) or validator self-bond address — this creates staking/distribution keeper state keyed to `castAddr(X)`.
3. Call the `addr` precompile's `associatePubKey` (or `associate`) with a signature deriving `(evmAddr = X, seiAddr = B)` where `B` is a brand-new, never-associated Sei address.
4. `associateAddresses` only checks `GetEVMAddress(ctx, B)` — not found, so it proceeds. `SetAddressMapping(B, X)` is written; `MigrateBalance` moves spendable/wei balances but leaves `LockedCoins` (the staking bond) under the now-orphaned `castAddr(X)`.
5. `evmAddr X` is now mapped to `seiAddr B`, while validator/delegation records still reference `castAddr(X)`, an identity with a stripped/altered association — matching the exact "orphaning ... staking/distribution state ... halt the chain via the distribution validator-removal hook" scenario the codebase's own ante-handler comment warns about for the structurally identical EIP-7702 case. [4](#0-3)

### Citations

**File:** precompiles/addr/addr.go (L239-255)
```go
func (p PrecompileExecutor) associateAddresses(ctx sdk.Context, method *abi.Method, evmAddr common.Address, seiAddr sdk.AccAddress, pubkey cryptotypes.PubKey) (ret []byte, remainingGas uint64, err error) {
	// Check that address is not already associated
	_, found := p.evmKeeper.GetEVMAddress(ctx, seiAddr)
	if found {
		return nil, 0, fmt.Errorf("address %s is already associated with evm address %s", seiAddr, evmAddr)
	}

	// Associate Addresses:
	associationHelper := helpers.NewAssociationHelper(p.evmKeeper, p.bankKeeper, p.accountKeeper)
	err = associationHelper.AssociateAddresses(ctx, seiAddr, evmAddr, pubkey, false)
	if err != nil {
		return nil, 0, err
	}

	ret, err = method.Outputs.Pack(seiAddr.String(), evmAddr)
	return ret, pcommon.GetRemainingGas(ctx, p.evmKeeper), err
}
```

**File:** utils/helpers/associate.go (L34-54)
```go
func (p AssociationHelper) AssociateAddresses(ctx sdk.Context, seiAddr sdk.AccAddress, evmAddr common.Address, pubkey cryptotypes.PubKey, migrateUseiOnly bool) error {
	castAddr := sdk.AccAddress(evmAddr[:])
	if !castAddr.Equals(seiAddr) && p.accountKeeper.GetAccount(ctx, seiAddr) == nil {
		castAcc := p.accountKeeper.GetAccount(ctx, castAddr)
		castBaseAcc, ok := castAcc.(*authtypes.BaseAccount)
		if ok && castBaseAcc.GetPubKey() == nil && p.bankKeeper.LockedCoins(ctx, castAddr).IsZero() {
			p.accountKeeper.SetAccount(ctx, authtypes.NewBaseAccount(seiAddr, pubkey, castBaseAcc.GetAccountNumber(), castBaseAcc.GetSequence()))
		}
	}
	p.evmKeeper.SetAddressMapping(ctx, seiAddr, evmAddr)
	acc := p.accountKeeper.GetAccount(ctx, seiAddr)
	if acc == nil {
		acc = p.accountKeeper.NewAccountWithAddress(ctx, seiAddr)
	}
	if acc.GetPubKey() == nil {
		if err := acc.SetPubKey(pubkey); err != nil {
			return err
		}
		p.accountKeeper.SetAccount(ctx, acc)
	}
	return p.MigrateBalance(ctx, evmAddr, seiAddr, migrateUseiOnly)
```

**File:** utils/helpers/associate.go (L57-83)
```go
func (p AssociationHelper) MigrateBalance(ctx sdk.Context, evmAddr common.Address, seiAddr sdk.AccAddress, migrateUseiOnly bool) error {
	castAddr := sdk.AccAddress(evmAddr[:])
	if castAddr.Equals(seiAddr) {
		return nil
	}
	var castAddrBalances sdk.Coins
	if migrateUseiOnly {
		castAddrBalances = sdk.Coins{p.bankKeeper.GetBalance(ctx, castAddr, "usei")}
	} else {
		castAddrBalances = p.bankKeeper.SpendableCoins(ctx, castAddr)
	}
	if !castAddrBalances.IsZero() {
		if err := p.bankKeeper.SendCoins(ctx, castAddr, seiAddr, castAddrBalances); err != nil {
			return err
		}
	}
	castAddrWei := p.bankKeeper.GetWeiBalance(ctx, castAddr)
	if !castAddrWei.IsZero() {
		if err := p.bankKeeper.SendCoinsAndWei(ctx, castAddr, seiAddr, sdk.ZeroInt(), castAddrWei); err != nil {
			return err
		}
	}
	if p.bankKeeper.LockedCoins(ctx, castAddr).IsZero() {
		p.accountKeeper.RemoveAccount(ctx, authtypes.NewBaseAccountWithAddress(castAddr))
	}
	return nil
}
```

**File:** x/evm/ante/preprocess.go (L103-110)
```go
	// EIP-7702 authorization authorities are distinct accounts from the tx sender, so the
	// sender association above does not cover them. Associate each authority to its true
	// (pubkey-derived) Sei address before EVM execution installs delegation code for it.
	// Otherwise SetCode creates a mutable direct-cast EVM->Sei mapping that a later
	// associatePubKey call can remap, orphaning any staking/distribution state created
	// under the direct-cast identity (which can then halt the chain via the distribution
	// validator-removal hook).
	p.associateAuthorizationAuthorities(ctx, msg, associateHelper)
```
