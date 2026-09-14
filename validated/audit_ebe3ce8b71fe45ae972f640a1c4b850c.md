### Title
Cross-domain BLS signature reuse via `sign_message_by_address`/`sign_message_by_id` raw signing modes enables unauthorized coin spend authorization - (File: `chia/wallet/util/signing.py`)

### Summary
The wallet RPC message-signing endpoints (`sign_message_by_address`, `sign_message_by_id`) use the exact same `synthetic_secret_key` that is used to produce `AGG_SIG_ME` signatures for spending a coin's standard puzzle. When the caller selects `BLS_MESSAGE_AUGMENTATION_HEX_INPUT` or `BLS_MESSAGE_AUGMENTATION_UTF8_INPUT` signing mode, the message is signed raw with no domain-separation prefix, unlike the `CHIP_0002` mode which wraps the message in `("Chia Signed Message", message)`. Because raw `AGG_SIG_ME` messages have the exact structure `delegated_puzzle_hash + coin_id + AGG_SIG_ME_ADDITIONAL_DATA`, an attacker who can induce (or programmatically drive via RPC) a "sign this message" action on this raw mode can obtain a signature that is byte-for-byte a valid spend-authorization signature for one of the wallet's coins.

### Finding Description
`sign_message` in `chia/wallet/util/signing.py` builds the exact bytes to sign based on `mode`: [1](#0-0) 

For `BLS_MESSAGE_AUGMENTATION_HEX_INPUT`/`BLS_MESSAGE_AUGMENTATION_UTF8_INPUT`, the exact caller-supplied bytes (`hexstr_to_bytes(message)` or UTF-8 bytes) are signed with no prefix/domain tag, unlike `CHIP_0002` variants which hash `("Chia Signed Message", message)` first. This is the only signing mode among the CHIP-0002 family that produces an unprefixed raw signature.

The wallet RPC handlers use the **same synthetic secret key** used for standard-coin spend signing (`AGG_SIG_ME`): [2](#0-1) [3](#0-2) 

Standard-coin `AGG_SIG_ME` messages are constructed as:
```
msg + coin.name() + AGG_SIG_ME_ADDITIONAL_DATA
```
where for a plain delegated-puzzle spend `msg` is typically `delegated_puzzle_hash` (see `make_aggsig_final_message`): [4](#0-3) 

and this is exactly the message the standard puzzle asks to be signed via `AGG_SIG_ME`, as documented: [5](#0-4) 

Since `sign_message`'s raw modes sign attacker-supplied bytes with zero framing, an attacker who controls (or can obtain) the `message` hex string passed to `sign_message_by_address`/`sign_message_by_id` can set `message = delegated_puzzle_hash_bytes.hex() + coin_id.hex() + AGG_SIG_ME_ADDITIONAL_DATA.hex()`. The wallet, believing it is just "signing an arbitrary message" for message-authentication purposes (CHIP-0002 use case), will happily return `AugSchemeMPL.sign(synthetic_secret_key, that exact byte string)` — which is bit-for-bit a valid `AGG_SIG_ME` authorization for spending the coin owned by that address, under a delegated puzzle chosen entirely by the attacker (any set of conditions, e.g. `CREATE_COIN` to the attacker's own puzzle hash).

This is precisely the ApolloX bug class: a signature scheme that fails to separate signing contexts, letting the attacker manufacture signatures that the system accepts as authorization for a different, more privileged operation (fund movement) than the one the signer believed they were performing.

### Impact Explanation
An attacker able to get the wallet operator to sign a raw hex/utf8 "message" (e.g., through a phishing-style dApp/site that requests message signing "to prove address ownership," a common wallet UX pattern) can extract a valid `AGG_SIG_ME` signature authorizing spend of one of the victim's standard coins to an attacker-controlled puzzle hash — resulting in unauthorized, unsigned-looking-but-technically-signed coin movement/theft, directly reachable via the standard wallet RPC surface with no privileged access. This maps to "concrete unsigned or unauthorized coin movement."

### Likelihood Explanation
The attacker needs the victim's wallet to call `sign_message_by_address`/`sign_message_by_id` with the `BLS_MESSAGE_AUGMENTATION_HEX_INPUT`/`UTF8_INPUT` mode and a message the attacker fully controls (attacker crafts the exact hex bytes, which requires knowing the target coin id and desired delegated puzzle hash — both derivable/public once the coin and desired spend conditions are chosen). This requires some social-engineering/UX trickery to get the raw-mode signature request approved, but is exactly the class of "signing oracle" issue that has caused real fund losses in other wallet ecosystems, and fits the reachable-by-RPC-caller / wallet-user threat model in scope.

### Recommendation
- Apply a domain-separated prefix (equivalent to the CHIP-0002 `("Chia Signed Message", message)` wrapping) unconditionally to all message-signing modes, including the raw `BLS_MESSAGE_AUGMENTATION_HEX_INPUT`/`UTF8_INPUT` paths, so that no user-signed message can ever collide with the `AGG_SIG_ME`/`AGG_SIG_*` byte format used for spend authorization.
- Alternatively/additionally, forbid `sign_message_by_address`/`sign_message_by_id` from using the same key material (`synthetic_secret_key`) as spend-authorizing keys; use a clearly distinct derivation path/key for message signing.
- Reject/flag any message-signing request whose raw bytes end with a known `*_ADDITIONAL_DATA` suffix or otherwise match the `AGG_SIG_*` message shape, similar to the `AGG_SIG_UNSAFE` disallowed-suffix check already implemented in `pkm_pairs_for_conditions_dict`: [6](#0-5) 

### Proof of Concept
1. Attacker learns/derives the target coin's `coin.name()` (public on-chain) belonging to address `A`, and picks a delegated puzzle `P` (e.g., `(q (51 <attacker_ph> <amount>))`) whose `get_tree_hash()` is `DPH`.
2. Attacker computes `raw = DPH + coin.name() + AGG_SIG_ME_ADDITIONAL_DATA` and encodes it as hex.
3. Attacker convinces the wallet owner (e.g., via a "verify your address" dApp flow) to call:
```
sign_message_by_address(address=A, message=raw.hex(), signing_mode=BLS_MESSAGE_AUGMENTATION_HEX_INPUT)
```
4. The RPC returns `signature = AugSchemeMPL.sign(synthetic_secret_key, raw)` — exactly the `AGG_SIG_ME` signature needed.
5. Attacker builds `CoinSpend(coin, puzzle_for_synthetic_pk, solution_for_delegated_puzzle(P, ()))` and submits `SpendBundle([coin_spend], signature)` to the mempool; it passes `AGG_SIG_ME` verification and spends the victim's coin to the attacker's puzzle hash. [1](#0-0) [2](#0-1)

### Citations

**File:** chia/wallet/util/signing.py (L72-85)
```python
def sign_message(secret_key: PrivateKey, message: str, mode: SigningMode) -> SignMessageResponse:
    public_key = secret_key.get_g1()
    if mode == SigningMode.CHIP_0002_HEX_INPUT:
        hex_message: bytes = Program.to((CHIP_0002_SIGN_MESSAGE_PREFIX, bytes.fromhex(message))).get_tree_hash()
    elif mode == SigningMode.BLS_MESSAGE_AUGMENTATION_UTF8_INPUT:
        hex_message = bytes(message, "utf-8")
    elif mode == SigningMode.BLS_MESSAGE_AUGMENTATION_HEX_INPUT:
        hex_message = bytes.fromhex(message)
    else:
        hex_message = Program.to((CHIP_0002_SIGN_MESSAGE_PREFIX, message)).get_tree_hash()
    return SignMessageResponse(
        pubkey=public_key,
        signature=AugSchemeMPL.sign(secret_key, hex_message),
    )
```

**File:** chia/wallet/wallet_rpc_api.py (L1762-1780)
```python
    async def sign_message_by_address(self, request: SignMessageByAddress) -> SignMessageByAddressResponse:
        """
        Given a derived P2 address, sign the message by its private key.
        :param request:
        :return:
        """
        synthetic_secret_key = self.service.wallet_state_manager.main_wallet.convert_secret_key_to_synthetic(
            await self.service.wallet_state_manager.get_private_key(decode_puzzle_hash(request.address))
        )
        signing_response = sign_message(
            secret_key=synthetic_secret_key,
            message=request.message,
            mode=request.signing_mode_enum,
        )
        return SignMessageByAddressResponse(
            pubkey=signing_response.pubkey,
            signature=signing_response.signature,
            signing_mode=request.signing_mode_enum.value,
        )
```

**File:** chia/wallet/wallet.py (L97-98)
```python
    def convert_secret_key_to_synthetic(self, secret_key: PrivateKey) -> PrivateKey:
        return calculate_synthetic_secret_key(secret_key, DEFAULT_HIDDEN_PUZZLE_HASH)
```

**File:** chia/consensus/condition_tools.py (L73-96)
```python
def make_aggsig_final_message(
    opcode: ConditionOpcode,
    msg: bytes,
    spend_conditions: Coin | SpendConditions,
    agg_sig_additional_data: dict[ConditionOpcode, bytes],
) -> bytes:
    if isinstance(spend_conditions, Coin):
        coin = spend_conditions
    elif isinstance(spend_conditions, SpendConditions):
        coin = Coin(spend_conditions.parent_id, spend_conditions.puzzle_hash, uint64(spend_conditions.coin_amount))
    else:
        raise ValueError(f"Expected Coin or Spend, got {type(spend_conditions)}")  # pragma: no cover

    COIN_TO_ADDENDUM_F_LOOKUP: dict[ConditionOpcode, Callable[[Coin], bytes]] = {
        ConditionOpcode.AGG_SIG_PARENT: lambda coin: coin.parent_coin_info,
        ConditionOpcode.AGG_SIG_PUZZLE: lambda coin: coin.puzzle_hash,
        ConditionOpcode.AGG_SIG_AMOUNT: lambda coin: int_to_bytes(coin.amount),
        ConditionOpcode.AGG_SIG_PUZZLE_AMOUNT: lambda coin: coin.puzzle_hash + int_to_bytes(coin.amount),
        ConditionOpcode.AGG_SIG_PARENT_AMOUNT: lambda coin: coin.parent_coin_info + int_to_bytes(coin.amount),
        ConditionOpcode.AGG_SIG_PARENT_PUZZLE: lambda coin: coin.parent_coin_info + coin.puzzle_hash,
        ConditionOpcode.AGG_SIG_ME: lambda coin: coin.name(),
    }
    addendum = COIN_TO_ADDENDUM_F_LOOKUP[opcode](coin)
    return msg + addendum + agg_sig_additional_data[opcode]
```

**File:** chia/consensus/condition_tools.py (L140-145)
```python
    for cwa in conditions_dict.get(ConditionOpcode.AGG_SIG_UNSAFE, []):
        validate_cwa(cwa)
        for disallowed in data.values():
            if cwa.vars[1].endswith(disallowed):
                raise ConsensusError(Err.INVALID_CONDITION)
        ret.append((G1Element.from_bytes(cwa.vars[0]), cwa.vars[1]))
```

**File:** chia/wallet/puzzles/p2_delegated_puzzle_or_hidden_puzzle.py (L22-35)
```python
This is important because it allows sign_coin_spends to function properly via the
following mechanism:

- A 'standard coin' coin exists in the blockchain with some puzzle hash.

- The user's wallet contains a primary sk/pk pair which are used to derive to one
  level a set of auxiliary sk/pk pairs which are used for specific coins. These
  can be used for signing in AGG_SIG_ME, but the standard coin uses a key further
  derived from one of these via calculate_synthetic_secret_key as described in
  https://chialisp.com/docs/standard_transaction. Therefore, when a wallet needs
  to find a secret key for signing based on a public key, it needs to try repeating
  this derivation as well and see if the G1Element (pk) associated with any of the
  derived secret keys matches the pk requested by the coin.

```
