### Title
Unhandled `ValueError` on unknown condition opcode during offer/spend signing halts wallet transaction processing - ([File: chia/consensus/condition_tools.py])

### Summary
`parse_sexp_to_condition()` constructs `ConditionOpcode(op)` from a single, attacker-supplied byte produced by executing a counterparty-controlled puzzle/solution. If that byte does not match any defined `ConditionOpcode` enum member, the `bytes, enum.Enum` constructor raises a bare `ValueError`, which is not caught anywhere in the call chain used during wallet-side offer/spend-bundle signing. This mirrors the CVE-2018-10774 bug class: a parser's keyword/opcode lookup crashing on attacker-controlled input during processing of externally supplied data, causing a denial of service.

### Finding Description
`conditions_for_solution()` executes the puzzle reveal against the solution and feeds the resulting sexp into `parse_sexp_to_conditions()`, which calls `parse_sexp_to_condition()` per top-level output value: [1](#0-0) 

The only exception this function's caller catches is `Program.EvalError`: [2](#0-1) 

`ConditionOpcode` is a `bytes`-backed `enum.Enum` with only a fixed set of legal single-byte values (1, 43-50, 51-52, 60-67, 70-76, 80-87, 90): [3](#0-2) 

`ConditionOpcode(op)` with any other single byte (e.g. any of the many unassigned byte values) raises `ValueError: <byte> is not a valid ConditionOpcode`, not `Program.EvalError`. This exception is uncaught by `conditions_for_solution()` and propagates up through `conditions_dict_for_solution()` into `WalletSigner.gather_signing_info()`: [4](#0-3) 

That path is reachable through `sign_offers()` (triggered when a wallet signs/accepts an offer built from a counterparty-supplied `Offer._bundle`) and through `sign_transactions()`/`sign_bundle()` more generally: [5](#0-4) [6](#0-5) 

An offer counterparty (or any wallet-action-supplied spend) that includes a coin whose puzzle output includes a condition list item using a single unassigned byte as the "opcode" atom (with the atom's length exactly 1, so it passes the `len(op) != 1` guard) will cause `ConditionOpcode(op)` to raise an unhandled `ValueError` instead of the expected `ConsensusError`. Because this is the local, non-consensus condition parser used specifically for signing (separate from the Rust-backed `SpendBundleConditions` consensus path), it is exercised whenever a wallet inspects a spend's AGG_SIG-relevant conditions before signing.

### Impact Explanation
An uncaught `ValueError` propagating out of `gather_signing_info()`/`sign_offers()`/`sign_bundle()` aborts the signing operation with an unexpected exception rather than a handled `ConsensusError`/`INVALID_CONDITION` rejection. Depending on the calling context (RPC handler, offer-take flow, CLI wallet command), this manifests as an unhandled exception bubbling to the wallet RPC/service layer, disrupting the in-progress transaction-processing operation for that call — a spend-triggered processing halt reachable purely by supplying a malicious offer/spend with a crafted puzzle output, without needing the coins to ever be pushed to the mempool or accepted by consensus.

### Likelihood Explanation
Likelihood is Medium: no special privileges are needed — an offer counterparty (or a locally-authored spend) only needs to construct a puzzle whose CLVM output includes a single-byte "opcode" atom outside the defined `ConditionOpcode` set at the position expected by `parse_sexp_to_condition`. Building custom puzzles that emit arbitrary condition-shaped lists is straightforward in CLVM and requires no signature or on-chain commitment prior to triggering the wallet's local signing/inspection path.

### Recommendation
Wrap the `ConditionOpcode(op)` construction in `parse_sexp_to_condition()` in a `try/except ValueError` and re-raise as `ConsensusError(Err.INVALID_CONDITION, ...)` (consistent with the other validation branches in the same function), or catch `ValueError` alongside `Program.EvalError` in `conditions_for_solution()` so malformed/unknown opcodes fail gracefully instead of raising an unhandled exception during signing.

### Proof of Concept
1. Construct a puzzle that, given any solution, outputs `((0x02 <arg1> <arg2>))` — i.e., a list whose first element's `f` atom is a single byte (e.g. `0x02`) that is not one of the values defined in `ConditionOpcode` (`chia/types/condition_opcodes.py`).
2. Build a `CoinSpend`/`Offer` using this puzzle reveal and a satisfying solution; embed it as a coin spend inside a `WalletSpendBundle`/`Offer` presented to the victim wallet (e.g., via `take_offer`/`sign_offers`).
3. When the victim wallet calls `WalletSigner.gather_signing_info()` (via `sign_offers()`/`sign_bundle()`/`sign_transactions()`), it calls `conditions_dict_for_solution()` → `conditions_for_solution()` → `parse_sexp_to_conditions()` → `parse_sexp_to_condition()`, which executes `ConditionOpcode(0x02)`.
4. This raises `ValueError: b'\x02' is not a valid ConditionOpcode`, uncaught by the `except Program.EvalError` handler, propagating out of the signing call and aborting the wallet's transaction-processing operation for that request.

### Citations

**File:** chia/consensus/condition_tools.py (L19-45)
```python
def parse_sexp_to_condition(sexp: Program) -> ConditionWithArgs:
    """
    Takes a ChiaLisp sexp and returns a ConditionWithArgs.
    Raises an ConsensusError if it fails.
    """
    first = sexp.pair
    if first is None:
        raise ConsensusError(Err.INVALID_CONDITION, ["first is None"])
    op = first[0].atom
    if op is None or len(op) != 1:
        raise ConsensusError(Err.INVALID_CONDITION, ["invalid op"])

    # since the ConditionWithArgs only has atoms as the args, we can't parse
    # hints and memos with this function. We just exit the loop if we encounter
    # a pair instead of an atom
    vars: list[bytes] = []
    for arg in Program(first[1]).as_iter():
        a = arg.atom
        if a is None:
            break
        vars.append(a)
        # no condition (currently) has more than 3 arguments. Additional
        # arguments are allowed but ignored
        if len(vars) > 3:
            break

    return ConditionWithArgs(ConditionOpcode(op), vars)
```

**File:** chia/consensus/condition_tools.py (L172-180)
```python
def conditions_for_solution(
    puzzle_reveal: Program | SerializedProgram, solution: Program | SerializedProgram, max_cost: int
) -> list[ConditionWithArgs]:
    # get the standard script for a puzzle hash and feed in the solution
    try:
        _cost, r = run_with_cost(puzzle_reveal, max_cost, solution)
        return parse_sexp_to_conditions(r)
    except Program.EvalError as e:
        raise ConsensusError(Err.SEXP_ERROR, [str(e)]) from e
```

**File:** chia/types/condition_opcodes.py (L1-73)
```python
from __future__ import annotations

import enum


# See chia/wallet/puzzles/condition_codes.clib
class ConditionOpcode(bytes, enum.Enum):
    # AGG_SIG is ascii "1"

    # the conditions below require bls12-381 signatures

    AGG_SIG_PARENT = bytes([43])
    AGG_SIG_PUZZLE = bytes([44])
    AGG_SIG_AMOUNT = bytes([45])
    AGG_SIG_PUZZLE_AMOUNT = bytes([46])
    AGG_SIG_PARENT_AMOUNT = bytes([47])
    AGG_SIG_PARENT_PUZZLE = bytes([48])
    AGG_SIG_UNSAFE = bytes([49])
    AGG_SIG_ME = bytes([50])

    # the conditions below reserve coin amounts and have to be accounted for in output totals

    CREATE_COIN = bytes([51])
    RESERVE_FEE = bytes([52])

    # the conditions below deal with announcements, for inter-coin communication

    CREATE_COIN_ANNOUNCEMENT = bytes([60])
    ASSERT_COIN_ANNOUNCEMENT = bytes([61])
    CREATE_PUZZLE_ANNOUNCEMENT = bytes([62])
    ASSERT_PUZZLE_ANNOUNCEMENT = bytes([63])
    ASSERT_CONCURRENT_SPEND = bytes([64])
    ASSERT_CONCURRENT_PUZZLE = bytes([65])

    # new message conditions in softfork introduced in Chia 2.3

    SEND_MESSAGE = bytes([66])
    RECEIVE_MESSAGE = bytes([67])

    # the conditions below let coins inquire about themselves

    ASSERT_MY_COIN_ID = bytes([70])
    ASSERT_MY_PARENT_ID = bytes([71])
    ASSERT_MY_PUZZLEHASH = bytes([72])
    ASSERT_MY_AMOUNT = bytes([73])
    ASSERT_MY_BIRTH_SECONDS = bytes([74])
    ASSERT_MY_BIRTH_HEIGHT = bytes([75])
    ASSERT_EPHEMERAL = bytes([76])

    # the conditions below ensure that we're "far enough" in the future

    # wall-clock time
    ASSERT_SECONDS_RELATIVE = bytes([80])
    ASSERT_SECONDS_ABSOLUTE = bytes([81])

    # block index
    ASSERT_HEIGHT_RELATIVE = bytes([82])
    ASSERT_HEIGHT_ABSOLUTE = bytes([83])

    # wall-clock time
    ASSERT_BEFORE_SECONDS_RELATIVE = bytes([84])
    ASSERT_BEFORE_SECONDS_ABSOLUTE = bytes([85])

    # block index
    ASSERT_BEFORE_HEIGHT_RELATIVE = bytes([86])
    ASSERT_BEFORE_HEIGHT_ABSOLUTE = bytes([87])

    # to be activated with the 2.0 hard fork.
    # the first parameter is always the cost of the condition
    SOFTFORK = bytes([90])

    # A condition that is always true and always ignore all arguments
    REMARK = bytes([1])
```

**File:** chia/wallet/wallet_signer.py (L94-117)
```python
    async def gather_signing_info(self, spends: list[Spend]) -> SigningInstructions:
        pks: list[bytes] = []
        signing_targets: list[SigningTarget] = []
        for spend in spends:
            coin_spend = spend.as_coin_spend()
            # Get AGG_SIG conditions
            conditions_dict = conditions_dict_for_solution(
                coin_spend.puzzle_reveal,
                coin_spend.solution,
                self.max_block_cost_clvm,
            )
            # Create signature
            for pk, msg in pkm_pairs_for_conditions_dict(
                conditions_dict, coin_spend.coin, self.agg_sig_me_additional_data
            ):
                pk_bytes = bytes(pk)
                pks.append(pk_bytes)
                fingerprint: bytes = pk.get_fingerprint().to_bytes(4, "big")
                signing_targets.append(SigningTarget(fingerprint, msg, std_hash(pk_bytes + msg)))

        return SigningInstructions(
            await self.key_hints_for_pubkeys(pks),
            signing_targets,
        )
```

**File:** chia/wallet/wallet_signer.py (L119-138)
```python
    async def gather_signing_info_for_bundles(self, bundles: list[WalletSpendBundle]) -> list[UnsignedTransaction]:
        utxs: list[UnsignedTransaction] = []
        for bundle in bundles:
            signer_protocol_spends: list[Spend] = [Spend.from_coin_spend(spend) for spend in bundle.coin_spends]
            utxs.append(
                UnsignedTransaction(
                    TransactionInfo(signer_protocol_spends),
                    await self.gather_signing_info(signer_protocol_spends),
                )
            )

        return utxs

    async def gather_signing_info_for_txs(self, txs: list[TransactionRecord]) -> list[UnsignedTransaction]:
        return await self.gather_signing_info_for_bundles(
            [tx.spend_bundle for tx in txs if tx.spend_bundle is not None]
        )

    async def gather_signing_info_for_trades(self, offers: list[Offer]) -> list[UnsignedTransaction]:
        return await self.gather_signing_info_for_bundles([offer._bundle for offer in offers])
```

**File:** chia/wallet/wallet_signer.py (L310-332)
```python
    async def sign_offers(
        self,
        offers: list[Offer],
        additional_signing_responses: list[SigningResponse] = [],
        partial_allowed: bool = False,
    ) -> tuple[list[Offer], list[SigningResponse]]:
        unsigned_txs: list[UnsignedTransaction] = await self.gather_signing_info_for_trades(offers)
        new_offers: list[Offer] = []
        all_signing_responses = additional_signing_responses.copy()
        for unsigned_tx, offer in zip(unsigned_txs, [offer for offer in offers]):
            signing_responses: list[SigningResponse] = await self.execute_signing_instructions(
                unsigned_tx.signing_instructions, partial_allowed=partial_allowed
            )
            all_signing_responses.extend(signing_responses)
            new_bundle = self.signed_tx_to_spendbundle(
                await self.apply_signatures(
                    unsigned_tx.transaction_info.spends,
                    [*additional_signing_responses, *signing_responses],
                )
            )
            new_offers.append(Offer(offer.requested_payments, new_bundle, offer.driver_dict))
        return new_offers, all_signing_responses

```
