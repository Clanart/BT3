import json
import os

from decouple import config

# todo: if scope_files is: 500 > 50, 300 > 30 , 100 > 10
MAX_REPO = 20
# todo: the GitLab namespace/project path, for example group/project
SOURCE_REPO = 'Zest-Protocol/zest-v2-contracts'
# todo: the name of the repository
REPO_NAME = 'zest-v2-contracts'

run_number = os.environ.get('GITHUB_RUN_NUMBER', '0')


def get_cyclic_index(run_number, max_index=100):
    """Convert run number to a cyclic index between 1 and max_index"""
    return (int(run_number) - 1) % max_index + 1


def load_repository_urls():
    """Load repository URLs from repositories.json."""
    repo_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "repositories.json")
    if not os.path.exists(repo_file):
        return []

    try:
        with open(repo_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return []

    if not isinstance(data, list):
        return []

    return [url for url in data if isinstance(url, str) and url.strip()]


if run_number == "0":
    BASE_URL = f"https://deepwiki.com/{SOURCE_REPO}"
else:
    repository_urls = load_repository_urls()
    if repository_urls:
        run_index = get_cyclic_index(run_number, len(repository_urls))
        BASE_URL = repository_urls[run_index - 1]
    else:
        BASE_URL = f"https://deepwiki.com/{SOURCE_REPO}"

scope_files = [
    # =================================================================================
    # LENS: PRICE, RISK PARAMETERS AND THE HEALTH VERDICT.
    #
    # One number decides everything in this protocol: the USD value of a position. This
    # variant audits only the pipeline that produces it and the comparison that consumes
    # it - raw feed -> `normalize-pyth` or DIA -> confidence and staleness gates ->
    # callcode transform -> `normalize` by per-asset decimals -> notional totals ->
    # `is-healthy` against an efficiency-group LTV -> the liquidation thresholds. Nothing
    # about custody, nothing about accounting; if a question does not end at a price or a
    # health verdict, it belongs to another variant.
    #
    # PROGRAM BOUNDARIES: incorrect data supplied by a third-party oracle is OUT OF SCOPE -
    # the defect must be in how THIS code consumes, transforms, gates or compares the
    # value. Oracle manipulation caused by a bug here remains in scope. The registries are
    # in scope only for their read paths; assume the DAO configured them correctly.
    # Flashloan logic and liquidation of disabled collateral are out of scope entirely.
    # =================================================================================

    # -- The whole pricing and health pipeline ------------------------------------------
    # write-feeds, resolve-pyth / resolve-dia, normalize-pyth, check-confidence,
    # oracle-timestamp-fresh, resolve-callcode / resolve-ststx / resolve-ztoken,
    # price-resolve, price-multi-resolve, merge-price, calculate-asset-notional-value,
    # normalize, is-healthy, is-healthy-with-mask, and the liquidation threshold math.
    "mainnet/contracts/market/v0-4-market.clar",

    # -- Which assets get priced at all, and with what decimals and oracle record --------
    # READ PATHS ONLY: lookup, find, status, status-multi, get-bitmap, mask-pos, subset,
    # uint-to-list-u64. `decimals` is captured once at registration and multiplies into
    # every USD figure the protocol ever computes.
    "mainnet/contracts/registry/v0-assets.clar",

    # -- Which LTV a resolved position is judged against ---------------------------------
    # READ PATHS ONLY: resolve, active, find-superset, iter-find-superset, population,
    # filter-u128, and buff-to-uint-be over the stored LTV-BORROW / LTV-LIQ-PARTIAL /
    # LTV-LIQ-FULL buffers. The lookup must be wrong, not the configuration.
    "mainnet/contracts/registry/v0-egroup.clar",

    # -- The mask that decides which rows enter the notional fold ------------------------
    "mainnet/contracts/market/v0-market-vault.clar",

    # -- The two vaults whose state feeds the price transforms ---------------------------
    # v0-vault-ststx backs CALLCODE-STSTX and the compound CALLCODE-ZSTSTX; its `lindex` is
    # the multiplier `resolve-ztoken` applies. v0-vault-sbtc is the 8-decimal asset that
    # breaks any implicit assumption that all assets normalize alike.
    "mainnet/contracts/vault/v0-vault-ststx.clar",
    "mainnet/contracts/vault/v0-vault-sbtc.clar",
]


target_scopes = [
    "Critical. `normalize-pyth` MISHANDLES SIGN. It computes `adj` as `(+ expo 8)`, uses an `asserts!` bound as an early return when `adj` is zero, and converts the raw `int` price with `to-uint`. Establish what happens for a negative price, for a large negative or positive exponent, and for the exponent that makes the adjustment zero, and show a feed value this code turns into a wildly wrong uint that `oracle-price-legal` still accepts because it is merely greater than zero. Impact: protocol insolvency through borrowing against a mispriced asset.",

    "Critical. THE CONFIDENCE GATE DOES NOT COVER EVERY FEED. `check-confidence` compares the Pyth confidence interval against `max-confidence-ratio` in BPS, but `resolve-dia` produces a price with no confidence concept at all, and `resolve-callcode` transforms the value AFTER the gate was applied to the raw one. Show a resolved price that passes every check while its true uncertainty, or its post-transform magnitude, is far outside what `max-confidence-ratio` was meant to bound. Impact: protocol insolvency.",

    "Critical. FRESHNESS IS DECIDED BY A DELTA THAT CAN BE ZERO. `oracle-timestamp-fresh` sets `delta` to `u0` whenever `ts` exceeds `stacks-block-time`, then requires `(<= delta max-staleness)` and `(>= ts prev)`. A timestamp in the future is therefore maximally fresh. Combined with `price-resolve` advancing the per-key `last-update` only when the new timestamp is greater, show a feed state that permanently pins `last-update` high enough to reject every subsequent legitimate update, or one that accepts an arbitrarily old price. Impact: protocol insolvency, or permanent freezing of every position priced by that feed.",

    "Critical. ONE `last-update` KEY, MANY ASSETS. The market stores freshness per `{{ type, ident }}` pair, while `max-staleness` is a per-asset field of the registry record. Two assets configured against the same feed ident - a token and its ztoken, or two wrappers of one underlying - share one monotonic timestamp but enforce different staleness. Show the stricter asset's guard being satisfied by an update performed for the looser one, or the looser asset advancing `last-update` past what the stricter one can ever match again. Impact: insolvency, or permanent freezing of funds.",

    "Critical. `write-feeds` FOLDS FAILURE INTO A STATUS. `write-feeds` accepts up to three attacker-supplied buffers and folds `write-feed` over them with a `(response bool uint)` accumulator. Determine exactly which failures abort the transaction and which are absorbed, then show a submission in which one feed updates and another silently does not, so the subsequent multi-asset health evaluation mixes a fresh price for one leg with a stale price for the other. Impact: protocol insolvency.",

    "Critical. `resolve-ztoken` DEPENDS ON A CACHE THE CALLER CAN SHAPE. It reads `lindex` from `get-cached-indexes` - the market's own `index-cache`, not the vault - and multiplies the underlying price by it before dividing by `INDEX-PRECISION`. Establish, for every entry point, whether that cache entry was primed from a genuinely accrued vault state, from a state the same transaction has already mutated, or not at all. Show a rehypothecated collateral valuation derived from an index that does not reflect the vault at the moment of the health check. Impact: protocol insolvency.",

    "Critical. THE COMPOUND CALLCODE ROUNDS TWICE AND OVERFLOWS ONCE. `CALLCODE-ZSTSTX` evaluates `resolve-ztoken` over the output of `resolve-ststx`, chaining `mul-div-down` by an external ratio scaled by `STSTX-RATIO-DECIMALS` with a multiplication by `cached-lindex` and a division by `INDEX-PRECISION`. Show the input range where the two round-downs compound into a materially wrong value, or where the intermediate product is large enough to abort, and show which side of the health check that error favours. Impact: insolvency, or temporary freezing of every operation on that asset.",

    "Critical. `resolve-ststx` PUTS AN EXTERNAL CALL INSIDE EVERY HEALTH CHECK. `call-ststx-ratio` is invoked during price resolution, and its failure is mapped to `ERR-ORACLE-CALLCODE`. Establish which user operations depend on that call succeeding, and show that a ratio value at a boundary, or a call that fails, either blocks liquidation of an unhealthy position or blocks a healthy user from withdrawing. Do not premise this on the external contract publishing wrong data; premise it on how this code handles the value and the failure. Impact: permanent or temporary freezing of funds.",

    "Critical. PRICES ARE ZIPPED BACK ONTO ASSETS BY POSITION. `price-multi-resolve` folds `iter-price-multi` over the oracle records to build a positional list of prices, carrying `aids` and `idx` in the accumulator without ever using them to align, and the result is attached to asset entries by `merge-price` in `get-assets`. Show a position whose asset list and oracle list can differ in length or order - a skipped entry, an early `valid: false`, a truncation at the 64 bound - so one asset is valued at another asset's price. Impact: direct theft of user funds, or protocol insolvency.",

    "Critical. THE ROUND-UP AND ROUND-DOWN FLAGS ARE PASSED BY HAND AT EVERY CALL SITE. `normalize` takes a boolean, and every caller chooses: `calculate-asset-notional-value` rounds collateral down and debt up, `find-and-resolve-asset-value` and `get-asset-value` take the flag from their caller, `process-debt-asset` rounds down, `calc-final-liquidation-amounts` rounds collateral-actual down. Enumerate all of them and find the one that rounds in the user's favour on a path where conservatism was required. Impact: protocol insolvency, or direct theft from the borrower during liquidation.",

    "Critical. THE PROTOCOL'S USD UNIT IS A WHOLE DOLLAR. `normalize` divides by `(pow u10 decimals)` only after multiplying amount by price, so every notional is an integer number of dollars. For an 8-decimal asset against a 6-decimal one, find the amount-and-price pairs where a real collateral holding normalizes to zero USD while remaining seizable, where a real debt normalizes to zero and passes `is-healthy` for free, or where the collateral round-down and debt round-up together open a persistent free-borrow window. Impact: protocol insolvency.",

    "Critical. `is-healthy` COMPARES UNNORMALIZED PRODUCTS AND SHORT-CIRCUITS ON ZERO. It returns true whenever `debt-usd` is zero, and otherwise compares `(* debt-usd BPS)` against `(* collateral-usd ltv)`. Show a debt that reaches the comparison as zero after normalization while real debt exists, and separately establish the magnitudes at which either product aborts on overflow - and what aborting means for a user trying to withdraw or a liquidator trying to seize. Impact: protocol insolvency, or permanent freezing of funds.",

    "Critical. THE FUTURE MASK IS NOT THE MASK THAT WILL EXIST. `borrow` computes `future-mask` by setting the bit at `(+ asset-id DEBT-OFFSET)` and validates against `is-healthy-with-mask`; `collateral-remove` computes a future mask by clearing a collateral bit when `removing-all`; `collateral-add` compares `future-capacity` against `current-capacity` using raw `(* coll-usd ltv)` products. Show a case where the mask actually written by market-vault after the call differs from the one the check validated - because a row hit zero, because a bit was already set, or because the check used the enabled-filtered mask - and land in an egroup that was never approved for the resulting position. Impact: protocol insolvency.",

    "Critical. THE PER-EGROUP BORROW BLOCK IS TESTED AT THE WRONG BIT. `borrow` asserts `(is-eq (bit-and disabled-borrow-mask (pow u2 asset-id)) u0)` against the FUTURE egroup's `BORROW-DISABLED-MASK`, using the bare `asset-id` while every debt bit elsewhere in the protocol is offset by `DEBT-OFFSET`. Establish which convention the stored mask actually uses, and show a borrow that is permitted because the test looked at a bit meaning something else, or blocked because it looked at a collateral bit. Impact: protocol insolvency, or temporary freezing of a legitimate borrow.",

    "Critical. THE THREE LTVs ARE READ AS BUFFERS AND USED WITHOUT ORDERING CHECKS. `LTV-BORROW`, `LTV-LIQ-PARTIAL` and `LTV-LIQ-FULL` are stored as buffers and converted at every use by `buff-to-uint-be`, then `calc-liq-factor` computes `(- ltv-curr ltv-liq-partial)` over `(- ltv-liq-full ltv-liq-partial)`. Working only from a correctly configured registry, show what the consuming code does when `ltv-curr` is below `ltv-liq-partial` (a subtraction that aborts), when the two liquidation thresholds are equal (a division), or when the buffer width differs from what `buff-to-uint-be` expects. Impact: permanent freezing of an unliquidatable position, hence insolvency.",

    "Critical. A POSITION THAT NO EGROUP RESOLVES IS A POSITION NOBODY CAN CLOSE. `get-egroup` wraps `resolve`, and every health path unwraps it with `try!`. Establish which reachable masks return no group - a combination assembled through a sequence of individually permitted actions, a mask left behind by a row that hit zero without its bit clearing, a debt-only or collateral-only mask - and show a user or a liquidator who can no longer act because the position cannot be priced at all. Impact: permanent freezing of funds.",

    "High. `find-superset` RETURNS THE FIRST MATCH, NOT THE TIGHTEST. `resolve` walks `active`, `find-superset` and `iter-find-superset` over `buckets` ordered by `population`, returning the first mask that is a superset of the position. With a correctly configured registry containing several overlapping groups, show a position whose resolved LTVs are looser than those of the smallest group that covers it, and borrow the difference. The bug must be in the search order, not in the stored data. Impact: protocol insolvency.",

    "High. THE ENABLED BITMAP DECIDES WHICH ROWS ARE EVEN PRICED. `get-notional-evaluation` folds `calculate-asset-notional-value` over the ASSET list derived from the enabled mask, looking each asset up in the position's collateral and debt lists - so a position row whose asset is not in that list contributes nothing to either total. For collateral that is conservative; for debt it is not. Show a live debt row omitted from `debt-total`, and the health verdict that follows. Impact: protocol insolvency.",

    "High. THE DEBT LEG IS PRICED FROM A CACHE INSIDE THE NOTIONAL FOLD. `calculate-asset-notional-value` calls `accrue-and-cache` with `unwrap-panic` in the middle of the fold to convert scaled debt at the borrow index before normalizing with round-up. Establish what happens when that asset was never primed, when the fold is entered from a path that primed a different set, and whether the index used for the debt leg is the same one used moments later by `convert-to-scaled-debt` or `scale-debt-for-liquidation`. Impact: insolvency, or a health check that aborts and freezes the position.",

    "High. THE LIQUIDATION CURVE IS INTEGER MATH PRETENDING TO BE CONTINUOUS. `calc-liq-factor-exp` uses `(/ exp BPS)` as an integer exponent for `pow`, divides by `(pow BPS (- (/ exp BPS) u1))`, and falls back to `sqrti` for exponents below BPS; `calc-liq-factor-bound` then scales the penalty between bounds. Show the exponent and factor values where this returns zero, saturates at BPS, or aborts, and what that does to how much a borrower loses - or to whether the position can be liquidated at all. Impact: direct theft from the borrower, or insolvency through an unliquidatable position.",

    "High. THE SAME PRICE IS ASKED TO DO THREE DIFFERENT JOBS. One resolved number sets borrowing capacity, triggers the liquidation threshold, and sizes the seizure, and each of those wants a different direction of conservatism. Trace a single price through `get-notional-evaluation`, `is-healthy`, `process-debt-asset` and `process-collateral-asset` in one liquidation, and show a value at which the position is judged unhealthy by one use and the seizure is sized by another in a way that takes more from the borrower than the shortfall justifies. Impact: direct theft of user funds.",

    "Critical. PRICED TWICE IN ONE TRANSACTION - the seam nobody modelled. Several entry points resolve a price more than once in a single call, from different inputs and at different points in the state: `collateral-remove` resolves the removed asset separately from the position fold; `liquidate` prices the debt leg, the collateral leg, and any socialized remainder in sequence while the vault state moves underneath; `collateral-add` prices the added asset with `get-asset-value` after the position was already valued with `find-and-resolve-asset-value`. Enumerate every transaction in which two price resolutions of the SAME asset can disagree, determine which one the safety check used and which one moved the money, and prove the gap with a single simnet transaction that asserts the two resolved values differ. Impact: name it as direct theft, permanent freezing, or protocol insolvency.",
]


scope_scan = [
]


def question_generator(target_file: str) -> str:
    """
    Generate pricing- and health-focused audit questions for one Zest v2 target.

    ```
    target_file format:
    "'File Name: mainnet/contracts/market/v0-4-market.clar -> Scope: Critical. ...'"
    """

    prompt = f"""
    ```

    Generate pricing and health-verdict security audit questions for this exact Zest Protocol v2 target:

    {target_file}

    Project focus:
    Every decision Zest v2 makes reduces to one number: the USD value of a position. This audit
    covers only the pipeline that produces it. A Pyth or DIA feed is read by `resolve-pyth` or
    `resolve-dia`, converted by `normalize-pyth` from an `int` price and an exponent, gated by
    `check-confidence` against `max-confidence-ratio` and by `oracle-timestamp-fresh` against a
    per-feed monotonic `last-update` and the asset's `max-staleness`, then transformed by
    `resolve-callcode` - `resolve-ststx` multiplies by an external ratio, `resolve-ztoken`
    multiplies by a `lindex` read from the market's own per-block `index-cache`, and
    `CALLCODE-ZSTSTX` composes both. `price-multi-resolve` builds a positional list that
    `merge-price` attaches to asset records from v0-assets, each carrying `decimals` captured at
    registration. `calculate-asset-notional-value` normalizes collateral down and debt up into
    whole-dollar totals, and `is-healthy` compares them against an LTV that v0-egroup's `resolve`
    selected for the position's 128-bit mask. The liquidation thresholds and the graduated
    penalty curve read the same parameters.

    Rules:
    * Treat `File Name:` as the exact contract.
    * Treat `Scope:` as the ONLY impact to target.
    * Assume full repo context is accessible.
    * Do not ask for code or say anything is missing.
    * Use exact Clarity symbols (define-public/private/read-only names, map, data-var, constant).
    * Every question must end at a PRICE or a HEALTH VERDICT that is wrong, and must say in which
      direction it is wrong and who benefits. Questions about custody, share accounting or
      authorization belong to other batches and must not be generated here.
    * Incorrect data published by a real third-party oracle is OUT OF SCOPE. The defect must be in
      how this code consumes, transforms, gates, aligns or compares the value. Oracle manipulation
      caused by a bug in this code IS in scope.
    * Assume v0-assets and v0-egroup are correctly configured by the DAO. Target only their read
      and resolution paths - `status`, `status-multi`, `lookup`, `get-bitmap`, `resolve`, `active`,
      `find-superset`, `population`, `buff-to-uint-be` over the stored LTV buffers.
    * Attacker is unprivileged only: an ordinary Stacks principal that funds a wallet, calls any
      public function, deploys its own Clarity contract, passes it as `<ft-trait>`, supplies its
      own `price-feeds` buffers, and chooses amounts and call ordering within a block.
    * Attacker is NOT a DAO signer, executor, market impl, authorized contract, miner, oracle
      publisher or node operator. Ignore malicious-miner, chain-reorg, MEV-only and
      social-engineering assumptions.
    * PROGRAM EXCLUSIONS - a question landing in any of these wastes the whole batch:
      - ANY logic related to flashloans is OUT OF SCOPE. A flashloan may be used as a source of
        capital for a different attack, but never target `flashloan` itself, its fee, its
        `flashloan-permissions` / `default-flashloan-permissions` whitelist, or `in-flashloan`.
      - Liquidation of disabled collateral, and any other deliberate protocol safety design
        decision, is OUT OF SCOPE.
      - Anything requiring DAO compromise, or an accidental or incorrect registry update by the
        DAO, is OUT OF SCOPE. Full DAO control of the asset and egroup registries is intended
        design, and every egroup invariant needing global market and position knowledge is
        verified off-chain by the DAO before approval. Assume both registries are correctly
        configured, and target only the read and resolution paths an ordinary user call executes.
      - Also excluded everywhere: leaked keys or credentials, privileged addresses, external
        stablecoin depegs the attacker did not cause through a bug in this code, 51% and basic
        economic or governance attacks, Sybil attacks, centralization risk, lack of liquidity,
        incorrect data supplied by third-party oracles, best-practice notes, feature requests,
        and test or configuration files.
      - Oracle manipulation caused by a bug in THIS code remains fully in scope.
    * IN-SCOPE IMPACTS - every question must land on one and name it:
      Critical: direct theft of user funds at rest or in motion, other than unclaimed yield;
      permanent freezing of funds; protocol insolvency.
      High: theft of unclaimed yield or royalties; permanent freezing of unclaimed yield or
      royalties; temporary freezing of funds.
    * Every question must be a concrete real-world scenario on mainnet, with the specific amounts,
      decimals, exponents or index values that trigger it. No speculative unbounded-list, memory
      or resource-hygiene questions.
    * Clarity `+` `-` `*` abort on overflow and underflow. In this pipeline an abort is a real
      finding: a price path that aborts makes a position unpriceable, therefore unwithdrawable and
      unliquidatable - say which.
    * Generate 30 to 40 high-signal questions.
    * At least 70% must land on a Critical impact rather than a High one.
    * Every question must be testable by a Clarinet / vitest simnet test in `local-testing/tests`
      on a local fork, driving a price or an index to a specific value. Never propose testing on
      mainnet or a public testnet.
    * Avoid generic checklist questions and repeated root causes.
    * Prefer questions that name a TRANSFORM and the GATE that was supposed to bound it, or a
      value and the second place the same value is computed differently: a price checked before a
      callcode and used after it, a decimals field captured once and multiplied everywhere, an LTV
      read as a buffer and compared as a uint, a positional list and the list it is zipped onto.

    Known dead ends - do NOT generate questions about these:
    * A real oracle publishing a wrong or stale price on its own.
    * Governance choosing a bad LTV, staleness, confidence ratio, penalty or curve exponent.
    * Pyth or Wormhole contract internals.
    * A user harming only its own position with no protocol invariant broken.
    * Anything only reproducible against the mock oracle or mock tokens.

    Core pricing invariants (each question must break one):
    * FEED INTEGRITY: a resolved price reflects a feed that passed the confidence and staleness
      gates in the form the gates were designed for, after every transform applied to it.
    * TRANSFORM SOUNDNESS: each callcode preserves magnitude and sign, rounds against the user,
      and cannot be moved by the caller within the same transaction.
    * ALIGNMENT: every price is attached to the asset it was resolved for, and every asset in a
      position enters the notional totals exactly once.
    * CONSERVATISM DIRECTION: collateral is valued low and debt is valued high at every call site,
      in that order, without exception.
    * VERDICT SOUNDNESS: the LTV a position is judged against belongs to the exact asset set the
      position will hold after the call, and the comparison neither aborts nor short-circuits.

    Each question must include:
    1. target function/method;
    2. the specific input that breaks it (price, exponent, confidence, timestamp, decimals, index,
       amount, mask);
    3. preconditions (position composition, vault state, feed state);
    4. call sequence;
    5. the pricing invariant broken and the DIRECTION of the error;
    6. who profits and the in-scope impact class;
    7. proof idea.

    Output only valid Python. No markdown. No explanations.

    questions = [
    "[File: {target_file}] [Function: symbol_or_method] Does INPUT_VALUE under PRECONDITIONS make SYMBOL produce a price or health verdict wrong in DIRECTION via CALL_SEQUENCE, violating INVARIANT, causing IMPACT_CLASS: SCOPE_IMPACT? Proof idea: Clarinet simnet test PARAMETERS and assert FEED_INTEGRITY, TRANSFORM_SOUNDNESS, ALIGNMENT, CONSERVATISM_DIRECTION, or VERDICT_SOUNDNESS.",
    ]
    """
    return prompt


def audit_format(security_question: str) -> str:
    """
    Generate a pricing-focused Zest v2 exploit-validation prompt.
    """

    prompt = f"""# SECURITY AUDIT PROMPT

## Question
{security_question}

## Rules
- Use existing repo context only. Analyze only this question and scoped impact.
- The claim must end at a wrong price or a wrong health verdict produced by THIS code. Reject anything whose root cause is a third-party oracle publishing bad data; oracle manipulation caused by a bug here is in scope.
- Assume v0-assets and v0-egroup hold a correct configuration. Only their read and resolution paths are in scope.
- Attacker is unprivileged only: an ordinary Stacks principal that funds a wallet, calls any public function, deploys its own Clarity contract, supplies its own `price-feeds`, and chooses amounts and ordering. No DAO signer, executor, market impl, authorized contract, miner, oracle publisher or node operator access.
- Reject malicious-miner, chain-reorg, MEV-only and social-engineering paths.
- OUT OF SCOPE, reject on sight: any flashloan logic (`flashloan`, its fee, its permission whitelist, `in-flashloan`) - though a flashloan used purely as capital for a different attack is fine; liquidation of disabled collateral and other deliberate safety design decisions; anything requiring DAO compromise or an accidental or incorrect DAO registry update, since full DAO control of the asset and egroup registries is intended design and egroup invariants needing global position knowledge are verified off-chain before approval.
- Also reject: leaked keys, privileged addresses, external stablecoin depegs the attacker did not cause through a bug here, 51% / basic economic / governance attacks, Sybil, centralization risk, lack of liquidity, incorrect data supplied by third-party oracles, best-practice notes, feature requests, and test or configuration files. Oracle manipulation caused by a bug in THIS code stays in scope.
- The impact must be one of: Critical - direct theft of user funds at rest or in motion other than unclaimed yield, permanent freezing of funds, or protocol insolvency; High - theft of unclaimed yield or royalties, permanent freezing of unclaimed yield or royalties, or temporary freezing of funds.
- Reject Pyth and Wormhole internals, `local-testing/**`, tests, mocks, deployment plans, docs, read-only aggregators, and dependency-only findings.

## Validate
- Follow one price end to end: raw feed value and exponent -> `normalize-pyth` or `resolve-dia` -> `check-confidence` -> `oracle-timestamp-fresh` and the per-key `last-update` -> `resolve-callcode` -> `merge-price` and the asset record -> `normalize` with `decimals` -> the notional totals -> `is-healthy` or the liquidation math.
- At each stage state the value and its units, and identify the exact stage where it becomes wrong.
- State the DIRECTION of the error - collateral overvalued, debt undervalued, or the reverse - and who profits.
- Check whether a later gate, a round-up, a health check, a slippage bound, or `oracle-price-legal` recovers the error before it reaches money.
- If the path aborts rather than mispricing, establish exactly which user or liquidator action becomes impossible, and whether that is permanent.
- Require exact file/function support and a reproducible Clarinet / vitest simnet PoC on a local fork that drives the input to the stated value.

## Output
If valid, output exactly:

### Title
[Bug statement] - ([File: file_path])

### Summary
[2-3 sentences]

### Finding Description
[The price traced stage by stage with values, the stage that breaks, root cause, attacker inputs, and why the gates do not catch it]

### Impact Explanation
[Direction and magnitude of the mispricing, who profits, and the exact in-scope severity category]

### Likelihood Explanation
[Input range required, how the attacker reaches it, capital cost, repeatability]

### Recommendation
[Specific fix]

### Proof of Concept
[Clarinet simnet test plan on a local fork, with the exact price, exponent, decimals or index values]

If invalid, output exactly:
#NoVulnerability found for this question.

No extra text.
"""
    return prompt


def validation_format(report: str) -> str:
    """
    Generate a strict bounty-style validation prompt for Zest v2 pricing claims.
    """
    prompt = f"""# VALIDATION PROMPT

## Security Claim
{report}

## Rules
- Validate only the submitted claim.
- Check SECURITY.md and Researcher.Md for scope, exclusions, and valid impact classes.
- Do not create a new vulnerability if the submitted claim is weak or invalid.
- Do not upgrade severity unless the provided evidence proves the higher impact.
- A pricing claim is only valid if the report traces the value stage by stage with concrete numbers and identifies the exact stage that breaks. Reject prose-only claims.
- Reject any claim whose root cause is a third-party oracle publishing incorrect data. Oracle manipulation caused by a bug in this code remains in scope.
- Reject any claim premised on a bad, accidental or hostile registry configuration; assume the DAO configured the asset and egroup registries correctly.
- Reject anything requiring a DAO signer, executor, market impl, authorized contract, miner, oracle publisher, node operator, or leaked keys.
- OUT OF SCOPE, reject on sight: any flashloan logic (`flashloan`, its fee, its permission whitelist, `in-flashloan`) - though a flashloan used purely as capital for a different attack is fine; liquidation of disabled collateral and other deliberate safety design decisions; anything requiring DAO compromise or an accidental or incorrect DAO registry update, since full DAO control of the asset and egroup registries is intended design and egroup invariants needing global position knowledge are verified off-chain before approval.
- Also reject: leaked keys, privileged addresses, external stablecoin depegs the attacker did not cause through a bug here, 51% / basic economic / governance attacks, Sybil, centralization risk, lack of liquidity, incorrect data supplied by third-party oracles, best-practice notes, feature requests, and test or configuration files. Oracle manipulation caused by a bug in THIS code stays in scope.
- The impact must be one of: Critical - direct theft of user funds at rest or in motion other than unclaimed yield, permanent freezing of funds, or protocol insolvency; High - theft of unclaimed yield or royalties, permanent freezing of unclaimed yield or royalties, or temporary freezing of funds.
- Reject Pyth and Wormhole internals, `local-testing/**`, tests, mocks, deployment plans, `.toml`, docs, read-only aggregator and dependency-only findings.
- Reject if the bug was already fixed, acknowledged, or covered by the published Clarity Alliance, Greybeard or Asymmetric audits.
- Reject any PoC requiring testing on mainnet or a public testnet; only local forks are permitted.
- A PoC is mandatory for every severity. Prefer #NoVulnerability over speculative reports.

## Required Validation Checks
All must pass:
1. Exact in-scope file, function, and line/code references.
2. The price traced from raw feed to health verdict with values and units at each stage.
3. The breaking stage identified, with the direction of the error stated.
4. Reachable path: preconditions -> attacker inputs -> mispriced value -> money moved or action blocked.
5. Confidence, staleness, `oracle-price-legal`, rounding direction, health checks and slippage bounds reviewed and shown not to recover the error.
6. Concrete in-scope impact class named, with the beneficiary identified.
7. Reproducible proof: Clarinet / vitest simnet test on a local fork driving the input to the stated value.

## Silent Triage Questions
Before output, internally answer:
- At exactly which line does the number become wrong, and in which direction?
- Is the root cause this code, or is it the data an oracle published?
- Does a later gate or rounding recover it before money moves?
- If it aborts instead, whose funds are frozen and for how long?
- Which in-scope impact class does it land on?
- What exact test drives the input to the breaking value?

## Output
If valid, output exactly:

Audit Report

## Title
[Clear vulnerability statement] - ([File: file_path])

## Summary
[2-3 sentence summary of the mispricing and impact]

## Finding Description
[Exact code path, the traced values, root cause, and why the gates fail]

## Impact Explanation
[Direction and magnitude, beneficiary, and the exact in-scope category]

## Likelihood Explanation
[Input range, reachability, capital cost, repeatability]

## Recommendation
[Specific fix guidance]

## Proof of Concept
[Minimal reproducible steps or Clarinet simnet test plan on a local fork]

If invalid, output exactly:
#NoVulnerability found for this question.

Output only one of the two outcomes above. No extra text.
"""
    return prompt


def scan_format(report: str) -> str:
    """
    Generate a short cross-project pricing analog scan prompt for Zest v2.
    """
    prompt = f"""# ANALOG SCAN PROMPT

## External Report
{report}

## Rules
- Use in-scope production repo context only (`mainnet/contracts/**`, excluding the dao directory). Do not ask for code or claim missing files.
- Use the external report only as a bug-class hint, not as proof.
- Keep only analogs that end at a wrong price or a wrong health verdict: exponent and sign conversion, confidence or staleness gating, a monotonic per-feed timestamp, a callcode transform, a price attached to the wrong asset, decimals normalization, rounding direction, an LTV read from a buffer, or the egroup a mask resolves to.
- Reject analogs whose root cause is third-party oracle data, or a bad or accidental registry configuration.
- OUT OF SCOPE, reject on sight: any flashloan logic (`flashloan`, its fee, its permission whitelist, `in-flashloan`) - though a flashloan used purely as capital for a different attack is fine; liquidation of disabled collateral and other deliberate safety design decisions; anything requiring DAO compromise or an accidental or incorrect DAO registry update, since full DAO control of the asset and egroup registries is intended design and egroup invariants needing global position knowledge are verified off-chain before approval.
- Also reject: leaked keys, privileged addresses, external stablecoin depegs the attacker did not cause through a bug here, 51% / basic economic / governance attacks, Sybil, centralization risk, lack of liquidity, incorrect data supplied by third-party oracles, best-practice notes, feature requests, and test or configuration files. Oracle manipulation caused by a bug in THIS code stays in scope.
- The impact must be one of: Critical - direct theft of user funds at rest or in motion other than unclaimed yield, permanent freezing of funds, or protocol insolvency; High - theft of unclaimed yield or royalties, permanent freezing of unclaimed yield or royalties, or temporary freezing of funds.
- Reject malicious-miner, chain-reorg, MEV-only, Pyth or Wormhole internals, `local-testing/**`, mock, deployment-plan, dependency-only and no-impact analogs.

## Validate
- Map the bug class to the strongest reachable Zest pricing path and trace the value stage by stage.
- State the direction of the error and who profits.
- Prove root cause with exact file/function support.
- Name the in-scope impact class it lands on.

## Output (Strict)
If valid analog exists, output:

### Title
[Clear vulnerability statement] - ([File: file_path])

### Summary
### Finding Description
### Impact Explanation
### Likelihood Explanation
### Recommendation
### Proof of Concept

If not, output exactly:
#NoVulnerability found for this question.

No extra text.
"""
    return prompt
