import json
import os

MAX_REPO = 40
SOURCE_REPO = 'desktop/desktop'
REPO_NAME = 'desktop'
run_number = os.environ.get("GITHUB_RUN_NUMBER") or os.environ.get(
    "CI_PIPELINE_IID", "0"
)


def get_cyclic_index(run_number, max_index=100):
    """Convert run number to a cyclic index between 1 and max_index."""
    return (int(run_number) - 1) % max_index + 1


def load_repository_urls():
    """Load repository URLs from repositories.json."""
    repo_file = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "repositories.json"
    )
    if not os.path.exists(repo_file):
        return []

    try:
        with open(repo_file, "r", encoding="utf-8") as f:
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
    'app/src/cli/main.ts',
    'app/src/highlighter/index.ts',
    'app/src/lib/actions-log-parser/action-log-parser.ts',
    'app/src/lib/actions-log-parser/action-log-pipeline-commands.ts',
    'app/src/lib/actions-log-parser/actions-log-parser-objects.ts',
    'app/src/lib/actions-log-parser/actions-logs-ansii.ts',
    'app/src/lib/api.ts',
    'app/src/lib/app-shell.ts',
    'app/src/lib/auth.ts',
    'app/src/lib/branch.ts',
    'app/src/lib/ci-checks/ci-checks.ts',
    'app/src/lib/cli-action.ts',
    'app/src/lib/commit-url.ts',
    'app/src/lib/copilot-commit-message.ts',
    'app/src/lib/copilot-conflict-context.ts',
    'app/src/lib/copilot-conflict-resolution.ts',
    'app/src/lib/copilot-in-memory-session-fs-provider.ts',
    'app/src/lib/copilot/byok.ts',
    'app/src/lib/copilot/conflict-resolution-model.ts',
    'app/src/lib/custom-integration.ts',
    'app/src/lib/databases/base-database.ts',
    'app/src/lib/databases/github-user-database.ts',
    'app/src/lib/databases/index.ts',
    'app/src/lib/databases/issues-database.ts',
    'app/src/lib/databases/pull-request-database.ts',
    'app/src/lib/databases/repositories-database.ts',
    'app/src/lib/diff-parser.ts',
    'app/src/lib/editors/darwin.ts',
    'app/src/lib/editors/found-editor.ts',
    'app/src/lib/editors/index.ts',
    'app/src/lib/editors/launch.ts',
    'app/src/lib/editors/linux.ts',
    'app/src/lib/editors/lookup.ts',
    'app/src/lib/editors/shared.ts',
    'app/src/lib/editors/win32.ts',
    'app/src/lib/email.ts',
    'app/src/lib/emoji.ts',
    'app/src/lib/endpoint-capabilities.ts',
    'app/src/lib/endpoint-token.ts',
    'app/src/lib/exec-file.ts',
    'app/src/lib/file-system.ts',
    'app/src/lib/find-account.ts',
    'app/src/lib/format-commit-message.ts',
    'app/src/lib/generic-git-auth.ts',
    'app/src/lib/get-file-hash.ts',
    'app/src/lib/get-old-path.ts',
    'app/src/lib/git/add.ts',
    'app/src/lib/git/apply.ts',
    'app/src/lib/git/authentication.ts',
    'app/src/lib/git/branch.ts',
    'app/src/lib/git/checkout-index.ts',
    'app/src/lib/git/checkout.ts',
    'app/src/lib/git/cherry-pick.ts',
    'app/src/lib/git/clone.ts',
    'app/src/lib/git/coerce-to-buffer.ts',
    'app/src/lib/git/coerce-to-string.ts',
    'app/src/lib/git/commit.ts',
    'app/src/lib/git/config.ts',
    'app/src/lib/git/core.ts',
    'app/src/lib/git/create-tail-stream.ts',
    'app/src/lib/git/credential.ts',
    'app/src/lib/git/description.ts',
    'app/src/lib/git/diff-check.ts',
    'app/src/lib/git/diff-index.ts',
    'app/src/lib/git/diff.ts',
    'app/src/lib/git/environment.ts',
    'app/src/lib/git/fetch.ts',
    'app/src/lib/git/for-each-ref.ts',
    'app/src/lib/git/format-patch.ts',
    'app/src/lib/git/git-delimiter-parser.ts',
    'app/src/lib/git/gitignore.ts',
    'app/src/lib/git/index.ts',
    'app/src/lib/git/init.ts',
    'app/src/lib/git/interpret-trailers.ts',
    'app/src/lib/git/lfs.ts',
    'app/src/lib/git/log.ts',
    'app/src/lib/git/merge-tree.ts',
    'app/src/lib/git/merge.ts',
    'app/src/lib/git/multi-operation-terminal-output.ts',
    'app/src/lib/git/pull.ts',
    'app/src/lib/git/push-terminal-chunk.ts',
    'app/src/lib/git/push.ts',
    'app/src/lib/git/rebase.ts',
    'app/src/lib/git/reflog.ts',
    'app/src/lib/git/refs.ts',
    'app/src/lib/git/remote.ts',
    'app/src/lib/git/reorder.ts',
    'app/src/lib/git/reset.ts',
    'app/src/lib/git/rev-list.ts',
    'app/src/lib/git/rev-parse.ts',
    'app/src/lib/git/revert.ts',
    'app/src/lib/git/rm.ts',
    'app/src/lib/git/show.ts',
    'app/src/lib/git/spawn.ts',
    'app/src/lib/git/squash.ts',
    'app/src/lib/git/stage.ts',
    'app/src/lib/git/stash.ts',
    'app/src/lib/git/status.ts',
    'app/src/lib/git/submodule.ts',
    'app/src/lib/git/tag.ts',
    'app/src/lib/git/update-index.ts',
    'app/src/lib/git/update-ref.ts',
    'app/src/lib/git/var.ts',
    'app/src/lib/git/worktree.ts',
    'app/src/lib/helpers/pull-request-matching.ts',
    'app/src/lib/helpers/regex.ts',
    'app/src/lib/helpers/repo-rules.ts',
    'app/src/lib/highlighter/worker.ts',
    'app/src/lib/hooks/config.ts',
    'app/src/lib/hooks/get-repo-hooks.ts',
    'app/src/lib/hooks/get-shell-env.ts',
    'app/src/lib/hooks/get-shell.ts',
    'app/src/lib/hooks/hooks-proxy.ts',
    'app/src/lib/hooks/shell-escape.ts',
    'app/src/lib/hooks/with-hooks-env.ts',
    'app/src/lib/http.ts',
    'app/src/lib/ipc-renderer.ts',
    'app/src/lib/ipc-shared.ts',
    'app/src/lib/is-application-bundle.ts',
    'app/src/lib/large-files.ts',
    'app/src/lib/local-storage.ts',
    'app/src/lib/logging/format-error.ts',
    'app/src/lib/logging/format-log-message.ts',
    'app/src/lib/logging/get-log-path.ts',
    'app/src/lib/logging/log-level.ts',
    'app/src/lib/logging/main/install.ts',
    'app/src/lib/logging/renderer/install.ts',
    'app/src/lib/markdown-filters/close-keyword-filter.ts',
    'app/src/lib/markdown-filters/commit-mention-filter.ts',
    'app/src/lib/markdown-filters/commit-mention-link-filter.ts',
    'app/src/lib/markdown-filters/emoji-filter.ts',
    'app/src/lib/markdown-filters/is-element.ts',
    'app/src/lib/markdown-filters/issue-link-filter.ts',
    'app/src/lib/markdown-filters/issue-mention-filter.ts',
    'app/src/lib/markdown-filters/mention-filter.ts',
    'app/src/lib/markdown-filters/node-filter.ts',
    'app/src/lib/markdown-filters/resolve-owner-repo.ts',
    'app/src/lib/markdown-filters/team-mention-filter.ts',
    'app/src/lib/markdown-filters/video-link-filter.ts',
    'app/src/lib/markdown-filters/video-tag-filter.ts',
    'app/src/lib/markdown-filters/video-url-regex.ts',
    'app/src/lib/menu-update.ts',
    'app/src/lib/notifications/notification-handler.ts',
    'app/src/lib/notifications/show-notification.ts',
    'app/src/lib/parse-app-url.ts',
    'app/src/lib/parse-pac-string.ts',
    'app/src/lib/patch-formatter.ts',
    'app/src/lib/path-exists.ts',
    'app/src/lib/path.ts',
    'app/src/lib/popup-manager.ts',
    'app/src/lib/process/win32.ts',
    'app/src/lib/pull-request-refs.ts',
    'app/src/lib/read-emoji.ts',
    'app/src/lib/release-notes.ts',
    'app/src/lib/remote-parsing.ts',
    'app/src/lib/remove-remote-prefix.ts',
    'app/src/lib/repository-matching.ts',
    'app/src/lib/resolve-git-proxy.ts',
    'app/src/lib/sanitize-ref-name.ts',
    'app/src/lib/shell.ts',
    'app/src/lib/shells/darwin.ts',
    'app/src/lib/shells/error.ts',
    'app/src/lib/shells/index.ts',
    'app/src/lib/shells/linux.ts',
    'app/src/lib/shells/shared.ts',
    'app/src/lib/shells/win32.ts',
    'app/src/lib/split-buffer.ts',
    'app/src/lib/ssh/ssh-credential-storage.ts',
    'app/src/lib/ssh/ssh-key-passphrase.ts',
    'app/src/lib/ssh/ssh-user-password.ts',
    'app/src/lib/ssh/ssh.ts',
    'app/src/lib/status-parser.ts',
    'app/src/lib/status.ts',
    'app/src/lib/stores/accounts-store.ts',
    'app/src/lib/stores/ahead-behind-store.ts',
    'app/src/lib/stores/alive-store.ts',
    'app/src/lib/stores/api-repositories-store.ts',
    'app/src/lib/stores/app-store.ts',
    'app/src/lib/stores/base-store.ts',
    'app/src/lib/stores/cloning-repositories-store.ts',
    'app/src/lib/stores/commit-status-store.ts',
    'app/src/lib/stores/copilot-store.ts',
    'app/src/lib/stores/git-store-cache.ts',
    'app/src/lib/stores/git-store.ts',
    'app/src/lib/stores/github-user-store.ts',
    'app/src/lib/stores/helpers/background-fetcher.ts',
    'app/src/lib/stores/helpers/branch-pruner.ts',
    'app/src/lib/stores/helpers/create-tutorial-repository.ts',
    'app/src/lib/stores/helpers/find-branch-name.ts',
    'app/src/lib/stores/helpers/find-default-remote.ts',
    'app/src/lib/stores/helpers/find-forked-remotes-to-prune.ts',
    'app/src/lib/stores/helpers/find-upstream-remote.ts',
    'app/src/lib/stores/helpers/pull-request-updater.ts',
    'app/src/lib/stores/helpers/repository-indicator-updater.ts',
    'app/src/lib/stores/helpers/tags-to-push-storage.ts',
    'app/src/lib/stores/helpers/tutorial-assessor.ts',
    'app/src/lib/stores/index.ts',
    'app/src/lib/stores/issues-store.ts',
    'app/src/lib/stores/notifications-store.ts',
    'app/src/lib/stores/pull-request-coordinator.ts',
    'app/src/lib/stores/pull-request-store.ts',
    'app/src/lib/stores/repositories-store.ts',
    'app/src/lib/stores/repository-state-cache.ts',
    'app/src/lib/stores/sign-in-store.ts',
    'app/src/lib/stores/stores.ts',
    'app/src/lib/stores/token-store.ts',
    'app/src/lib/stores/updates/changes-state.ts',
    'app/src/lib/stores/updates/update-remote-url.ts',
    'app/src/lib/stores/upstream-already-exists-error.ts',
    'app/src/lib/suppress-certificate-error.ts',
    'app/src/lib/tailer.ts',
    'app/src/lib/text-token-parser.ts',
    'app/src/lib/trampoline/find-account.ts',
    'app/src/lib/trampoline/trampoline-askpass-handler.ts',
    'app/src/lib/trampoline/trampoline-command-parser.ts',
    'app/src/lib/trampoline/trampoline-command.ts',
    'app/src/lib/trampoline/trampoline-credential-helper.ts',
    'app/src/lib/trampoline/trampoline-environment.ts',
    'app/src/lib/trampoline/trampoline-server.ts',
    'app/src/lib/trampoline/trampoline-tokens.ts',
    'app/src/lib/trampoline/trampoline-ui-helper.ts',
    'app/src/lib/trampoline/url-without-credentials.ts',
    'app/src/lib/trampoline/use-external-credential-helper.ts',
    'app/src/lib/valid-notification-pull-request-review.ts',
    'app/src/lib/web-flow-committer.ts',
    'app/src/lib/wrap-rich-text-commit-message.ts',
    'app/src/main-process/alive-origin-filter.ts',
    'app/src/main-process/app-window.ts',
    'app/src/main-process/authenticated-image-filter.ts',
    'app/src/main-process/crash-window.ts',
    'app/src/main-process/desktop-console-transport.ts',
    'app/src/main-process/desktop-file-transport.ts',
    'app/src/main-process/exception-reporting.ts',
    'app/src/main-process/ipc-main.ts',
    'app/src/main-process/ipc-webcontents.ts',
    'app/src/main-process/log.ts',
    'app/src/main-process/main.ts',
    'app/src/main-process/menu/build-context-menu.ts',
    'app/src/main-process/menu/build-default-menu.ts',
    'app/src/main-process/menu/build-spell-check-menu.ts',
    'app/src/main-process/menu/crash-menu.ts',
    'app/src/main-process/menu/ensure-item-ids.ts',
    'app/src/main-process/menu/get-all-menu-items.ts',
    'app/src/main-process/menu/index.ts',
    'app/src/main-process/menu/menu-event.ts',
    'app/src/main-process/notifications.ts',
    'app/src/main-process/now.ts',
    'app/src/main-process/ordered-webrequest.ts',
    'app/src/main-process/same-origin-filter.ts',
    'app/src/main-process/shell.ts',
    'app/src/main-process/show-uncaught-exception.ts',
    'app/src/main-process/squirrel-updater.ts',
    'app/src/main-process/trusted-ipc-sender.ts',
    'app/src/models/account.ts',
    'app/src/models/clone-options.ts',
    'app/src/models/commit-identity.ts',
    'app/src/models/commit.ts',
    'app/src/models/github-repository.ts',
    'app/src/models/owner.ts',
    'app/src/models/pull-request.ts',
    'app/src/models/remote.ts',
    'app/src/models/repository.ts',
    'app/src/models/status.ts',
    'app/src/models/submodule.ts',
    'app/src/ui/add-repository/add-existing-repository.tsx',
    'app/src/ui/add-repository/create-repository.tsx',
    'app/src/ui/add-repository/git-attributes.ts',
    'app/src/ui/add-repository/gitignores.ts',
    'app/src/ui/add-repository/sanitized-repository-name.ts',
    'app/src/ui/add-repository/write-default-readme.ts',
    'app/src/ui/autocompletion/autocompleting-text-input.tsx',
    'app/src/ui/autocompletion/autocompletion-provider.ts',
    'app/src/ui/autocompletion/branch-autocompletion-provider.tsx',
    'app/src/ui/autocompletion/build-autocompletion-providers.ts',
    'app/src/ui/autocompletion/common.ts',
    'app/src/ui/autocompletion/emoji-autocompletion-provider.tsx',
    'app/src/ui/autocompletion/index.ts',
    'app/src/ui/autocompletion/issues-autocompletion-provider.tsx',
    'app/src/ui/autocompletion/user-autocompletion-provider.tsx',
    'app/src/ui/check-runs/ci-check-re-run-button.tsx',
    'app/src/ui/check-runs/ci-check-run-actions-job-step-item.tsx',
    'app/src/ui/check-runs/ci-check-run-actions-job-step-list.tsx',
    'app/src/ui/check-runs/ci-check-run-list-item.tsx',
    'app/src/ui/check-runs/ci-check-run-list.tsx',
    'app/src/ui/check-runs/ci-check-run-no-steps.tsx',
    'app/src/ui/check-runs/ci-check-run-popover.tsx',
    'app/src/ui/check-runs/ci-check-run-rerun-dialog.tsx',
    'app/src/ui/check-runs/ci-check-run-step-list-header.tsx',
    'app/src/ui/clone-repository/clone-generic-repository.tsx',
    'app/src/ui/clone-repository/clone-github-repository.tsx',
    'app/src/ui/clone-repository/clone-repository.tsx',
    'app/src/ui/clone-repository/cloneable-repository-filter-list.tsx',
    'app/src/ui/clone-repository/group-repositories.ts',
    'app/src/ui/clone-repository/index.tsx',
    'app/src/ui/diff/binary-file.tsx',
    'app/src/ui/diff/changed-range.ts',
    'app/src/ui/diff/diff-contents-warning.tsx',
    'app/src/ui/diff/diff-explorer.ts',
    'app/src/ui/diff/diff-header.tsx',
    'app/src/ui/diff/diff-helpers.tsx',
    'app/src/ui/diff/diff-options.tsx',
    'app/src/ui/diff/diff-search-input.tsx',
    'app/src/ui/diff/get-tokens.ts',
    'app/src/ui/diff/image-diffs/dds-converter.ts',
    'app/src/ui/diff/image-diffs/deleted-image-diff.tsx',
    'app/src/ui/diff/image-diffs/difference-blend.tsx',
    'app/src/ui/diff/image-diffs/image-container.tsx',
    'app/src/ui/diff/image-diffs/index.ts',
    'app/src/ui/diff/image-diffs/modified-image-diff.tsx',
    'app/src/ui/diff/image-diffs/new-image-diff.tsx',
    'app/src/ui/diff/image-diffs/onion-skin.tsx',
    'app/src/ui/diff/image-diffs/sizing.ts',
    'app/src/ui/diff/image-diffs/swipe.tsx',
    'app/src/ui/diff/image-diffs/two-up.tsx',
    'app/src/ui/diff/index.tsx',
    'app/src/ui/diff/seamless-diff-switcher.tsx',
    'app/src/ui/diff/side-by-side-diff-row.tsx',
    'app/src/ui/diff/side-by-side-diff.tsx',
    'app/src/ui/diff/submodule-diff.tsx',
    'app/src/ui/diff/syntax-highlighting/index.ts',
    'app/src/ui/diff/text-diff-expansion.ts',
    'app/src/ui/diff/whitespace-hint-popover.tsx',
    'app/src/ui/dispatcher/dispatcher.ts',
    'app/src/ui/dispatcher/error-handlers.ts',
    'app/src/ui/dispatcher/index.ts',
    'app/src/ui/generic-git-auth/generic-git-auth.tsx',
    'app/src/ui/lib/app-proxy.ts',
    'app/src/ui/lib/authentication-form.tsx',
    'app/src/ui/lib/author-input/author-input.tsx',
    'app/src/ui/lib/author-input/author-text.ts',
    'app/src/ui/lib/avatar.tsx',
    'app/src/ui/lib/context-menu.ts',
    'app/src/ui/lib/default-dir.ts',
    'app/src/ui/lib/enterprise-validate-url.ts',
    'app/src/ui/lib/expiring-operation-cache.ts',
    'app/src/ui/lib/highlight-text.tsx',
    'app/src/ui/lib/identifier-rules.ts',
    'app/src/ui/lib/install-cli.ts',
    'app/src/ui/lib/link-button.tsx',
    'app/src/ui/lib/open-file.ts',
    'app/src/ui/lib/parse-files-to-be-overwritten.ts',
    'app/src/ui/lib/path-text.tsx',
    'app/src/ui/lib/releases.ts',
    'app/src/ui/lib/rich-text.tsx',
    'app/src/ui/lib/sandboxed-markdown.tsx',
    'app/src/ui/lib/sign-in.tsx',
    'app/src/ui/lib/update-store.ts',
    'app/src/ui/main-process-proxy.ts',
    'app/src/ui/notifications/pull-request-checks-failed.tsx',
    'app/src/ui/notifications/pull-request-comment-like.tsx',
    'app/src/ui/notifications/pull-request-comment.tsx',
    'app/src/ui/notifications/pull-request-review-helpers.ts',
    'app/src/ui/notifications/pull-request-review.tsx',
    'app/src/ui/open-with-external-editor/open-with-external-editor.tsx',
    'app/src/ui/preferences/custom-integration-form.tsx',
    'app/src/ui/publish-repository/publish-repository.tsx',
    'app/src/ui/repository-settings/git-config.tsx',
    'app/src/ui/repository-settings/git-ignore.tsx',
    'app/src/ui/repository-settings/remote.tsx',
    'app/src/ui/sign-in/sign-in.tsx',
    'app/src/ui/ssh/add-ssh-host.tsx',
    'app/src/ui/ssh/ssh-key-passphrase.tsx',
    'app/src/ui/ssh/ssh-user-password.tsx',
    'app/src/ui/terminal.tsx',
    'app/src/ui/untrusted-certificate/untrusted-certificate.tsx',
    'app/src/ui/welcome/sign-in-enterprise.tsx',
    'app/src/ui/worktrees/add-worktree-dialog.tsx',
]

target_scopes = [
    'Critical. Content of a repository the user merely clones, fetches, checks out, or opens - paths, symlinks, `.gitattributes`, `.gitmodules`, submodule or LFS metadata, hook-adjacent files - makes Desktop write, replace, or execute something outside the intended working tree, yielding code execution on the user machine.',
    'Critical. Attacker-controlled text that reaches a git or child-process invocation (ref, branch, tag, remote URL, repository path, worktree path, editor/shell/custom-integration argument) is parsed as an option, flag, or shell token, so git or a spawned program runs attacker-chosen commands or configuration.',
    'Critical. A GitHub OAuth token, PAT, generic git credential, or SSH passphrase held by Desktop is sent to, or accepted from, a host the user never authorized, through remote/redirect handling, the trampoline askpass or credential helper, the proxy resolver, or certificate-error handling.',
    'Critical. A `x-github-client://` / `github-mac://` / `github-windows://` deep link or its OAuth callback lets a page the user only visits finish sign-in, bind an attacker account or Enterprise endpoint, or drive a clone/open/repository action as the user without a fresh explicit consent step.',
    'Critical. Untrusted repository or API text - markdown, commit message, PR or issue title, branch name, avatar or image URL, diff body, Actions log - escapes the sandboxed-markdown frame or renderer escaping and reaches a privileged capability such as IPC, `shell.openExternal`, node APIs, or arbitrary navigation.',
    'Critical. Renderer-to-main IPC, webContents routing, or the origin/webRequest filters accept a sender, frame, or origin they should reject, letting untrusted embedded content invoke privileged main-process behaviour: file access, process spawn, window or menu control, or updater actions.',
    'Critical. The auto-update, Squirrel installer, or CLI-install path resolves, verifies, or writes its payload so that an unprivileged local user or an on-path attacker can substitute the executed content, giving code execution on the next launch or invocation.',
    'High. Parsing of attacker-controlled git or API output (status, diff, log, refs, submodules, trampoline commands, ANSI logs, PAC strings) yields state that misrepresents what will be committed, discarded, pushed, or checked out, causing silent loss of local work or publication of content the user did not intend to publish.',
    'High. Repository-to-account confusion in repository matching, endpoint or account selection, token-store keys, or cached user and pull-request data makes Desktop attach one account credential or private data to a repository or request belonging to a different owner or endpoint.',
    'High. Attacker-controlled repository content or API data makes Desktop read, stage, or transmit files outside the selected repository scope - into a diff, a commit, Copilot or conflict context, or an error report - disclosing local user data to the attacker.',
]

DESKTOP_ALLOWED_IMPACT_SCOPE = """Valid only: unprivileged GitHub Desktop issues where the attacker
controls a cloned/fetched repository, a GitHub API object, a link or deep link the user clicks, or a
git remote/proxy response, and the result is code execution, file write or read outside the repo,
credential/token exfiltration, unauthorized OAuth or account binding, renderer-sandbox or IPC escape,
or silent corruption of what the user commits or pushes. Reject anything needing local/physical
access, admin rights, malware already on the host, leaked credentials, unprompted unnatural user
steps, self-XSS, DoS/rate-limit-only, missing headers or hardening, dependency CVEs with no reachable
path, social engineering, and tests/mocks/docs/generated/config files."""

DESKTOP_AUDIT_PIVOTS = """Focus on broken path/symlink containment, argv-flag or shell injection into
git and spawned editors/shells, credential-to-host binding in trampoline/askpass/credential-helper,
deep-link and OAuth state handling, IPC sender and origin trust, markdown/webview sandbox limits, and
repository-to-account matching."""


def question_generator(target_file: str) -> str:
    """
    Generate security questions for one GitHub Desktop target.
    """

    prompt = f"""
    Draft 18 to 24 GitHub Desktop exploit questions for:
    {target_file}

    Use only real unprivileged entrypoints: cloned or fetched repository content, refs and remotes,
    GitHub API objects (PRs, issues, checks, avatars, Actions logs), clicked links and
    `x-github-client://` deep links, git remote/HTTP/proxy responses, and untrusted content rendered
    in the renderer.

    {DESKTOP_ALLOWED_IMPACT_SCOPE}
    {DESKTOP_AUDIT_PIVOTS}

    Rules:
    * `File Name:` = this file.
    * `Scope:` = exactly one `target_scopes` item.
    * Use repo context only.
    * No admin/local-access/malware-on-host/leaked-credential/off-repo assumptions.
    * Ignore tests, mocks, docs, generated files, config files, dependencies, DoS-only, self-XSS, and best-practice-only ideas.
    * Name the exact wrong value and keep each question immediately testable.

    Return Python only.

    questions = [
    "[File: {target_file}] [Symbol: symbol_or_type] Can attacker-controlled INPUT through PUBLIC_ENTRYPOINT under REQUIRED_STATE reach TARGET_PATH and break INVARIANT, corrupting EXACT_VALUE with scoped impact SCOPE_IMPACT? Proof idea: focused repo test.",
    ]
    """
    return prompt


def audit_format(question: str) -> str:
    """
    Generate a focused GitHub Desktop exploit-question validation prompt.
    """
    return f"""# GITHUB DESKTOP REVIEW

## Submitted Question
{question}

## Scope
Only GitHub Desktop production code. Only unprivileged attacker-controlled input: repository
content, API objects, clicked links/deep links, remote responses. Reject local access, admin
rights, prior malware, leaked credentials, and excluded bounty families.

## Valid Impact
{DESKTOP_ALLOWED_IMPACT_SCOPE}

## Review Path
1. Trace the exact untrusted-input path from entrypoint to sink.
2. Compare intended vs actual path/argv/credential-host/origin/IPC-sender/render-escaping result.
3. Name the wrong value exactly.
4. Reject if existing checks already stop it.

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
    Generate a cross-project analog scan prompt for GitHub Desktop issues.
    """
    prompt = f"""# GITHUB DESKTOP ANALOG SCAN

## External Report
{report}

## Task
Use the external report only as a bug-class seed. Find a real GitHub Desktop analog from local code
only.

## Valid Impact
{DESKTOP_ALLOWED_IMPACT_SCOPE}

## Method
- Reduce the report to its broken invariant and attacker primitive.
- Keep only the strongest Desktop path with exact file/function support.
- Reject local-access/admin/prior-malware/leaked-credential/social-engineering/DoS-only assumptions.
- Name the exact corrupted value and show why existing guards do not stop the path.
- Either produce a concrete Desktop issue from local code evidence or return `#NoVulnerability found for this question.`

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
"""
    return prompt


def validation_format(report: str) -> str:
    """
    Generate a strict GitHub Desktop validation prompt for security claims.
    """
    prompt = f"""# GITHUB DESKTOP CLAIM VALIDATION

## Security Claim
{report}

## Rules
- Validate only the submitted claim against GitHub Desktop production code.
- Do not widen the claim or raise severity without evidence.
- The attacker must be unprivileged and reach the code through repository content, API objects, a clicked link or deep link, a git remote/proxy response, or untrusted rendered content.
- Reject local/physical access, admin rights, prior malware, leaked credentials, unprompted unnatural user steps, self-XSS, DoS-only, hardening-only, dependency CVEs with no reachable path, and test/mock/docs/generated/config files.
- The final impact must match one `target_scopes` item and name the exact corrupted value.

## Valid Impact
{DESKTOP_ALLOWED_IMPACT_SCOPE}

## Required Checks
1. Exact file and function references in scoped code.
2. A clear invariant tied to path containment, argument construction, credential-to-host binding, deep-link/OAuth consent, IPC or origin trust, render escaping, or repository-to-account matching.
3. A reachable exploit path from attacker input to code execution, file write/read outside scope, credential exposure, unauthorized account action, sandbox/IPC escape, or corrupted commit/push state.
4. Existing guards reviewed and shown insufficient.
5. Exact wrong value named: written path, spawned argv, target host, token or passphrase, IPC sender or origin, rendered node, staged file set, or account/repository binding.
6. A reproducible proof path via a focused unit, integration, or end-to-end test in this repo.

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
[Concrete allowed repository impact and severity rationale]

## Likelihood Explanation
[Attacker capability, required conditions, feasibility, repeatability]

## Recommendation
[Specific fix guidance]

## Proof of Concept
[Minimal reproducible steps or test plan]

If invalid, output exactly:
#NoVulnerability found for this question.

Output only one of the two outcomes above. No extra text.
"""
    return prompt
