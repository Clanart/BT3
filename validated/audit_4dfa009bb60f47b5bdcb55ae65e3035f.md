### Title
Unrestricted `customMessage` in the `addr` precompile's `associate()` allows signature replay to force unauthorized address association and fund migration - (File: precompiles/addr/addr.go)

### Summary
The `addr` precompile's `associate(v, r, s, customMessage)` function recovers a pubkey/address pair from an arbitrary, caller-supplied `customMessage` and its `v/r/s` signature, with no on-chain validation that the message is the canonical Sei association prompt, and no binding to chain ID, nonce, or a Sei-specific domain separator. Any previously observed `personal_sign`-style signature produced by a victim for an unrelated purpose (a different dApp login, a permit-style message, etc.) can therefore be replayed by anyone to trigger `associate()` on Sei. Because a successful association immediately and irreversibly triggers `MigrateBalance`, this forces an unauthorized on-chain transfer of the victim's funds from the "cast address" to the real Sei address, at a time and place chosen solely by whoever holds the leaked signature—not the victim.

### Finding Description
`associate()` treats `customMessage` as fully attacker-controlled input: [1](#0-0) 

The hash fed into signature recovery is simply `keccak256(customMessage)` with no fixed prefix/purpose check, no chain-ID binding, and no nonce or deadline: [2](#0-1) 

The expected client-side usage only *conventionally* signs a fixed "Please sign this message to link your EVM and Sei addresses..." string, but nothing in `associate()` enforces that convention on-chain — the contract will happily recover and associate addresses for *any* message the caller supplies together with a valid signature over it: [3](#0-2) 

Once `associate()` succeeds, `associateAddresses()` calls `AssociationHelper.AssociateAddresses`, which unconditionally calls `MigrateBalance`: [4](#0-3) 

`AssociateAddresses`/`MigrateBalance` sweep all spendable coins and wei balance from the "cast address" (`sdk.AccAddress(evmAddr[:])`) into the newly linked Sei address, and remove the cast `BaseAccount` if it holds no locked coins — an irreversible, automatic transfer of funds: [5](#0-4) 

This mirrors the reported bug class exactly: a signature intended for one purpose/context is accepted for another purpose because the signed payload lacks context-binding data (no domain separator, no nonce/deadline, no explicit statement of intent enforced on-chain). In the Tokensoft report, this let a purchased/leaked signature be replayed across unrelated airdrops; here it lets any historically leaked `personal_sign` signature be replayed to force an EVM<->Sei address linkage and immediate fund migration that the victim never authorized for this purpose.

### Impact Explanation
- The association is permanent (a repeated call fails with "address is already associated"), so an attacker can force this identity linkage — and the associated fund sweep from the cast address — at a time of their choosing, without the victim's consent or even awareness that the replayed signature could be used this way.
- `MigrateBalance` performs a real `SendCoins`/`SendCoinsAndWei` fund transfer as an automatic side effect of the precompile call, and also deletes the cast `BaseAccount`. This is an unauthorized state-changing/fund-moving action triggered via replay of a signature that was never intended to authorize it, satisfying the "unauthorized transfer via precompile" impact category.
- Because the resulting mapping is between the victim's own EVM pubkey-derived addresses, the attacker does not steal funds to their own address, but they can force irreversible account merging/fund migration on the victim's behalf at an attacker-controlled time, which can disrupt account assumptions (e.g., moving previously "unlinked" cast-address balances into a now-publicly-linked identity) and cannot be undone by the victim.

### Likelihood Explanation
Likelihood is moderate: it requires the attacker to obtain a valid `(message, v, r, s)` tuple signed by the victim's EVM key for some other purpose (e.g., a `personal_sign`-based "Sign-In with Ethereum" flow, or any other message a wallet signs off-chain) and to know/derive the victim's expectation that association hasn't happened yet. Many wallets and dApps request `personal_sign` signatures over arbitrary human-readable strings, and such signatures are routinely observable (server logs, block explorers if later published on any chain, phishing pages), making this practically reachable by any actor able to submit an EVM transaction to the public `addr` precompile at `0x0000000000000000000000000000000000001004`.

### Recommendation
Bind the association signature to a fixed, Sei-specific, non-reusable payload instead of an arbitrary caller-supplied string:
- Enforce that `customMessage` matches an exact canonical template (e.g., must contain the fixed "Please sign this message to link..." prefix) rather than accepting any string.
- Include chain ID and the target Sei address (or the caller's EVM address) inside the signed payload so a signature cannot be replayed across chains/purposes.
- Consider adding a nonce or short-lived deadline to the signed message so leaked signatures cannot be replayed indefinitely.

### Proof of Concept
1. Victim's EVM wallet produces a `personal_sign` signature over some unrelated message `M` for a third-party dApp (e.g., a login/nonce message), yielding `(v, r, s)`. This tuple becomes known to an attacker (e.g., via a compromised dApp backend, public receipt data, or phishing).
2. Attacker calls the `addr` precompile: `associate(v-27, r, s, M)`.
3. `associate()` computes `customMessageHash = keccak256(M)` and recovers the victim's real `evmAddr`/`seiAddr`/`pubkey` — see [2](#0-1) .
4. Since the account is not yet associated, `associateAddresses()` succeeds and calls `AssociationHelper.AssociateAddresses`, which immediately migrates all `SpendableCoins`/wei balance from `sdk.AccAddress(evmAddr[:])` to `seiAddr` and deletes the cast account — see [6](#0-5) .
5. The victim's EVM/Sei identity is now permanently linked and any balance previously held at the derived cast address has been moved, none of which the victim authorized via this specific signature.

### Citations

**File:** precompiles/addr/addr.go (L169-202)
```go
	// v, r and s are components of a signature over the customMessage sent.
	// We use the signature to construct the user's pubkey to obtain their addresses.
	v := args[0].(string)
	r := args[1].(string)
	s := args[2].(string)
	customMessage := args[3].(string)

	rBytes, err := decodeHexString(r)
	if err != nil {
		return nil, 0, err
	}
	sBytes, err := decodeHexString(s)
	if err != nil {
		return nil, 0, err
	}
	vBytes, err := decodeHexString(v)
	if err != nil {
		return nil, 0, err
	}

	vBig := new(big.Int).SetBytes(vBytes)
	rBig := new(big.Int).SetBytes(rBytes)
	sBig := new(big.Int).SetBytes(sBytes)

	// Derive addresses
	vBig = new(big.Int).Add(vBig, utils.Big27)

	customMessageHash := crypto.Keccak256Hash([]byte(customMessage))
	evmAddr, seiAddr, pubkey, err := helpers.GetAddresses(vBig, rBig, sBig, customMessageHash)
	if err != nil {
		return nil, 0, err
	}

	return p.associateAddresses(ctx, method, evmAddr, seiAddr, pubkey)
```

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

**File:** integration_test/precompile_tests/precompiles/addr.spec.ts (L31-38)
```typescript
const eip191Envelope = (message: string): string =>
    `\x19Ethereum Signed Message:\n${Buffer.from(message, 'utf8').length}${message}`;

/** v/r/s in the precompile's expected shape: v is the 0/1 recovery id as hex. */
const signatureParts = async (wallet: EvmAccount, message: string) => {
    const sig = ethers.Signature.from(await wallet.wallet.signMessage(message));
    return { v: `0x${sig.v - 27}`, r: sig.r, s: sig.s };
};
```

**File:** utils/helpers/associate.go (L34-83)
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
}

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
