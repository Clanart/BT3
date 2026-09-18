## Title
`addr` precompile's `associate()` accepts an unbound signature over an arbitrary caller-supplied message, allowing replay of any of a victim's off-chain/other-context ECDSA signatures to force a permanent, fund-migrating address association - (File: precompiles/addr/addr.go)

### Summary
The `associate` method of the `addr` precompile (address `0x...1004`) recovers a pubkey from a caller-supplied `(v, r, s, customMessage)` tuple and immediately uses the recovered EVM/Sei address pair to create a permanent, bidirectional address association plus a balance migration - without ever checking that `customMessage` is the canonical association message. This mirrors the reported bug class: a signature that lacks any binding to the specific action/context it is meant to authorize can be captured and replayed for a different, unintended purpose.

### Finding Description
`PrecompileExecutor.associate` takes four raw arguments — `v`, `r`, `s`, and a free-form `customMessage` string — hashes `customMessage` with `keccak256` and recovers the signer via `helpers.GetAddresses`, then calls `associateAddresses`, which performs the permanent state-machine changes: [1](#0-0) 

Crucially, there is no check anywhere in this path that `customMessage` equals the expected association text (`"Please sign this message to link your EVM and Sei addresses..."`); any string works, as shown by the only place the expected text is enforced — in the JS/TS test harnesses, not in the precompile itself: [2](#0-1) 

Because ECDSA signatures are deterministic-nonce or otherwise portable, **any** signature a wallet has ever produced over **any** message hash (e.g., a `personal_sign` login message for an unrelated dApp, a signature meant for another chain, a permit-style signature, etc.) can be captured off-chain and replayed into `associate()`. The precompile derives the EVM/Sei address pair purely from the recovered pubkey — it does not require that the caller (`msg.sender`) match the recovered address, and it does not require the signature to have been produced specifically for the Sei "associate" action.

Once `associate` succeeds, `AssociateAddresses` executes irreversible side effects: [3](#0-2) 
including migrating all spendable coins and wei balance from the EVM-address-derived "cast" account to the newly linked Sei address, and removing the cast account: [4](#0-3) 

This is the same underlying flaw class flagged in the external report: a signature is accepted as "proof of consent" for a specific state-changing action, but the signed payload contains no domain separator tying it to that specific action (no fixed message check, no chain ID, no nonce, no purpose tag). The codebase's own EIP-7702 authorization-recovery code explicitly documents awareness of this exact class of bug and the danger of "migrating their direct-cast balance and orphaning staking/distribution state" via forced association: [5](#0-4) 
— yet the `associate` precompile path (and its numerous legacy versions, e.g. v575 through v640) was never given the same protection: it accepts arbitrary caller-chosen `customMessage` without validating its content.

### Impact Explanation
An attacker who obtains any signature ever produced by a victim's EOA key (via phishing a benign-looking `personal_sign` request, observing a signature used for a different chain/dApp, or intercepting any signed payload whose hash is derivable) can force that victim's EVM address to be permanently and bidirectionally associated with a Sei address on-chain, at a time of the attacker's choosing, without the victim's consent or awareness. This:
- Permanently commits the victim's account (association cannot be undone or redone — `associate` rejects already-associated accounts).
- Immediately migrates any funds sitting in the direct-cast (`sdk.AccAddress(evmAddr[:])`) shadow account into the derived Sei address, which is an unrequested, irreversible balance-migration side effect triggered entirely by attacker-supplied replay.
- Can interfere with any protocol logic gated on association state or timing (e.g., airdrops, EIP-7702 flows, staking/distribution state as noted in the codebase's own comments).

This satisfies "unauthorized transfer via precompile" and "permanent" state change categories from the validation criteria, since fund migration and irreversible mapping occur without the affected user's specific authorization for that action.

### Likelihood Explanation
Likelihood is meaningful but not universal: it requires the attacker to obtain *some* signature from the victim's private key over *some* message (not necessarily the canonical association message). Given how common `personal_sign`/off-chain signing requests are in the broader Web3 ecosystem (logins, permits, order signing, etc.), and that this precompile is reachable by any public EVM transaction sender with no privilege requirement, an attacker only needs to capture one signature artifact from a target's public interactions to exploit this — a realistic bar for a "High" audit-class bug, matching the severity of the original report.

### Recommendation
Require `customMessage` to exactly match a canonical, versioned/domain-separated association message before accepting the signature (e.g., embed the chain ID and/or the caller-derived Sei address inside the mandatory message format, and reject any other string), rather than trusting the caller-provided `customMessage` verbatim. This closes the "signature accepted for any purpose" gap analogous to the reported `claimBySignature` replay issue and matches the mitigation pattern the codebase already documented and applied for EIP-7702 authorizations.

### Proof of Concept
1. Victim signs an unrelated off-chain message with their EOA private key for some other application (e.g., a `personal_sign` login challenge), producing `(v, r, s)` over `keccak256(message)`.
2. Attacker (or the dApp operator, or anyone who intercepts the signature) captures `message`, `v`, `r`, `s`.
3. Attacker calls `addr.associate(v-27_hex, r, s, message)` on the `0x...1004` precompile — no relationship to `msg.sender` is required. [6](#0-5) 
4. The precompile recovers the victim's EVM/Sei address pair from the signature and calls `AssociateAddresses`, permanently linking the addresses and migrating any funds sitting in the victim's direct-cast shadow account to the newly associated Sei address — all without the victim ever intending to perform an "associate" action. [7](#0-6)

### Citations

**File:** precompiles/addr/addr.go (L160-203)
```go
func (p PrecompileExecutor) associate(ctx sdk.Context, method *abi.Method, args []interface{}, value *big.Int) (ret []byte, remainingGas uint64, err error) {
	if err := pcommon.ValidateNonPayable(value); err != nil {
		return nil, 0, err
	}

	if err := pcommon.ValidateArgsLength(args, 4); err != nil {
		return nil, 0, err
	}

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
}
```

**File:** integration_test/precompile_tests/precompiles/addr.spec.ts (L26-38)
```typescript
/** The canonical association message users sign (mirrors the legacy hardhat suite). */
const ASSOCIATE_MESSAGE =
    'Please sign this message to link your EVM and Sei addresses. No SEI will be spent as a result of this signature.\n\n';

/** EIP-191 envelope the precompile hashes: "\x19Ethereum Signed Message:\n<len><message>". */
const eip191Envelope = (message: string): string =>
    `\x19Ethereum Signed Message:\n${Buffer.from(message, 'utf8').length}${message}`;

/** v/r/s in the precompile's expected shape: v is the 0/1 recovery id as hex. */
const signatureParts = async (wallet: EvmAccount, message: string) => {
    const sig = ethers.Signature.from(await wallet.wallet.signMessage(message));
    return { v: `0x${sig.v - 27}`, r: sig.r, s: sig.s };
};
```

**File:** utils/helpers/associate.go (L34-82)
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
```

**File:** utils/helpers/address.go (L143-155)
```go
// AuthorityToPreAssociate returns the authority of an EIP-7702 authorization that should be
// associated with its true (pubkey-derived) Sei address before execution, or ok=false.
//
// It mirrors go-ethereum's StateTransition.validateAuthorization (chain id, nonce overflow,
// authority code, and account-nonce checks) so that pre-association happens only for
// authorizations the EVM will actually apply, and additionally skips authorities that are
// already associated. Mirroring validateAuthorization is essential to security: the
// authorization sig hash is computed from the auth's own ChainID, so recovery and
// auth.Authority() succeed for an authorization signed for ANY chain. Without these checks a
// publicly-visible authorization a user signed for another chain (e.g. Ethereum mainnet)
// could be replayed in a sponsored Sei SetCode tx to force-associate them — migrating their
// direct-cast balance and orphaning staking/distribution state — even though the EVM skips
// the wrong-chain authorization and installs no delegation.
```
