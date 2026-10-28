import asyncio
import logging
from collections import namedtuple
from contextlib import AsyncExitStack

import gidgethub
import httpx
import stamina
from django.db.models import Q
from django_github_app.github import AsyncGitHubAPI
from django_github_app.models import Repository
from django_tasks import task

from cla.comments import post_or_update_fail_comment, update_success_comment
from cla.models import (
    Agreement,
    PendingSignature,
    PreApprovedAccount,
    RepositoryMapping,
    Signature,
)
from cla.status import fail_status_check, succeed_status_check
from cla.utils import normalize_email

Author = namedtuple("Author", "login id node_id email")

# Committer used by GitHub for commits created through its web interface
GITHUB_WEB_FLOW_LOGIN = "web-flow"
GITHUB_WEB_FLOW_EMAIL = "noreply@github.com"


def _commit_person(commit, role):
    """Build an Author for the commit's `role` ("author" or "committer")."""
    if commit[role]:
        return Author(
            commit[role]["login"],
            commit[role]["id"],
            commit[role]["node_id"],
            commit["commit"][role]["email"],
        )
    if commit["commit"][role]:
        return Author(
            commit["commit"][role]["name"],
            None,
            None,
            commit["commit"][role]["email"],
        )
    return None


def _is_same_person(author, committer):
    if author.id is not None and committer.id is not None:
        return author.id == committer.id
    return (author.email or "").lower() == (committer.email or "").lower()


def _is_github_web_flow(committer):
    return (
        committer.login == GITHUB_WEB_FLOW_LOGIN
        or (committer.email or "").lower() == GITHUB_WEB_FLOW_EMAIL
    )


def commit_signer(commit):
    """Return the person who must have signed the CLA for this commit.

    When a commit is committed by someone other than its author (e.g. a
    contributor porting someone else's work), the committer is the one
    contributing it, so the committer's CLA is checked. Otherwise, the
    author's CLA is checked.
    """
    author = _commit_person(commit, "author")
    committer = _commit_person(commit, "committer")
    if (
        committer is None
        or _is_github_web_flow(committer)
        or (author is not None and _is_same_person(author, committer))
    ):
        return author
    return committer


@task
@stamina.retry(on=gidgethub.GitHubException)
@stamina.retry(on=httpx.HTTPError)
async def check_pull_request(
    github_user_id=None,
    pull_request_id=None,
    pull_request_number=None,
    pull_request_head_sha=None,
    pull_request_url=None,
    target_repository_id=None,
    target_repository_full_name=None,
    react=False,
    immediate=True,
    gh=None,
):
    if not immediate:
        await asyncio.sleep(5.0)

    if any(
        [
            not x
            for x in [
                github_user_id,
                pull_request_id,
                pull_request_number,
                pull_request_head_sha,
                pull_request_url,
                target_repository_id,
                target_repository_full_name,
            ]
        ]
    ):
        # TODO: HTTP 400
        raise Exception()

    # Find mapped Agreement for the repository, or default
    repository_mapping = (
        await RepositoryMapping.objects.select_related("agreement")
        .filter(github_repository__repository_id=target_repository_id)
        .afirst()
    )
    if repository_mapping is None:
        agreement = await Agreement.objects.filter(default=True).afirst()
    else:
        agreement = repository_mapping.agreement

    if agreement is None:
        logging.info(f"No CLA configured for {target_repository_full_name}")
        return f"No CLA configured for {target_repository_full_name}"

    logging.info(
        f"Checking {target_repository_full_name} #{pull_request_number} - " f"{pull_request_url}"
    )

    async with AsyncExitStack() as stack:
        if gh is None:
            _repository = await Repository.objects.select_related("installation").aget(
                repository_id=target_repository_id,
            )
            gh = await stack.enter_async_context(
                AsyncGitHubAPI("clabot", installation=_repository.installation)
            )

        # Collect the person responsible for each commit
        # TODO: Should we consider "Co-authored-by"???
        authors = set()
        async for commit in gh.getiter(
            f"/repos/{target_repository_full_name}/pulls/{pull_request_number}/commits"
        ):
            author = commit_signer(commit)
            if author:
                authors.add(author)

        # Find and remove any PreApprovedAccounts, they do not need to sign
        pre_approved_accounts = set()
        for author in authors:
            pre_approved_account = await PreApprovedAccount.objects.filter(
                Q(github_id=author.id)
                | Q(github_node_id=author.node_id)
                | Q(github_login=author.login)
                | Q(email_address__iexact=author.email)
            ).afirst()
            if pre_approved_account:
                pre_approved_accounts.add(author)

        authors = authors - pre_approved_accounts

        logging.info(f"Found {len(authors)} authors for this PR")

        needs_signing = set()
        # Check for the correct Agreement Signature for each remaining author
        for author in authors:
            signature = (
                await Signature.objects.filter(Q(normalized_email=normalize_email(author.email)))
                .filter(Q(agreement=agreement) | Q(agreement__in=agreement.compatible.all()))
                .afirst()
            )

            if signature is None:
                await PendingSignature.objects.aupdate_or_create(
                    agreement=agreement,
                    github_repository_id=target_repository_id,
                    email_address=author.email,
                    pull_number=pull_request_number,
                )
                needs_signing.add(author)
            elif signature.github_id is None or signature.github_node_id is None:
                await Signature.objects.filter(
                    agreement=agreement, email_address=author.email
                ).aupdate(github_id=author.id, github_node_id=author.node_id)

        logging.info(f"Found {len(needs_signing)} authors without a CLA:")
        for author in needs_signing:
            logging.info(f"  - {author}")

        if react:
            # Set Commit Status Check
            if needs_signing:
                await fail_status_check(gh, target_repository_full_name, pull_request_head_sha)
            else:
                await succeed_status_check(gh, target_repository_full_name, pull_request_head_sha)

            # Send/Update comments
            if needs_signing:
                email_addresses = [author.email for author in needs_signing]
                await post_or_update_fail_comment(
                    gh, email_addresses, target_repository_full_name, pull_request_number
                )
            else:
                await update_success_comment(gh, target_repository_full_name, pull_request_number)

    return list(needs_signing)
