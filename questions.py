import json
import os

from decouple import config

# todo: if scope_files is: 500 > 50, 300 > 30 , 100 > 10
MAX_REPO = 20
# todo: the path from https:///github.com/dfinity/ICRC-1
SOURCE_REPO = "DeeBravegiant/sequencer"
# todo: the name of the repository
REPO_NAME = "sequencer"
run_number = os.environ.get('GITHUB_RUN_NUMBER') or os.environ.get('CI_PIPELINE_IID', '0')


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
    "crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/block_context.cairo",
    "crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/block_hash.cairo",
    "crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/builtins.cairo",
    "crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/constants.cairo",
    "crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/contract_address/contract_address.cairo",
    "crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/contract_class/blake_compiled_class_hash.cairo",
    "crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/contract_class/compiled_class.cairo",
    "crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/contract_class/compiled_class_struct.cairo",
    "crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/contract_class/contract_class.cairo",
    "crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/contract_class/deprecated_compiled_class.cairo",
    "crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/contract_class/poseidon_compiled_class_hash.cairo",
    "crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/data_availability/bls_field.cairo",
    "crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/data_availability/commitment.cairo",
    "crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/data_availability/compression.cairo",
    "crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/encrypt.cairo",
    "crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/account_backward_compatibility.cairo",
    "crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deploy_contract.cairo",
    "crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deprecated_execute_entry_point.cairo",
    "crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deprecated_execute_syscalls.cairo",
    "crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/entry_point_utils.cairo",
    "crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/entry_point_utils__virtual.cairo",
    "crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_entry_point.cairo",
    "crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_syscalls.cairo",
    "crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_syscalls__virtual.cairo",
    "crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_transaction_utils.cairo",
    "crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_transactions.cairo",
    "crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_transactions_inner.cairo",
    "crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_transactions_inner__virtual.cairo",
    "crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execution_constraints.cairo",
    "crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execution_constraints__virtual.cairo",
    "crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/revert.cairo",
    "crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo",
    "crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo",
    "crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/hash/hash_state_blake.cairo",
    "crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/naive_blake.cairo",
    "crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/os.cairo",
    "crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/os_config/os_config.cairo",
    "crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/os_utils.cairo",
    "crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/os_utils__virtual.cairo",
    "crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/output.cairo",
    "crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/aliases.cairo",
    "crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/commitment.cairo",
    "crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/output.cairo",
    "crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/squash.cairo",
    "crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/state.cairo",
    "crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo",
    "crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/virtual_os_output.cairo",
]

target_scopes = [
    "Critical. Permanent freezing of funds",
    "Critical. Direct loss of funds",
    "High. Network not being able to confirm new transactions (total network shutdown)",
    "High. Unintended chain split (network partition)",
]



def question_generator(target_file: str) -> str:
    """
    Generate exploit-focused audit + fuzzing questions for one StarkNet protocol target.

    ```
    target_file format:
    "'File Name: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_transactions.cairo -> Scope: Critical. Direct loss of funds'"
    ```
    """

    prompt = f"""
    ```

    Generate exploit-focused security audit and fuzzing questions for this exact StarkNet protocol target:

    {target_file}

    Use live context from the project if available: StarkNet OS state transition, transaction execution, syscall handling, deploy/declare/invoke flows, contract class hashing, contract address derivation, state commitment/output, block context, data availability, parsing, and cryptography.

    Protocol focus:
    This repository includes StarkNet OS Cairo code that drives StarkNet state transition and proof-related execution. The audit focus is whether invalid transactions, class data, syscalls, state updates, commitments, or outputs can be accepted, or whether bugs can cause direct fund loss, permanent fund freezing, total network shutdown, or chain split.

    Core invariants:

    * Invalid transactions, syscalls, contract classes, state diffs, commitments, hashes, or outputs must not be accepted by honest StarkNet participants.
    * Valid transactions and state updates must remain processable without unintended chain split, proof/state divergence, or network halt.
    * Execution, deployment, fee, nonce, class, and state-transition logic must preserve balances, authorization, and commitment correctness under adversarial inputs.
    * Output, data availability, compression, and serialization paths must reject malformed or adversarial inputs safely.
    * Cryptographic verification and hashing must preserve consensus, authorization, and protocol safety.

    Rules:

    * Treat `File Name:` as the exact file/module.
    * Treat `Scope:` as the ONLY impact to target.
    * Assume full repo context is accessible.
    * Do not ask for code or say anything is missing.
    * Attacker is unprivileged: StarkNet transaction sender, contract deployer, class declarer, L1/L2 message sender, or user controlling public protocol inputs.
    * Do not rely on malicious operator behavior, leaked keys, privileged addresses, social engineering, front-run-only paths, network-level DoS, 51%/governance attacks, oracle-only failures, or public-mainnet testing.
    * Generate 20 to 30 high-signal questions.
    * At least 70% must be multi-step flow, invariant, fuzz, accounting, state-transition, consensus, or cross-module questions.
    * Every question must be testable by PoC, unit test, fuzz test, invariant test, or differential test.
    * Avoid generic checklist questions and repeated root causes.
    * Every question must target a plausible valid issue.

    High-value attack surfaces:

    * Transactions and execution: invoke/declare/deploy_account/deploy flows, syscall dispatch, reverts, fee/nonce/resource accounting, and execution constraints.
    * State transition and commitments: storage/class updates, alias/squash logic, state commitment, output roots, block hash, and output encoding.
    * Contract and class handling: compiled/deprecated class hashing, contract address derivation, transaction hash derivation, and class parsing/serialization.
    * External protocol inputs: L1/L2 messages, block context, calldata/retdata, data availability compression/encryption, and public input shaping.
    * Cryptography and parsing: Poseidon/Blake hashing, field arithmetic helpers, commitment serialization, and proof-related input derivation.

    Impact mapping:

    * Critical: Permanent freezing of funds.
    * Critical: Direct loss of funds.
    * High: Network not being able to confirm new transactions (total network shutdown).
    * High: Unintended chain split (network partition).

    Each question must include:

    1. target function/module;
    2. attacker action;
    3. preconditions;
    4. call sequence;
    5. invariant tested;
    6. scoped impact;
    7. proof idea.

    Output only valid Python. No markdown. No explanations.

    questions = [
    "[File: {target_file}] [Function: symbol_or_module] Can an unprivileged ATTACKER_ACTION under PRECONDITIONS trigger CALL_SEQUENCE, violating INVARIANT, causing scoped impact: SCOPE_IMPACT? Proof idea: fuzz/state-test PARAMETERS and assert EXPECTED_PROPERTY.",
    ]
    """
    return prompt


def audit_format(question: str) -> str:
    """
    Generate a focused StarkNet protocol exploit-question validation prompt.
    """
    return f"""# QUESTION SCAN PROMPT

## Exploit Question
{question}

Main Focus should be on crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/ 
Issues outside that file is out of scope 


## Scope Rules
- Audit only production StarkNet protocol code.
- Do not ask for repo contents or claim files are missing.
- Ignore tests, docs, mocks, scripts, configs, build files, IDE files, package metadata, vendored libraries, and local-only fixtures.

## Objective
Decide whether the question leads to a real, reachable StarkNet protocol vulnerability.
The attacker must be unprivileged and enter through transaction submission, class declaration, contract deployment, L1/L2 message flow, or other public protocol inputs.
The impact must match one of the allowed StarkNet protocol impacts below.
Prefer #NoVulnerability unless the path is concrete, local-testable, and bounty-grade.

## Allowed Impact Scope
Only these impacts are valid:
- Critical. Permanent freezing of funds.
- Critical. Direct loss of funds.
- High. Network not being able to confirm new transactions (total network shutdown).
- High. Unintended chain split (network partition).

## Method
1. Trace the attacker-controlled entrypoint.
2. Map it to exact production StarkNet files/functions.
3. Check the relevant guard: execution or syscall validation, fee/nonce/accounting checks, class/hash/commitment validation, state-transition/output checks, parser bounds, or crypto verification.
4. Decide whether the questioned invariant can actually break under intended deployment.
5. Prove root cause with file/function/line references.
6. Confirm realistic likelihood and exact scoped impact.
7. Reject if current validation already prevents the exploit.

## Reject Immediately
- Requires trusted role, leaked key, malicious operator behavior, or privileged operator access.
- Requires third-party dependency compromise, Sybil/51% attack, phishing, public-mainnet testing, or network-level DoS only.
- Only affects tests, docs, configs, scripts, mocks, local fixtures, vendored libraries, or local deployment choices.
- External dependency behavior is the only cause.
- Impact is only logging, observability, local misconfiguration, non-security correctness, harmless revert, stale read, rejected update, or theoretical risk.
- No concrete scoped impact or no realistic exploit path.

## Output
If valid:

### Title
[Clear vulnerability statement] - ([File: file_path])

### Summary
### Finding Description
### Impact Explanation
### Likelihood Explanation
### Recommendation
### Proof of Concept

If invalid, output exactly:
#NoVulnerability found for this question.
"""


def scan_format(report: str) -> str:
    """
    Generate a short cross-project analog scan prompt for StarkNet protocol.
    """
    prompt = f"""# ANALOG SCAN PROMPT

## External Report
{report}

Main Focus should be on crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/ 
Issues outside that file is out of scope 


## Access Rules (Strict)
- Treat production StarkNet protocol files in the provided scope as accessible context.
- Do not claim missing/inaccessible files.
- Do not ask for repository contents.
- Do not scan tests, docs, build files, IDE files, configs, resources, local fixtures, vendored libraries, or package metadata as audited targets.

## Objective
Use the external report's vulnerability class as a hint to find valid issues based on the StarkNet bounty scope.
Focus on reachable issues triggered by an unprivileged transaction sender, class declarer, contract deployer, L1/L2 message sender, or public protocol input user.
Only report an analog if this codebase has its own reachable root cause and the impact matches one of the allowed StarkNet protocol impacts below.

## Allowed Impact Scope
Only these impacts are valid:
- Critical. Permanent freezing of funds.
- Critical. Direct loss of funds.
- High. Network not being able to confirm new transactions (total network shutdown).
- High. Unintended chain split (network partition).

## Method
1. Classify vuln type: state-transition bypass, chain split, network halt, invalid transaction or class acceptance, fee/accounting bug, commitment/output flaw, parser bounds issue, or crypto verification flaw.
2. Map to StarkNet protocol components and exact production files.
3. Prove root cause with exact file/function/module/line references.
4. Confirm concrete scoped impact and realistic likelihood.
5. Explain the attacker-controlled entry path and why this repository's code is a necessary vulnerable step.
6. Reject if the impact does not match one of the allowed StarkNet protocol impacts above.

## Disqualify Immediately
- No reachable attacker-controlled entry path.
- Requires trusted role, leaked key, malicious operator behavior, or privileged operator access.
- Requires third-party dependency compromise, Sybil/51% attack, phishing, public-mainnet testing, or network-level DoS only.
- External dependency behavior is the only cause.
- Test/docs/config/build-only issue.
- Theoretical-only issue with no protocol impact.
- Impact is only local misconfiguration, observability noise, logging noise, harmless revert, stale read, or non-security correctness.
- Impact or likelihood missing.

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



def validation_format(report: str) -> str:
    """
    Generate a strict StarkNet protocol bounty-style validation prompt for security claims.
    """
    prompt = f"""# VALIDATION PROMPT

## Security Claim
{report}

Main Focus should be on crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/ 
Issues outside that file is out of scope 


## Rules
- Validate only the submitted claim.
- Check SECURITY.md, Researcher.md if present, and the StarkNet bounty scope for scope, exclusions, and valid impact classes.
- Do not create a new vulnerability if the submitted claim is weak or invalid.
- Do not upgrade severity unless the provided evidence proves the higher impact.
- Reject malicious-operator-only, privileged-address-only, leaked-key, host-compromise, best-practice, docs/style, config/test-only, gas-optimization-only, front-run-only, network-level-DoS-only, and purely theoretical issues.
- Reject if the exploit requires unrealistic assumptions, victim mistakes, phishing/social engineering, third-party dapp/oracle compromise, governance or 51% control, sybil/centralization assumptions, public-mainnet DoS testing, or unsupported protocol behavior.
- A valid report must be triggerable by an unprivileged user through StarkNet transactions, contract/class flows, L1/L2 messaging, or another public protocol input, unless the claim proves privilege escalation from an unprivileged path.
- The final impact must match an in-scope bounty impact, not just a generic code bug.
- Reject any issue whose final impact is not one of the allowed StarkNet protocol impacts listed below.
- Prefer #NoVulnerability over speculative reports.

## In-Scope Protocol Areas
The claim must affect production in-scope StarkNet protocol code or systems, such as:
- StarkNet OS transaction and execution paths: invoke/declare/deploy_account/deploy, syscall handling, revert logic, fee/nonce/resource checks, and execution constraints.
- State transition and output paths: state updates, class/storage commitments, output encoding, block hash derivation, data availability, compression, and serialization.
- Contract and class logic: contract address derivation, transaction hashing, compiled/deprecated class hashing, and class parsing.
- Cryptography and proof-related processing: Poseidon/Blake hashing, commitment arithmetic, public input shaping, and related validation.

Reject third-party dapps, unlisted public websites, tests, docs, examples, mocks, generated files, local deployment helpers, vendored libraries, and issues that only affect local developer tooling unless the submitted claim proves a direct in-scope StarkNet protocol security impact.

## Allowed Impact Scope
Only these impacts are valid:
- Critical. Permanent freezing of funds.
- Critical. Direct loss of funds.
- High. Network not being able to confirm new transactions (total network shutdown).
- High. Unintended chain split (network partition).

Informational, non-security correctness, observability/logging-only, harmless reject/revert, stale read without consensus/state/accounting/security impact, local misconfiguration, and non-demonstrably-exploitable reports are invalid for this validation output.

If the submitted claim does not concretely prove one of the allowed StarkNet protocol impacts above, it is invalid.

## Required Validation Checks
All must pass:
1. Exact in-scope file, function, and line/code references.
2. Clear root cause and broken protocol/security/accounting/authentication/certification assumption.
3. Reachable exploit path: preconditions -> attacker action -> trigger -> bad result.
4. Existing checks/guards reviewed and shown insufficient.
5. Concrete impact that exactly matches one allowed StarkNet protocol impact above, with realistic likelihood.
6. Reproducible safe proof path: runnable PoC, deterministic integration test, invariant/fuzz test, differential test, or exact local manual steps.
7. No obvious rejection reason from SECURITY.md, Researcher.md if present, known issues, privileges, or scope exclusions.

## Silent Triage Questions
Before output, internally answer:
- Can a normal external user trigger this through a public StarkNet protocol path?
- Does the code actually behave as claimed?
- Is the impact caused by StarkNet production protocol code, not by an external dependency alone?
- Is the chain-split/network/funds-loss/funds-freeze impact concrete, not hypothetical?
- Does the claim avoid malicious operator, privileged address, leaked key, mainnet DoS, governance, and third-party compromise assumptions?
- Would a bounty triager accept the proof?
- What exact test would prove it?

## Output
If valid, output exactly:

Audit Report

## Title
[Clear vulnerability statement] - ([File: file_path])

## Summary
[2-3 sentence summary of the bug and impact]

## Finding Description
[Exact code path, root cause, exploit flow, and why existing checks fail]

## Impact Explanation
[Concrete allowed StarkNet protocol bounty impact and severity rationale]

## Likelihood Explanation
[Attacker capability, required conditions, feasibility, repeatability]

## Recommendation
[Specific fix guidance]

## Proof of Concept
[Minimal reproducible steps or fuzz/invariant/fork test plan]

If invalid, output exactly:
#NoVulnerability found for this question.

Output only one of the two outcomes above. No extra text.
"""
    return prompt
