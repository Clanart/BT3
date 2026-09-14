### Title
Unhandled `ValueError` in condition-opcode parsing crashes wallet offer-signing on a malicious offer - ([File: chia/consensus/condition_tools.py])

### Summary
`parse_sexp_to_condition()` converts the raw opcode atom parsed from a puzzle's CLVM output directly into a `ConditionOpcode` enum member via `ConditionOpcode(op)` without validating that the byte value is a known opcode. `ConditionOpcode` is defined with sparse (non-contiguous) byte values, so many single-byte values (e.g. 2, 53-59, 68-69, 77-79, 88-89, 91+) are not members of the enum. Feeding such a value causes a bare Python `ValueError` to be raised, but the only caller-side exception handling, in `conditions_for_solution()`, catches `Program.EvalError` — not `ValueError`. This mirrors the reported bug class: a conversion routine (`TryFrom`-equivalent) panics/raises on attacker-influenced input instead of returning a typed error. [1](#0-0) [2](#0-1) [3](#0-2) 

### Finding Description
`parse_sexp_to_condition()` reads the opcode atom `op = first[0].atom` from a puzzle's output sexp and, after checking only that it's a single byte, constructs `ConditionOpcode(op)`. Because the enum only defines specific byte values (1, 43-52, 60-67, 70-76, 80-87, 90) and Python's `enum.Enum.__call__` raises `ValueError` for any value not present, any puzzle/solution combination whose evaluated output contains an out-of-range single-byte opcode will raise an uncaught `ValueError` inside `parse_sexp_to_conditions()`. [4](#0-3) 

The only caller in this module, `conditions_for_solution()`, wraps the CLVM execution and condition parsing in a `try/except Program.EvalError`, which does not catch `ValueError`: [2](#0-1) 

This function is used by `conditions_dict_for_solution()`, which is invoked from `WalletSigner.gather_signing_info()` on every spend being signed, including spends coming from an untrusted counterparty's offer: [5](#0-4) 

That method is reached from `gather_signing_info_for_trades(offers)` → `sign_offers(offers)`, which the wallet calls when a user signs/takes an `Offer` object built from an attacker-supplied `WalletSpendBundle` (`offer._bundle`): [6](#0-5) [7](#0-6) 

A malicious offer author only needs to craft a `puzzle_reveal`/`solution` pair (using a custom inner puzzle attached to the offered coin) whose CLVM execution emits a condition list containing an opcode byte outside the enum's defined set. Because `Offer` accepts arbitrary CoinSpend puzzle reveals from the counterparty side (that's the entire point of an offer file), this condition is fully attacker-controlled.

### Impact Explanation
When a wallet user attempts to sign or take such a maliciously crafted offer, `WalletSigner.gather_signing_info()` raises an unhandled `ValueError` while trying to extract AGG_SIG conditions for signing. This crashes/aborts the offer-signing RPC call, denying the honest party the ability to process that specific offer (and, depending on how the RPC layer handles unexpected exceptions, potentially disrupting the wallet service call). This is a spend-triggered halt in wallet transaction processing reachable purely by delivering a malformed offer file to a counterparty — no privileged access needed.

### Likelihood Explanation
Likelihood is high for triggering the crash: constructing a coin with a simple custom puzzle that outputs a `(CREATE_COIN ...)`-shaped list with an unused opcode byte (e.g., byte value `2` or `91`) is trivial CLVM authoring, and offers are explicitly designed to carry arbitrary counterparty-supplied puzzle reveals/solutions. Any wallet user who opens/signs an offer sent by an attacker hits this path.

### Recommendation
In `parse_sexp_to_condition()` (chia/consensus/condition_tools.py), validate the opcode byte against the enum before construction and raise a `ConsensusError`/typed error for unknown byte values instead of letting `ConditionOpcode(op)` raise a bare `ValueError`, e.g.:
```python
try:
    opcode = ConditionOpcode(op)
except ValueError:
    raise ConsensusError(Err.INVALID_CONDITION, ["unknown opcode"])
```
Additionally, widen the `except` clause in `conditions_for_solution()` to also catch `ValueError`/`ConsensusError` from the parsing step, or ensure all conversion call sites (`WalletSigner.gather_signing_info`, `cat_utils.unsigned_spend_bundle_for_spendable_cats`, offer trade flows) wrap condition parsing so a single malformed spend cannot propagate an unhandled exception out of signing/offer-processing code.

### Proof of Concept
1. Attacker crafts a coin with a puzzle that, given any solution, evaluates to a CLVM list such as `((2 0xAA 0xBB))` — i.e., a condition whose opcode atom is the single byte `2`, which is not defined in `ConditionOpcode`.
2. Attacker builds a `WalletSpendBundle`/`Offer` containing a `CoinSpend` with this puzzle reveal and any solution, and sends the resulting offer file to a victim.
3. The victim's wallet calls `WalletSigner.sign_offers([offer])` (or equivalently `gather_signing_info_for_trades`) to sign/accept the offer.
4. Internally this calls `conditions_dict_for_solution(puzzle_reveal, solution, max_cost)` → `conditions_for_solution()` → `parse_sexp_to_conditions()` → `parse_sexp_to_condition()`.
5. `ConditionOpcode(b"\x02")` raises `ValueError: 2 is not a valid ConditionOpcode`, which is not caught by the `except Program.EvalError` in `conditions_for_solution()`, propagating out of `gather_signing_info` and aborting the signing call — denial of service for that offer-processing request.

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

**File:** chia/types/condition_opcodes.py (L7-73)
```python
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

**File:** chia/wallet/wallet_signer.py (L137-138)
```python
    async def gather_signing_info_for_trades(self, offers: list[Offer]) -> list[UnsignedTransaction]:
        return await self.gather_signing_info_for_bundles([offer._bundle for offer in offers])
```

**File:** chia/wallet/wallet_signer.py (L310-331)
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
